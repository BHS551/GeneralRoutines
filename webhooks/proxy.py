"""
Webhook proxy: recibe el POST del MSP y reenvía al endpoint /fire de la rutina
con los headers requeridos por Anthropic.

Usar cuando el MSP no permite personalizar headers en sus webhooks.

Despliegue sugerido: Google Cloud Run, AWS Lambda (con API Gateway), o Cloudflare Workers.

Dependencias: fastapi, uvicorn, httpx, python-dotenv
"""

import hashlib
import hmac
import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from utils.logger import get_logger

app = FastAPI(title="MSP Webhook Proxy")
log = get_logger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST"],
    allow_headers=["*"],
)


@app.post("/webhook")
async def receive_msp_webhook(request: Request) -> Response:
    """
    Endpoint que recibe el webhook del MSP, extrae el texto de la alerta
    y dispara la rutina con los headers correctos.
    """
    body = await request.body()

    _verify_msp_auth(request, body)

    payload = await request.json()
    alert_text = _extract_alert_text(payload)

    if not alert_text:
        log.warning("Webhook recibido sin texto de alerta interpretable", extra={"payload": payload})
        return Response(content='{"status":"ignored","reason":"no_alert_text"}', media_type="application/json")

    log.info("Alerta recibida desde MSP", extra={"text_preview": alert_text[:120]})

    try:
        fire_response = await _fire_routine(alert_text)
    except RuntimeError as exc:
        log.error("No se pudo disparar la rutina", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail="Routine fire endpoint unavailable") from exc

    log.info(
        "Rutina disparada",
        extra={
            "session_id": fire_response.get("session_id"),
            "session_url": fire_response.get("session_url"),
        },
    )

    return Response(
        content=str(fire_response).encode(),
        media_type="application/json",
        status_code=202,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ts": int(time.time())}


def _verify_msp_auth(request: Request, body: bytes) -> None:
    """
    Autentica el webhook entrante. Soporta dos esquemas, ambos opcionales:

      - Token estático (MSP_WEBHOOK_TOKEN): para MSPs que solo permiten headers
        fijos, como Datadog. Se lee de 'Authorization: Bearer <token>' o de
        'X-Webhook-Token: <token>'.
      - Firma HMAC (MSP_WEBHOOK_SECRET): para MSPs que firman el payload con
        HMAC-SHA256, enviado en 'X-MSP-Signature: sha256=<hex>'.

    Si no se configura ninguno, se omite la validación (solo desarrollo).
    Si se configura al menos uno, la petición debe satisfacer alguno de ellos.
    """
    token = os.environ.get("MSP_WEBHOOK_TOKEN")
    secret = os.environ.get("MSP_WEBHOOK_SECRET")

    if not token and not secret:
        return

    if token and _check_static_token(request, token):
        return
    if secret and _check_hmac_signature(request, body, secret):
        return

    log.warning("Autenticación de webhook fallida")
    raise HTTPException(status_code=401, detail="Invalid webhook authentication")


def _check_static_token(request: Request, expected: str) -> bool:
    """Valida un token compartido estático en 'Authorization' o 'X-Webhook-Token'."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        presented = auth[len("Bearer "):]
        if hmac.compare_digest(presented, expected):
            return True

    presented = request.headers.get("X-Webhook-Token", "")
    return bool(presented) and hmac.compare_digest(presented, expected)


def _check_hmac_signature(request: Request, body: bytes, secret: str) -> bool:
    """Valida la firma HMAC-SHA256 del payload en 'X-MSP-Signature'."""
    signature_header = request.headers.get("X-MSP-Signature", "")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature_header) and hmac.compare_digest(expected, signature_header)


def _extract_alert_text(payload: dict) -> str:
    """
    Extrae el texto de la alerta del payload del MSP.
    Ajustar según el formato real del MSP (Zabbix, Datadog, PagerDuty, etc.).
    """
    candidates = ["text", "message", "alert", "body", "description", "details"]
    for key in candidates:
        if key in payload and isinstance(payload[key], str):
            return payload[key]

    if "subject" in payload and "status" in payload:
        return f"{payload.get('status','')}: {payload.get('subject','')} — {payload.get('body','')}"

    return ""


async def _fire_routine(alert_text: str) -> dict:
    """
    Envía el POST al endpoint /fire de la rutina de Claude Code.
    Reintenta hasta 4 veces con backoff exponencial.
    """
    routine_id = os.environ["ROUTINE_ID"]
    token = os.environ["ROUTINE_FIRE_TOKEN"]
    url = f"https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "experimental-cc-routine-2026-04-01",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    delays = [2, 4, 8, 16]
    last_exc: Exception | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt, delay in enumerate(delays, start=1):
            try:
                response = await client.post(url, json={"text": alert_text}, headers=headers)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                log.warning(
                    "Fallo al disparar rutina",
                    extra={"attempt": attempt, "error": str(exc), "retry_in": delay},
                )
                if attempt < len(delays):
                    time.sleep(delay)

    raise RuntimeError(f"No se pudo disparar la rutina tras {len(delays)} intentos: {last_exc}")

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

    _verify_msp_signature(request, body)

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


def _verify_msp_signature(request: Request, body: bytes) -> None:
    """
    Valida la firma HMAC del webhook si MSP_WEBHOOK_SECRET está definido.
    Omitir en desarrollo; obligatorio en producción.
    """
    secret = os.environ.get("MSP_WEBHOOK_SECRET")
    if not secret:
        return

    signature_header = request.headers.get("X-MSP-Signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(f"sha256={expected}", signature_header):
        log.warning("Firma de webhook inválida")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


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

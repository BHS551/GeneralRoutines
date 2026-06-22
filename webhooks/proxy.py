"""
Webhook proxy: recibe el POST del MSP, valida su autenticidad, descarta alertas
duplicadas y dispara la rutina de análisis en el proveedor configurado.

Proveedores soportados (env var PROVIDER):
  - "anthropic" (default): dispara una Claude Code Routine vía /fire (fire-and-forget).
  - "cursor": lanza un Cloud Agent vía POST /v1/agents y hace polling del run
    (la API v1 de Cursor aún no entrega callbacks por webhook).

Usar cuando el MSP no permite personalizar headers en sus webhooks.

Despliegue sugerido: Google Cloud Run, AWS Lambda (con API Gateway), o Cloudflare Workers.

Dependencias: fastapi, uvicorn, httpx, python-dotenv
"""

import asyncio
import hashlib
import hmac
import os
import time

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
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

# Deduplicación en memoria: huella de alerta → timestamp de la última vez vista.
# NOTA: es por-proceso. Con múltiples workers/instancias usa un store compartido
# (Redis) para que la dedup sea global.
_recent_alerts: dict[str, float] = {}


@app.post("/webhook")
async def receive_msp_webhook(request: Request, background: BackgroundTasks) -> Response:
    """
    Recibe el webhook del MSP, valida auth, descarta duplicados y dispara la rutina.
    """
    body = await request.body()

    _verify_msp_auth(request, body)

    payload = await request.json()
    alert_text = _extract_alert_text(payload)

    if not alert_text:
        log.warning("Webhook recibido sin texto de alerta interpretable", extra={"payload": payload})
        return Response(content='{"status":"ignored","reason":"no_alert_text"}', media_type="application/json")

    fingerprint = _alert_fingerprint(alert_text)
    if _is_duplicate(fingerprint):
        log.info("Alerta duplicada descartada (dedup)", extra={"fingerprint": fingerprint})
        return Response(
            content=f'{{"status":"deduplicated","fingerprint":"{fingerprint}"}}',
            media_type="application/json",
        )

    log.info("Alerta recibida desde MSP", extra={"fingerprint": fingerprint, "text_preview": alert_text[:120]})

    try:
        fire_response = await _fire_routine(alert_text)
    except RuntimeError as exc:
        # Si falló el disparo, olvidar la huella para que un reintento legítimo pase.
        _forget(fingerprint)
        log.error("No se pudo disparar la rutina", extra={"error": str(exc)})
        raise HTTPException(status_code=502, detail="Routine fire endpoint unavailable") from exc

    # La API v1 de Cursor no entrega callbacks: hacemos polling del run en background
    # para no bloquear la respuesta al MSP.
    if fire_response.get("provider") == "cursor" and fire_response.get("agent_id") and fire_response.get("run_id"):
        background.add_task(_poll_cursor_run, fire_response["agent_id"], fire_response["run_id"])

    log.info(
        "Rutina disparada",
        extra={
            "provider": fire_response.get("provider"),
            "session_id": fire_response.get("session_id"),
            "session_url": fire_response.get("session_url"),
            "agent_id": fire_response.get("agent_id"),
            "run_id": fire_response.get("run_id"),
        },
    )

    return Response(
        content=str(fire_response).encode(),
        media_type="application/json",
        status_code=202,
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ts": int(time.time()), "provider": os.environ.get("PROVIDER", "anthropic")}


# --------------------------------------------------------------------------- #
# Autenticación del webhook entrante                                          #
# --------------------------------------------------------------------------- #

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


# --------------------------------------------------------------------------- #
# Extracción y deduplicación de la alerta                                     #
# --------------------------------------------------------------------------- #

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


def _alert_fingerprint(text: str) -> str:
    """Huella estable de la alerta (texto normalizado) para deduplicar."""
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def _is_duplicate(fingerprint: str) -> bool:
    """
    True si esta huella se vio dentro de la ventana DEDUP_TTL_SECONDS (default 300s).
    Marca la huella como vista al primer encuentro. Evita crear tickets repetidos
    cuando el MSP dispara la misma alerta varias veces seguidas.
    """
    ttl = int(os.environ.get("DEDUP_TTL_SECONDS", "300"))
    now = time.time()

    for stale in [fp for fp, ts in _recent_alerts.items() if now - ts > ttl]:
        _recent_alerts.pop(stale, None)

    if fingerprint in _recent_alerts:
        return True

    _recent_alerts[fingerprint] = now
    return False


def _forget(fingerprint: str) -> None:
    """Olvida una huella (p. ej. tras un fallo de disparo) para permitir reintentos."""
    _recent_alerts.pop(fingerprint, None)


# --------------------------------------------------------------------------- #
# Disparo de la rutina (dispatch por proveedor)                               #
# --------------------------------------------------------------------------- #

async def _fire_routine(alert_text: str) -> dict:
    """Despacha el disparo según PROVIDER."""
    provider = os.environ.get("PROVIDER", "anthropic").lower()
    if provider == "cursor":
        return await _fire_cursor(alert_text)
    return await _fire_anthropic(alert_text)


async def _fire_anthropic(alert_text: str) -> dict:
    """Dispara una Claude Code Routine vía /fire (fire-and-forget)."""
    routine_id = os.environ["ROUTINE_ID"]
    token = os.environ["ROUTINE_FIRE_TOKEN"]
    url = f"https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire"

    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "experimental-cc-routine-2026-04-01",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }

    data = await _post_with_retry(url, {"text": alert_text}, headers, label="anthropic")
    data["provider"] = "anthropic"
    return data


async def _fire_cursor(alert_text: str) -> dict:
    """
    Lanza un Cloud Agent de Cursor vía POST /v1/agents.
    NOTA: los nombres de campos de la respuesta pueden variar; verifica contra la
    API actual de Cursor. Capturamos 'id' del agente y el id del run inicial.
    """
    base = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com")
    api_key = os.environ["CURSOR_API_KEY"]
    repo_url = os.environ["CURSOR_REPO_URL"]
    starting_ref = os.environ.get("CURSOR_STARTING_REF", "main")
    model_id = os.environ.get("CURSOR_MODEL", "composer-2")
    auto_pr = os.environ.get("CURSOR_AUTO_CREATE_PR", "true").lower() == "true"

    body = {
        "prompt": {"text": _build_cursor_prompt(alert_text)},
        "repos": [{"url": repo_url, "startingRef": starting_ref}],
        "model": {"id": model_id},
        "autoCreatePR": auto_pr,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    data = await _post_with_retry(f"{base}/v1/agents", body, headers, label="cursor")

    agent_id = data.get("id") or data.get("agentId")
    run_id = None
    runs = data.get("runs")
    if isinstance(runs, list) and runs:
        run_id = runs[0].get("id")
    run_id = run_id or data.get("runId")

    return {"provider": "cursor", "agent_id": agent_id, "run_id": run_id, "raw": data}


def _build_cursor_prompt(alert_text: str) -> str:
    """Envuelve la alerta con la instrucción de la rutina para el agente de Cursor."""
    return (
        "Eres un agente de análisis de incidentes. Sigue las fases definidas en "
        "CLAUDE.md: comprensión de la alerta, recuperación de contexto, evaluación "
        "de impacto y accionabilidad, investigación (revisa y debuggea el código "
        "relacionado), y creación del ticket de Jira con la resolución propuesta. "
        "Si el problema no es accionable, regístralo y no crees ticket.\n\n"
        f"Alerta recibida:\n{alert_text}"
    )


async def _poll_cursor_run(agent_id: str, run_id: str) -> None:
    """
    Hace polling del run de Cursor hasta un estado terminal o agotar los intentos.
    Corre en background; solo registra estados (no bloquea la respuesta al MSP).
    """
    base = os.environ.get("CURSOR_API_BASE", "https://api.cursor.com")
    api_key = os.environ["CURSOR_API_KEY"]
    url = f"{base}/v1/agents/{agent_id}/runs/{run_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    max_polls = int(os.environ.get("CURSOR_MAX_POLLS", "60"))
    interval = int(os.environ.get("CURSOR_POLL_INTERVAL", "10"))
    terminal = {"FINISHED", "COMPLETED", "ERROR", "FAILED", "CANCELLED"}

    async with httpx.AsyncClient(timeout=30) as client:
        for poll in range(1, max_polls + 1):
            await asyncio.sleep(interval)
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                status = (data.get("status") or "").upper()
                log.info(
                    "Estado del run de Cursor",
                    extra={"agent_id": agent_id, "run_id": run_id, "status": status, "poll": poll},
                )
                if status in terminal:
                    return
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                log.warning(
                    "Fallo al consultar estado del run de Cursor",
                    extra={"agent_id": agent_id, "run_id": run_id, "error": str(exc)},
                )

    log.warning(
        "Polling del run de Cursor agotado sin estado terminal",
        extra={"agent_id": agent_id, "run_id": run_id, "max_polls": max_polls},
    )


async def _post_with_retry(url: str, json_body: dict, headers: dict, label: str) -> dict:
    """POST con reintentos y backoff exponencial (2→4→8→16s). Lanza RuntimeError al agotar."""
    delays = [2, 4, 8, 16]
    last_exc: Exception | None = None

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt, delay in enumerate(delays, start=1):
            try:
                response = await client.post(url, json=json_body, headers=headers)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                last_exc = exc
                log.warning(
                    "Fallo al disparar rutina",
                    extra={"provider": label, "attempt": attempt, "error": str(exc), "retry_in": delay},
                )
                if attempt < len(delays):
                    await asyncio.sleep(delay)

    raise RuntimeError(f"No se pudo disparar la rutina ({label}) tras {len(delays)} intentos: {last_exc}")

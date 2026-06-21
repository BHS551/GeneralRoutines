#!/usr/bin/env bash
# Simulador de MSP: actúa como la plataforma de monitoreo para validar el proxy
# de extremo a extremo SIN depender del MSP real.
#
# Soporta los dos esquemas de auth del proxy:
#   - HMAC  (MSP_WEBHOOK_SECRET): firma el payload, como un MSP que firma.
#   - Token (MSP_WEBHOOK_TOKEN):  manda 'Authorization: Bearer <token>',
#                                 como haría Datadog con un header estático.
#
# Uso:
#   MSP_WEBHOOK_SECRET=xxx ./scripts/simulate_msp.sh [URL_DEL_PROXY] [TEXTO_ALERTA]
#   MSP_WEBHOOK_TOKEN=yyy  ./scripts/simulate_msp.sh [URL_DEL_PROXY] [TEXTO_ALERTA]

set -euo pipefail

PROXY_URL="${1:-http://localhost:8080}"
ALERT_TEXT="${2:-"Alerta PROD-4521: error 500 en checkout-service al procesar pagos. NullPointerException en PaymentProcessor.java:142"}"
SECRET="${MSP_WEBHOOK_SECRET:-}"
TOKEN="${MSP_WEBHOOK_TOKEN:-}"

PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'text': sys.argv[1]}))" "$ALERT_TEXT")

AUTH_HEADERS=()
if [ -n "$TOKEN" ]; then
  AUTH_HEADERS+=(-H "Authorization: Bearer $TOKEN")
  echo "Auth: token estático (Authorization: Bearer ...) — modo Datadog"
fi
if [ -n "$SECRET" ]; then
  SIG=$(python3 -c "
import hmac, hashlib, sys
secret, body = sys.argv[1], sys.argv[2]
print('sha256=' + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest())
" "$SECRET" "$PAYLOAD")
  AUTH_HEADERS+=(-H "X-MSP-Signature: $SIG")
  echo "Auth: firma HMAC ($SIG)"
fi
if [ ${#AUTH_HEADERS[@]} -eq 0 ]; then
  echo "AVISO: ni MSP_WEBHOOK_TOKEN ni MSP_WEBHOOK_SECRET definidos — enviando sin auth (el proxy lo rechazará si exige auth)."
fi

echo "Proxy:   $PROXY_URL/webhook"
echo "Alerta:  $ALERT_TEXT"
echo ""

curl -s -w "\nHTTP %{http_code}\n" \
  -X POST "$PROXY_URL/webhook" \
  "${AUTH_HEADERS[@]}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"

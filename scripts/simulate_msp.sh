#!/usr/bin/env bash
# Simulador de MSP: actúa como la plataforma de monitoreo para validar el proxy
# de extremo a extremo SIN depender del MSP real.
#
# Firma el payload con HMAC-SHA256 exactamente como lo haría el MSP y lo envía
# al endpoint /webhook del proxy.
#
# Uso:
#   MSP_WEBHOOK_SECRET=xxx ./scripts/simulate_msp.sh [URL_DEL_PROXY] [TEXTO_ALERTA]
#
# Ejemplo:
#   MSP_WEBHOOK_SECRET=$SECRET ./scripts/simulate_msp.sh http://localhost:8080 \
#     "Alerta PROD-4521: error 500 en checkout-service"

set -euo pipefail

PROXY_URL="${1:-http://localhost:8080}"
ALERT_TEXT="${2:-"Alerta PROD-4521: error 500 en checkout-service al procesar pagos. NullPointerException en PaymentProcessor.java:142"}"
SECRET="${MSP_WEBHOOK_SECRET:-}"

PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'text': sys.argv[1]}))" "$ALERT_TEXT")

# Construir la cabecera de firma igual que el MSP (vacía si no hay secreto configurado)
SIG_HEADER=()
if [ -n "$SECRET" ]; then
  SIG=$(python3 -c "
import hmac, hashlib, sys
secret, body = sys.argv[1], sys.argv[2]
print('sha256=' + hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest())
" "$SECRET" "$PAYLOAD")
  SIG_HEADER=(-H "X-MSP-Signature: $SIG")
  echo "Firma HMAC: $SIG"
else
  echo "AVISO: MSP_WEBHOOK_SECRET no definido — enviando sin firma (el proxy lo rechazará si exige firma)."
fi

echo "Proxy:   $PROXY_URL/webhook"
echo "Alerta:  $ALERT_TEXT"
echo ""

curl -s -w "\nHTTP %{http_code}\n" \
  -X POST "$PROXY_URL/webhook" \
  "${SIG_HEADER[@]}" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD"

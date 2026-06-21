#!/usr/bin/env bash
# Script de prueba para disparar la rutina manualmente.
# Uso: ROUTINE_ID=xxx ROUTINE_FIRE_TOKEN=yyy ./scripts/fire_test.sh

set -euo pipefail

ROUTINE_ID="${ROUTINE_ID:?Debe definir ROUTINE_ID}"
ROUTINE_FIRE_TOKEN="${ROUTINE_FIRE_TOKEN:?Debe definir ROUTINE_FIRE_TOKEN}"

ALERT_TEXT="${1:-"Alerta PROD-4521: error 500 en checkout-service al procesar pagos. Stack trace: java.lang.NullPointerException at PaymentProcessor.charge(PaymentProcessor.java:142)"}"

echo "Disparando rutina: $ROUTINE_ID"
echo "Alerta: $ALERT_TEXT"
echo ""

RESPONSE=$(curl -s -X POST \
  "https://api.anthropic.com/v1/claude_code/routines/${ROUTINE_ID}/fire" \
  -H "Authorization: Bearer ${ROUTINE_FIRE_TOKEN}" \
  -H "anthropic-beta: experimental-cc-routine-2026-04-01" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"${ALERT_TEXT}\"}")

echo "Respuesta:"
echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"

SESSION_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null || echo "")
SESSION_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_url',''))" 2>/dev/null || echo "")

if [ -n "$SESSION_ID" ]; then
  echo ""
  echo "Sesión creada: $SESSION_ID"
  echo "URL de sesión: $SESSION_URL"
fi

# GeneralRoutines

Automatización de Alertas de Monitoreo a Tickets de Jira mediante Claude Code Routines.

## Descripción del sistema

Este sistema detecta automáticamente errores reportados por la plataforma de monitoreo del MSP, realiza un análisis inteligente del problema (evaluación de impacto, recuperación de contexto desde documentación interna y repositorios de código), determina si el problema es accionable, investiga qué habría que hacer, y finalmente crea un ticket de Jira en una épica específica, asignado al ingeniero correspondiente.

La pieza central del razonamiento es **Claude Code Routines**, que se ejecuta en la infraestructura de Anthropic en la nube. Esto significa que el sistema funciona de forma continua, sin depender de que ninguna máquina local esté encendida.

## Documento de arquitectura

El documento de diseño técnico completo se encuentra en [`docs/arquitectura1.docx`](docs/arquitectura1.docx).

## Estructura del repositorio

```
GeneralRoutines/
├── docs/
│   └── arquitectura1.docx          # Documento de diseño técnico v1
├── CLAUDE.md                        # Prompt principal de la rutina (agente)
├── requirements.txt                 # Dependencias Python del proxy
├── config/
│   ├── assignment_rules.json        # Mapeo servicio → ingeniero
│   └── routine_config.json          # Configuración general de la rutina
├── integrations/
│   ├── jira_client.py               # Cliente para la API de Jira (con ADF builder)
│   ├── confluence_client.py         # Cliente para la API de Confluence
│   ├── github_client.py             # Cliente para la API de GitHub
│   └── google_docs_client.py        # Cliente para la API de Google Docs
├── webhooks/
│   ├── proxy.py                     # Proxy FastAPI: MSP webhook → /fire
│   └── Dockerfile                   # Imagen para desplegar el proxy
├── utils/
│   ├── logger.py                    # Logger JSON estructurado por fases
│   ├── error_handler.py             # Reintentos, ToolFailureTracker, revisión humana
│   └── monitor.py                   # Métricas de tokens y salud por sesión
└── scripts/
    └── fire_test.sh                 # Script de prueba manual (curl)
```

## Flujo de extremo a extremo

```
MSP Monitoring → Webhook → /fire endpoint → Claude Code Routine (Cloud)
                                                      │
                              ┌───────────────────────┼──────────────────────┐
                              ▼                       ▼                      ▼
                         Confluence             Google Docs              GitHub API
                         (runbooks)           (documentación)           (código)
                              │                       │                      │
                              └───────────────────────┼──────────────────────┘
                                                      ▼
                                          Evaluación de impacto
                                          ¿Es accionable?
                                                      │
                              ┌───────────────────────┴──────────────────────┐
                              ▼                                               ▼
                    SÍ → Crear ticket Jira                       NO → Registrar decisión
                         (épica + ingeniero)
```

## Cómo disparar la rutina

**Directamente (prueba):**
```bash
# Requiere ROUTINE_ID y ROUTINE_FIRE_TOKEN en el entorno
./scripts/fire_test.sh "Alerta PROD-4521: error 500 en checkout-service"
```

**Via proxy en producción:**
```bash
# El MSP apunta su webhook a https://<tu-dominio>/webhook
# El proxy añade los headers y dispara la rutina automáticamente
curl -X POST https://<tu-dominio>/webhook \
  -H "X-MSP-Signature: sha256=<hmac>" \
  -H "Content-Type: application/json" \
  -d '{"text": "Alerta PROD-4521: error 500 en checkout-service"}'
```

Ver [`scripts/fire_test.sh`](scripts/fire_test.sh) y [`webhooks/proxy.py`](webhooks/proxy.py) para los ejemplos completos.

## Datadog directo (sin proxy)

Para empezar sin infraestructura adicional, Datadog puede llamar directamente a la API del proveedor. El payload incluye el texto de la alerta y los headers de autenticación se configuran como "Custom headers" en la integración.

> **Sin proxy pierdes:** deduplicación en el receptor, visibilidad del estado de runs de Cursor, y pre-filtrado centralizado. Con proxy bajo tráfico alto o múltiples fuentes de alertas, la capa intermedia vale la pena.

### Opción A — Datadog → Anthropic `/fire` (recomendado para empezar)

En **Integrations → Webhooks → New Webhook**:

| Campo | Valor |
|-------|-------|
| **URL** | `https://api.anthropic.com/v1/routines/<ROUTINE_ID>/fire` |
| **Method** | `POST` |

**Custom headers** (uno por línea en la UI de Datadog):

```
Authorization: Bearer <ROUTINE_FIRE_TOKEN>
anthropic-beta: experimental-cc-routine-2026-04-01
Content-Type: application/json
```

> Datadog permite múltiples custom headers en la integración de webhooks. Los tres son necesarios: sin `anthropic-beta` el endpoint devuelve 404.

**Payload** (pestaña "Payload" en la UI):

```json
{
  "text": "$ALERT_TITLE — $EVENT_MSG",
  "service": "$ALERT_SCOPE",
  "severity": "$ALERT_PRIORITY",
  "monitor_id": "$ALERT_ID",
  "env": "$HOSTNAME"
}
```

Anthropic acepta el body completo y lo pasa como `text` al agente. El agente (CLAUDE.md) ya parsea `text` en la Fase 1.

### Opción B — Datadog → Cursor Cloud Agents

En **Integrations → Webhooks → New Webhook**:

| Campo | Valor |
|-------|-------|
| **URL** | `https://api.cursor.com/v1/agents` |
| **Method** | `POST` |

**Custom headers**:

```
Authorization: Bearer <CURSOR_API_KEY>
Content-Type: application/json
```

**Payload**:

```json
{
  "prompt": {
    "text": "Analiza esta alerta de monitoreo y sigue las fases del runbook: $ALERT_TITLE — $EVENT_MSG. Servicio: $ALERT_SCOPE. Severidad: $ALERT_PRIORITY."
  },
  "repos": [
    {
      "url": "https://github.com/<org>/<repo>",
      "ref": "main"
    }
  ],
  "model": {
    "id": "composer-2"
  }
}
```

> La API v1 de Cursor no devuelve callbacks: el run se ejecuta en background. Para ver el estado usa el dashboard de Cursor o monta el proxy con `PROVIDER=cursor` para polling automático.

### Seleccionar qué monitores disparan el webhook

No añadas `@webhook-<nombre>` a todos los monitores. En el mensaje de cada monitor de Datadog:

```
{{#is_alert}}
Alerta en {{service.name}}: {{value}} supera el umbral de {{threshold}}.
@webhook-jira-routine
{{/is_alert}}
```

Añade `@webhook-jira-routine` **solo** a los monitores que quieres que generen un ticket. Monitores informativos o de baja prioridad no deben incluirlo.

---

## Control de volumen (evitar tormentas de tickets)

El control se aplica en Datadog, antes de que se dispare cualquier llamada al proveedor.

### 1. Ventana de evaluación sostenida

En la configuración del monitor, pestaña **"Set alert conditions"**:

- **Evaluation window:** `5 minutes` o más (evita alertar por spikes de 1 minuto)
- **Alert when:** `the value is above threshold for the last X of Y data points` — requiere que el problema sea sostenido, no puntual

### 2. Umbral de fallos consecutivos ("Notify on")

En **"Advanced alert conditions"**:

```
Notify if the monitor has been in alert state for at least: 3 consecutive checks
```

Con esto, un flap de 1-2 checks no genera ticket. Solo problemas reales y persistentes pasan.

### 3. Re-notificación limitada

En la sección **"Notify your team"** del monitor:

```
Re-notify after: 4 hours
Renotify for: 1 time
```

Así, si el problema persiste, solo recibes un recordatorio, no una ráfaga de tickets adicionales.

### 4. Notification grouping

En el campo **"Group by"** del monitor:

```
Group by: service, env
```

Datadog agrupa instancias del mismo problema y manda una sola notificación por grupo, no una por cada host afectado.

### 5. Muting de dependencias

Si tienes un monitor de base de datos y monitores de servicios que dependen de ella:

- En **Monitor → Edit → Composite monitors**: crea un composite que solo alerte sobre el servicio si la DB **no** está en alert.
- O usa **Downtimes**: cuando la DB entra en alerta, crea un downtime automático sobre los monitores dependientes para suprimir el ruido.

### 6. Filtrar por entorno

Usa tags de Datadog para que el webhook solo se dispare en producción:

```
{{#is_alert}}{{#is_match "env" "production"}}
@webhook-jira-routine
{{/is_match}}{{/is_alert}}
```

Staging y dev no generan tickets.

### 7. Resumen de configuración recomendada por tipo de alerta

| Tipo de alerta | Evaluation window | Consecutive checks | Re-notify | Webhook |
|----------------|-------------------|--------------------|-----------|---------|
| Error 5xx crítico | 2 min | 2 | 2h × 1 | ✅ |
| Latencia elevada | 5 min | 3 | 4h × 1 | ✅ |
| CPU/mem alta | 10 min | 5 | 8h × 1 | ✅ |
| Disco > 80% | 15 min | 3 | 24h × 1 | ✅ |
| Info / heartbeat | — | — | — | ❌ |

---

## Proxy de webhook (opcional, para producción avanzada)

El proxy en `webhooks/proxy.py` añade sobre el modo directo:
- **Deduplicación**: alertas idénticas dentro de `DEDUP_TTL_SECONDS` (default 300s) se descartan con `200 deduplicated`.
- **Polling de Cursor**: monitoriza el estado del run hasta `FINISHED`/`ERROR` y lo registra.
- **Auth centralizada**: el secreto HMAC o el token no viajan en el payload de Datadog.
- **Pre-filtrado**: lógica custom antes de disparar (filtros de severidad, reglas adicionales).

```bash
# Desarrollo local
pip install -r requirements.txt
uvicorn webhooks.proxy:app --reload --port 8080

# Producción con Docker
docker build -f webhooks/Dockerfile -t msp-webhook-proxy .
docker run -p 8080:8080 \
  -e ROUTINE_ID=xxx \
  -e ROUTINE_FIRE_TOKEN=yyy \
  -e MSP_WEBHOOK_TOKEN=zzz \
  -e PROVIDER=anthropic \
  msp-webhook-proxy
```

Con proxy, la URL en Datadog apunta a `https://<tu-dominio>/webhook` con `Authorization: Bearer <MSP_WEBHOOK_TOKEN>`.

### Autenticación del proxy

| Esquema | Variable | Header esperado | Para |
|---------|----------|-----------------|------|
| Token estático | `MSP_WEBHOOK_TOKEN` | `Authorization: Bearer <token>` | **Datadog**, PagerDuty |
| Firma HMAC | `MSP_WEBHOOK_SECRET` | `X-MSP-Signature: sha256=<hex>` | Zabbix, integraciones custom |

Basta con satisfacer uno si se configuran ambos.

### Proveedor de ejecución (con proxy)

| `PROVIDER` | Qué hace |
|------------|----------|
| `anthropic` (default) | Dispara Claude Code Routine vía `/fire` — fire-and-forget |
| `cursor` | Lanza Cloud Agent vía `POST /v1/agents` + polling hasta `FINISHED`/`ERROR` |

## Credenciales necesarias

| Variable de entorno        | Descripción                                                  |
|----------------------------|--------------------------------------------------------------|
| `ROUTINE_ID`               | ID de la rutina en Claude Code                               |
| `ROUTINE_FIRE_TOKEN`       | Token de disparo de la rutina (se muestra solo una vez)      |
| `MSP_WEBHOOK_TOKEN`        | Token estático para auth por header (ej. Datadog)            |
| `MSP_WEBHOOK_SECRET`       | Secreto HMAC para MSPs que firman el payload                 |
| `PROVIDER`                 | `anthropic` (default) o `cursor`                            |
| `DEDUP_TTL_SECONDS`        | Ventana de dedup de alertas idénticas (default 300)         |
| `CURSOR_API_KEY`           | API key de Cursor (solo `PROVIDER=cursor`)                  |
| `CURSOR_REPO_URL`          | Repo del Cloud Agent (solo `PROVIDER=cursor`)               |
| `CONFLUENCE_API_TOKEN`     | Token de API de Confluence                                   |
| `CONFLUENCE_EMAIL`         | Email de la cuenta de servicio de Confluence                 |
| `CONFLUENCE_BASE_URL`      | URL base de la instancia de Confluence                       |
| `GOOGLE_SERVICE_ACCOUNT`   | JSON de credenciales de cuenta de servicio Google            |
| `GITHUB_TOKEN`             | Token de GitHub con permisos de lectura                      |
| `JIRA_API_TOKEN`           | Token de API de Jira                                         |
| `JIRA_EMAIL`               | Email de la cuenta de servicio de Jira                       |
| `JIRA_BASE_URL`            | URL base de la instancia de Jira                             |
| `JIRA_PROJECT_KEY`         | Clave del proyecto Jira destino                              |
| `JIRA_EPIC_KEY`            | Clave de la épica destino                                    |
| `LOG_LEVEL`                | Nivel de logging: `INFO` (default) o `DEBUG`                 |
| `CONFIDENCE_THRESHOLD`     | % mínimo de confianza para no exigir revisión humana (def. 60) |
| `WARN_TOKENS_PER_HOUR`     | Tokens/hora para alertar consumo elevado (def. 80000)        |

Nunca incrustar secretos en el prompt ni en el código. Usar variables de entorno y gestor de secretos.

## Plan de implementación por fases

| Fase | Descripción                                                         | Estado |
|------|---------------------------------------------------------------------|--------|
| A    | Camino feliz mínimo: alerta → ticket Jira simple                    | ✅      |
| B    | Añadir contexto: GitHub, Confluence, Google Docs                    | ✅      |
| C    | Inteligencia: evaluación de impacto, accionabilidad, investigación  | ✅      |
| D    | Producción: webhook real, manejo de errores, logging                | ✅      |

## Decisión de arquitectura

**Por qué Claude Code Routines (y no N8N/Zapier):** Las plataformas visuales son más rápidas para flujos determinísticos, pero este caso requiere razonamiento real (evaluar impacto, leer documentación, analizar código). Claude Code aporta ese razonamiento de forma nativa.

**Por qué llamadas directas a API (y no MCP):** MCP es valioso cuando se reutilizan integraciones entre múltiples agentes. Para esta primera versión de un solo agente, las llamadas directas reducen complejidad. Si se multiplican los agentes, migrar a MCP es el paso natural.

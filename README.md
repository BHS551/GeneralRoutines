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

## Despliegue del proxy (Fase D)

```bash
# Instalar dependencias
pip install -r requirements.txt

# Desarrollo local
uvicorn webhooks.proxy:app --reload --port 8080

# Producción con Docker
docker build -f webhooks/Dockerfile -t msp-webhook-proxy .
docker run -p 8080:8080 \
  -e ROUTINE_ID=xxx \
  -e ROUTINE_FIRE_TOKEN=yyy \
  -e MSP_WEBHOOK_SECRET=zzz \
  msp-webhook-proxy
```

## Credenciales necesarias

| Variable de entorno        | Descripción                                                  |
|----------------------------|--------------------------------------------------------------|
| `ROUTINE_ID`               | ID de la rutina en Claude Code                               |
| `ROUTINE_FIRE_TOKEN`       | Token de disparo de la rutina (se muestra solo una vez)      |
| `MSP_WEBHOOK_SECRET`       | Secreto HMAC para validar firma del MSP (producción)         |
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

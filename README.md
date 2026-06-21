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
├── config/
│   ├── assignment_rules.json        # Mapeo servicio → ingeniero
│   └── routine_config.json          # Configuración general de la rutina
├── integrations/
│   ├── jira_client.py               # Cliente para la API de Jira
│   ├── confluence_client.py         # Cliente para la API de Confluence
│   ├── github_client.py             # Cliente para la API de GitHub
│   └── google_docs_client.py        # Cliente para la API de Google Docs
└── scripts/
    └── fire_test.sh                 # Script de prueba para disparar la rutina
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

```bash
curl -X POST https://api.anthropic.com/v1/claude_code/routines/{routine_id}/fire \
  -H "Authorization: Bearer $ROUTINE_FIRE_TOKEN" \
  -H "anthropic-beta: experimental-cc-routine-2026-04-01" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{"text": "Alerta PROD-4521: error 500 en checkout-service. Stack trace adjunto..."}'
```

Ver [`scripts/fire_test.sh`](scripts/fire_test.sh) para un ejemplo completo.

## Credenciales necesarias

| Variable de entorno        | Descripción                                        |
|----------------------------|----------------------------------------------------|
| `ROUTINE_FIRE_TOKEN`       | Token de disparo de la rutina (una sola vez)       |
| `CONFLUENCE_API_TOKEN`     | Token de API de Confluence                         |
| `CONFLUENCE_EMAIL`         | Email de la cuenta de servicio de Confluence       |
| `CONFLUENCE_BASE_URL`      | URL base de la instancia de Confluence             |
| `GOOGLE_SERVICE_ACCOUNT`   | JSON de credenciales de cuenta de servicio Google  |
| `GITHUB_TOKEN`             | Token de GitHub con permisos de lectura            |
| `JIRA_API_TOKEN`           | Token de API de Jira                               |
| `JIRA_EMAIL`               | Email de la cuenta de servicio de Jira             |
| `JIRA_BASE_URL`            | URL base de la instancia de Jira                   |
| `JIRA_PROJECT_KEY`         | Clave del proyecto Jira destino                    |
| `JIRA_EPIC_KEY`            | Clave de la épica destino                          |

Nunca incrustar secretos en el prompt ni en el código. Usar variables de entorno y gestor de secretos.

## Plan de implementación por fases

| Fase | Descripción                                                         | Estado |
|------|---------------------------------------------------------------------|--------|
| A    | Camino feliz mínimo: alerta → ticket Jira simple                    | ✅      |
| B    | Añadir contexto: GitHub, Confluence, Google Docs                    | ✅      |
| C    | Inteligencia: evaluación de impacto, accionabilidad, investigación  | ✅      |
| D    | Producción: webhook real, manejo de errores, logging                | ⬜      |

## Decisión de arquitectura

**Por qué Claude Code Routines (y no N8N/Zapier):** Las plataformas visuales son más rápidas para flujos determinísticos, pero este caso requiere razonamiento real (evaluar impacto, leer documentación, analizar código). Claude Code aporta ese razonamiento de forma nativa.

**Por qué llamadas directas a API (y no MCP):** MCP es valioso cuando se reutilizan integraciones entre múltiples agentes. Para esta primera versión de un solo agente, las llamadas directas reducen complejidad. Si se multiplican los agentes, migrar a MCP es el paso natural.

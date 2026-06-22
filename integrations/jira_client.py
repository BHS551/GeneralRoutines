"""
Cliente para la API de Jira (REST API v3).
Requiere: JIRA_API_TOKEN, JIRA_EMAIL, JIRA_BASE_URL como variables de entorno.
"""

import os
import json
import requests
from requests.auth import HTTPBasicAuth


def _auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(os.environ["JIRA_EMAIL"], os.environ["JIRA_API_TOKEN"])


def _base_url() -> str:
    return os.environ["JIRA_BASE_URL"].rstrip("/")


def create_ticket(
    summary: str,
    description_adf: dict,
    priority: str,
    assignee_account_id: str | None,
    labels: list[str],
    project_key: str | None = None,
    epic_key: str | None = None,
    issue_type: str = "Bug",
) -> dict:
    """
    Crea un issue en Jira y devuelve el response JSON con la key del ticket creado.
    priority: 'Highest' | 'High' | 'Medium' | 'Low' | 'Lowest'
    description_adf: documento en formato Atlassian Document Format (ADF).
    """
    project_key = project_key or os.environ["JIRA_PROJECT_KEY"]
    epic_key = epic_key or os.environ.get("JIRA_EPIC_KEY")

    fields: dict = {
        "project": {"key": project_key},
        "issuetype": {"name": issue_type},
        "summary": summary,
        "description": description_adf,
        "priority": {"name": priority},
        "labels": labels,
    }

    if epic_key:
        fields["parent"] = {"key": epic_key}

    if assignee_account_id:
        fields["assignee"] = {"accountId": assignee_account_id}

    payload = {"fields": fields}
    url = f"{_base_url()}/rest/api/3/issue"

    response = requests.post(url, json=payload, auth=_auth(), timeout=30)
    response.raise_for_status()
    return response.json()


def build_description_adf(
    impact: str,
    root_cause: str,
    proposed_steps: list[str],
    confluence_links: list[str],
    github_links: list[str],
    confidence_pct: int,
    alert_text: str,
) -> dict:
    """Construye el cuerpo del ticket en formato ADF."""

    def paragraph(text: str) -> dict:
        return {"type": "paragraph", "content": [{"type": "text", "text": text}]}

    def heading(text: str, level: int = 3) -> dict:
        return {
            "type": "heading",
            "attrs": {"level": level},
            "content": [{"type": "text", "text": text}],
        }

    def bullet_list(items: list[str]) -> dict:
        return {
            "type": "bulletList",
            "content": [
                {
                    "type": "listItem",
                    "content": [paragraph(item)],
                }
                for item in items
            ],
        }

    content = [
        heading("Alerta original", 3),
        paragraph(alert_text),
        heading("Impacto", 3),
        paragraph(impact),
        heading("Causa raíz probable", 3),
        paragraph(root_cause),
        heading(f"Pasos propuestos (confianza: {confidence_pct}%)", 3),
        bullet_list(proposed_steps) if proposed_steps else paragraph("N/A"),
    ]

    if confluence_links:
        content.append(heading("Documentación consultada (Confluence)", 3))
        content.append(bullet_list(confluence_links))

    if github_links:
        content.append(heading("Código relacionado (GitHub)", 3))
        content.append(bullet_list(github_links))

    return {"version": 1, "type": "doc", "content": content}


def get_ticket(issue_key: str) -> dict:
    url = f"{_base_url()}/rest/api/3/issue/{issue_key}"
    response = requests.get(url, auth=_auth(), timeout=30)
    response.raise_for_status()
    return response.json()

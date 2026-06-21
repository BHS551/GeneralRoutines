"""
Cliente para la API de Confluence (Cloud REST API v2).
Requiere: CONFLUENCE_API_TOKEN, CONFLUENCE_EMAIL, CONFLUENCE_BASE_URL como variables de entorno.
"""

import os
import requests
from requests.auth import HTTPBasicAuth


def _auth() -> HTTPBasicAuth:
    return HTTPBasicAuth(os.environ["CONFLUENCE_EMAIL"], os.environ["CONFLUENCE_API_TOKEN"])


def _base_url() -> str:
    return os.environ["CONFLUENCE_BASE_URL"].rstrip("/")


def search(query: str, spaces: list[str] | None = None, limit: int = 5) -> list[dict]:
    """
    Busca páginas en Confluence usando CQL.
    Devuelve lista de dicts con title, url, excerpt y body (texto plano).
    """
    space_filter = ""
    if spaces:
        space_keys = " OR ".join(f'space.key = "{s}"' for s in spaces)
        space_filter = f" AND ({space_keys})"

    cql = f'text ~ "{query}"{space_filter} ORDER BY lastModified DESC'

    params = {
        "cql": cql,
        "limit": limit,
        "expand": "body.storage,version",
    }
    url = f"{_base_url()}/wiki/rest/api/content/search"
    response = requests.get(url, params=params, auth=_auth(), timeout=30)
    response.raise_for_status()
    data = response.json()

    results = []
    for item in data.get("results", []):
        page_id = item["id"]
        title = item["title"]
        page_url = f"{_base_url()}/wiki{item['_links']['webui']}"
        body_storage = item.get("body", {}).get("storage", {}).get("value", "")
        results.append({
            "id": page_id,
            "title": title,
            "url": page_url,
            "body": _strip_html(body_storage),
        })

    return results


def get_page_by_id(page_id: str) -> dict:
    url = f"{_base_url()}/wiki/rest/api/content/{page_id}"
    params = {"expand": "body.storage"}
    response = requests.get(url, params=params, auth=_auth(), timeout=30)
    response.raise_for_status()
    item = response.json()
    return {
        "id": item["id"],
        "title": item["title"],
        "url": f"{_base_url()}/wiki{item['_links']['webui']}",
        "body": _strip_html(item.get("body", {}).get("storage", {}).get("value", "")),
    }


def _strip_html(html: str) -> str:
    """Elimina tags HTML básicos para obtener texto plano."""
    import re
    clean = re.sub(r"<[^>]+>", " ", html)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean[:4000]

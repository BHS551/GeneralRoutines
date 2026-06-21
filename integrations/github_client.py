"""
Cliente para la API de GitHub (REST API v3).
Requiere: GITHUB_TOKEN como variable de entorno.
"""

import os
from datetime import datetime, timedelta, timezone
import requests


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


BASE = "https://api.github.com"


def search_code(query: str, org: str, max_results: int = 10) -> list[dict]:
    """
    Busca código en los repositorios de la organización.
    Devuelve lista de dicts con repo, path, url y fragmento del archivo.
    """
    params = {"q": f"{query} org:{org}", "per_page": max_results}
    response = requests.get(f"{BASE}/search/code", params=params, headers=_headers(), timeout=30)
    response.raise_for_status()
    items = response.json().get("items", [])

    results = []
    for item in items:
        results.append({
            "repo": item["repository"]["full_name"],
            "path": item["path"],
            "url": item["html_url"],
            "sha": item["sha"],
        })
    return results


def get_recent_commits(owner: str, repo: str, path: str | None = None, days: int = 7) -> list[dict]:
    """
    Obtiene commits recientes de un repositorio, opcionalmente filtrados por path.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    params: dict = {"since": since, "per_page": 20}
    if path:
        params["path"] = path

    url = f"{BASE}/repos/{owner}/{repo}/commits"
    response = requests.get(url, params=params, headers=_headers(), timeout=30)
    response.raise_for_status()

    results = []
    for commit in response.json():
        results.append({
            "sha": commit["sha"][:8],
            "message": commit["commit"]["message"].split("\n")[0],
            "author": commit["commit"]["author"]["name"],
            "date": commit["commit"]["author"]["date"],
            "url": commit["html_url"],
        })
    return results


def get_file_content(owner: str, repo: str, path: str, ref: str = "HEAD") -> str:
    """Obtiene el contenido de un archivo del repositorio (hasta 100KB)."""
    import base64
    url = f"{BASE}/repos/{owner}/{repo}/contents/{path}"
    params = {"ref": ref}
    response = requests.get(url, params=params, headers=_headers(), timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")[:4000]
    return ""


def search_issues(owner: str, repo: str, keywords: str) -> list[dict]:
    """Busca issues y PRs abiertos relacionados con las palabras clave."""
    query = f"{keywords} repo:{owner}/{repo} state:open"
    params = {"q": query, "per_page": 5}
    response = requests.get(f"{BASE}/search/issues", params=params, headers=_headers(), timeout=30)
    response.raise_for_status()

    results = []
    for item in response.json().get("items", []):
        results.append({
            "number": item["number"],
            "title": item["title"],
            "url": item["html_url"],
            "type": "PR" if "pull_request" in item else "Issue",
        })
    return results

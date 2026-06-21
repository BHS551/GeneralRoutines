"""
Cliente para Google Docs y Google Drive API.
Requiere: GOOGLE_SERVICE_ACCOUNT como variable de entorno (JSON de cuenta de servicio).
"""

import json
import os


def _get_credentials():
    """Construye credenciales de cuenta de servicio desde la variable de entorno."""
    from google.oauth2 import service_account

    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT"]
    sa_info = json.loads(sa_json)
    scopes = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/documents.readonly",
    ]
    return service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)


def search(query: str, drive_id: str | None = None, max_results: int = 5) -> list[dict]:
    """
    Busca documentos de Google Docs relacionados con la query.
    Devuelve lista de dicts con title, url y texto extraído.
    """
    from googleapiclient.discovery import build

    creds = _get_credentials()
    drive_service = build("drive", "v3", credentials=creds)

    q = f"mimeType='application/vnd.google-apps.document' and fullText contains '{query}'"
    params: dict = {
        "q": q,
        "pageSize": max_results,
        "fields": "files(id, name, webViewLink)",
    }
    if drive_id:
        params["driveId"] = drive_id
        params["includeItemsFromAllDrives"] = True
        params["supportsAllDrives"] = True
        params["corpora"] = "drive"

    results_data = drive_service.files().list(**params).execute()
    files = results_data.get("files", [])

    results = []
    for file in files:
        text = get_document_text(file["id"])
        results.append({
            "id": file["id"],
            "title": file["name"],
            "url": file["webViewLink"],
            "text": text,
        })
    return results


def get_document_text(document_id: str) -> str:
    """Obtiene el texto plano de un Google Doc."""
    from googleapiclient.discovery import build

    creds = _get_credentials()
    docs_service = build("docs", "v1", credentials=creds)
    doc = docs_service.documents().get(documentId=document_id).execute()

    text_parts = []
    for element in doc.get("body", {}).get("content", []):
        paragraph = element.get("paragraph")
        if paragraph:
            for elem in paragraph.get("elements", []):
                text_run = elem.get("textRun")
                if text_run:
                    text_parts.append(text_run.get("content", ""))

    return "".join(text_parts)[:4000]

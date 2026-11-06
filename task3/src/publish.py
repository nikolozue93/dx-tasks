"""Idempotent Google Drive CSV + native Google Sheets publication.

Uses user OAuth: created artifacts belong to the signed-in Drive account.
Requires Google Drive and Google Sheets APIs enabled in Google Cloud.
"""
from __future__ import annotations

import csv
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_credentials(credentials_file: Path, token_file: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = Credentials.from_authorized_user_file(str(token_file), SCOPES) if token_file.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        token_file.chmod(0o600)
    return creds


def _existing(drive, name: str, mime: str, parent_id: str) -> str | None:
    q = f"name = '{name.replace(chr(39), chr(92) + chr(39))}' and '{parent_id}' in parents and mimeType = '{mime}' and trashed = false"
    result = drive.files().list(q=q, fields="files(id,name)", page_size=10).execute()
    files = result.get("files", [])
    return files[0]["id"] if files else None


def publish(csv_path: Path, year: int, oauth_json: Path, token_json: Path, folder_name="Trading Holidays") -> dict:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = get_credentials(oauth_json, token_json)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)

    parent = _existing(drive, folder_name, "application/vnd.google-apps.folder", "root")
    if parent is None:
        parent = drive.files().create(body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()["id"]

    csv_name = csv_path.name
    csv_id = _existing(drive, csv_name, "text/csv", parent)
    media = MediaFileUpload(str(csv_path), mimetype="text/csv", resumable=False)
    if csv_id:
        drive.files().update(fileId=csv_id, media_body=media).execute()
    else:
        csv_id = drive.files().create(
            body={"name": csv_name, "parents": [parent], "mimeType": "text/csv"},
            media_body=media, fields="id",
        ).execute()["id"]

    sheet_name = f"Trading Holidays {year}"
    sheet_id = _existing(drive, sheet_name, "application/vnd.google-apps.spreadsheet", parent)
    if not sheet_id:
        sheet_id = drive.files().create(
            body={"name": sheet_name, "parents": [parent], "mimeType": "application/vnd.google-apps.spreadsheet"},
            fields="id",
        ).execute()["id"]

    with csv_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.reader(file))
    sheets.spreadsheets().values().clear(spreadsheetId=sheet_id, range="A:Z", body={}).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="A1", valueInputOption="RAW", body={"values": rows}
    ).execute()
    # Bold, frozen headers and readable columns.
    spreadsheet = sheets.spreadsheets().get(spreadsheetId=sheet_id, fields="sheets(properties(sheetId,title))").execute()
    tab_id = spreadsheet["sheets"][0]["properties"]["sheetId"]
    sheets.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body={"requests": [
        {"updateSheetProperties": {"properties": {"sheetId": tab_id, "gridProperties": {"frozenRowCount": 1}}, "fields": "gridProperties.frozenRowCount"}},
        {"repeatCell": {"range": {"sheetId": tab_id, "startRowIndex": 0, "endRowIndex": 1}, "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.88, "green": 0.92, "blue": 0.98}}}, "fields": "userEnteredFormat"}},
        {"autoResizeDimensions": {"dimensions": {"sheetId": tab_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": min(len(rows[0]), 11)}}},
    ]}).execute()

    return {
        "csv_url": f"https://drive.google.com/file/d/{csv_id}/view",
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
    }

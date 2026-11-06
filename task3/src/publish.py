"""Publish the validated CSV to Google Drive and a native Google Sheet.

When SHEET_ID and CSV_FILE_ID are set in .env, update those two existing files.
Otherwise, discover/create files by name in the Trading Holidays folder.
Authentication uses Google user OAuth, not a service account.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

from dotenv import load_dotenv

SCOPES = [
    # Full Drive access is needed to update existing files created outside this app.
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_credentials(credentials_file: Path, token_file: Path):
    """Authorize the signed-in Google account and cache its access token."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file))

    # Tokens created with drive.file cannot update files created by other apps.
    if credentials and not credentials.has_scopes(SCOPES):
        credentials = None

    if credentials and not credentials.valid and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            credentials = None

    if not credentials or not credentials.valid:
        if not credentials_file.exists():
            raise FileNotFoundError(
                f"Missing OAuth Desktop client file: {credentials_file}. "
                "Download it from your Google Cloud project as credentials.json."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
        credentials = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(credentials.to_json(), encoding="utf-8")
    token_file.chmod(0o600)
    return credentials


def read_target_ids() -> tuple[str | None, str | None]:
    """Use explicit destination IDs when provided; require both or neither."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    sheet_id = os.getenv("SHEET_ID", "").strip() or None
    csv_file_id = os.getenv("CSV_FILE_ID", "").strip() or None

    if bool(sheet_id) != bool(csv_file_id):
        raise ValueError("Set both SHEET_ID and CSV_FILE_ID in .env, or neither.")
    return sheet_id, csv_file_id


def _find_in_folder(drive, name: str, mime_type: str, folder_id: str) -> str | None:
    """Find an existing Drive file in one folder using an exact name and MIME type."""
    escaped_name = name.replace("\\", "\\\\").replace("'", "\\'")
    query = (
        f"name = '{escaped_name}' and '{folder_id}' in parents "
        f"and mimeType = '{mime_type}' and trashed = false"
    )
    response = drive.files().list(q=query, fields="files(id,name)", page_size=100).execute()
    matches = response.get("files", [])

    if len(matches) > 1:
        raise RuntimeError(f"Multiple files named {name!r} in the folder; set IDs in .env.")
    return matches[0]["id"] if matches else None


def _ensure_target(drive, file_id: str, expected_mime_type: str) -> None:
    """Refuse to overwrite a file of a different type."""
    metadata = drive.files().get(
        fileId=file_id,
        fields="id,name,mimeType,trashed",
    ).execute()

    if metadata.get("trashed"):
        raise ValueError(f"Target file is in the trash: {metadata.get('name', file_id)}")
    if metadata.get("mimeType") != expected_mime_type:
        raise ValueError(
            f"Wrong target type for {metadata.get('name', file_id)!r}: "
            f"expected {expected_mime_type}, got {metadata.get('mimeType')}"
        )


def _get_or_create_folder(drive, folder_name: str) -> str:
    folder_id = _find_in_folder(
        drive, folder_name, "application/vnd.google-apps.folder", "root"
    )
    if folder_id:
        return folder_id
    response = drive.files().create(
        body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return response["id"]


def _write_csv(drive, path: Path, file_id: str | None, folder_id: str | None) -> str:
    from googleapiclient.http import MediaFileUpload

    media = MediaFileUpload(str(path), mimetype="text/csv", resumable=False)
    if file_id:
        drive.files().update(fileId=file_id, media_body=media).execute()
        return file_id

    created = drive.files().create(
        body={"name": path.name, "parents": [folder_id], "mimeType": "text/csv"},
        media_body=media,
        fields="id",
    ).execute()
    return created["id"]


def _write_sheet(sheets, spreadsheet_id: str, csv_path: Path) -> None:
    with csv_path.open(encoding="utf-8", newline="") as source:
        rows = list(csv.reader(source))
    if not rows or not rows[0]:
        raise ValueError("Cannot publish an empty CSV")

    spreadsheet = sheets.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    first_sheet = spreadsheet["sheets"][0]["properties"]
    sheet_id = first_sheet["sheetId"]
    sheet_title = first_sheet["title"]
    escaped_title = sheet_title.replace("'", "''")
    range_prefix = f"'{escaped_title}'!"

    sheets.spreadsheets().values().clear(
        spreadsheetId=spreadsheet_id,
        range=range_prefix + "A:Z",
        body={},
    ).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=range_prefix + "A1",
        valueInputOption="RAW",
        body={"values": rows},
    ).execute()

    requests = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                        "backgroundColor": {"red": 0.94, "green": 0.95, "blue": 0.97},
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "autoResizeDimensions": {
                "dimensions": {
                    "sheetId": sheet_id,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": len(rows[0]),
                }
            }
        },
    ]
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()

    # Verify row count after writing. A successful HTTP status alone is insufficient.
    readback = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_prefix + f"A1:K{len(rows)}",
    ).execute().get("values", [])
    if len(readback) != len(rows) or readback[0] != rows[0]:
        raise RuntimeError(
            f"Sheet verification failed: expected {len(rows)} rows, got {len(readback)}"
        )


def publish(
    csv_path: Path,
    year: int,
    oauth_json: Path,
    token_json: Path,
    folder_name: str = "Trading Holidays",
) -> dict[str, str]:
    """Publish one CSV and one native Sheet; return their permanent URLs."""
    from googleapiclient.discovery import build

    sheet_id, csv_file_id = read_target_ids()
    credentials = get_credentials(oauth_json, token_json)
    drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
    sheets = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    folder_id = None
    if sheet_id is not None:
        _ensure_target(drive, sheet_id, "application/vnd.google-apps.spreadsheet")
        _ensure_target(drive, csv_file_id, "text/csv")
    else:
        folder_id = _get_or_create_folder(drive, folder_name)
        csv_file_id = _find_in_folder(drive, csv_path.name, "text/csv", folder_id)
        sheet_name = f"Trading Holidays {year}"
        sheet_id = _find_in_folder(
            drive, sheet_name, "application/vnd.google-apps.spreadsheet", folder_id
        )
        if sheet_id is None:
            sheet_id = drive.files().create(
                body={
                    "name": sheet_name,
                    "parents": [folder_id],
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                },
                fields="id",
            ).execute()["id"]

    # Write Sheet first; if it fails, report the error instead of claiming success.
    _write_sheet(sheets, sheet_id, csv_path)
    csv_file_id = _write_csv(drive, csv_path, csv_file_id, folder_id)

    return {
        "csv_url": f"https://drive.google.com/file/d/{csv_file_id}/view",
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit",
    }

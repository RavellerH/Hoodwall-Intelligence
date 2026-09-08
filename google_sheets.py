"""Google Sheets / Drive helper functions.

All ingestion and enrichment scripts read/write through this module so the
service-account credentials and API clients are only wired up in one place.
"""
from io import BytesIO

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_sheets_service = None
_drive_service = None


def _credentials():
    return service_account.Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_KEY, scopes=SCOPES
    )


def get_sheets_service():
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = build("sheets", "v4", credentials=_credentials())
    return _sheets_service


def get_drive_service():
    global _drive_service
    if _drive_service is None:
        _drive_service = build("drive", "v3", credentials=_credentials())
    return _drive_service


def append_row(spreadsheet_id, sheet_name, values):
    append_rows(spreadsheet_id, sheet_name, [values])


def append_rows(spreadsheet_id, sheet_name, rows):
    """Append multiple rows in a single API call (stays under Sheets API rate limits)."""
    if not rows:
        return
    service = get_sheets_service()
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:Z",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()


def get_all_rows(spreadsheet_id, sheet_name):
    service = get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=f"{sheet_name}!A:Z")
        .execute()
    )
    return result.get("values", [])


def get_existing_addresses(spreadsheet_id, sheet_name, column_index=0):
    """Values already present in a column (header row skipped) - used to dedupe appends."""
    rows = get_all_rows(spreadsheet_id, sheet_name)
    if len(rows) <= 1:
        return set()
    return {row[column_index] for row in rows[1:] if len(row) > column_index}


def upload_csv_to_drive(folder_id, filename, csv_content):
    service = get_drive_service()

    file_metadata = {"name": filename, "parents": [folder_id]}
    media = MediaIoBaseUpload(
        BytesIO(csv_content.encode("utf-8")), mimetype="text/csv", resumable=True
    )

    return (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id, webViewLink")
        .execute()
    )

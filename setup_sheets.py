"""One-time helper: creates the required tabs (with header rows) in the
target Google Sheet if they don't already exist.

Run this once after creating the spreadsheet and sharing it with the
service account's email address (Editor access).
"""
import config
from google_sheets import get_sheets_service

SHEET_SCHEMAS = {
    "candidates": ["address", "first_seen_at", "source", "source_url", "status", "last_updated"],
    "wallets": [
        "address", "address_type", "first_seen_at", "last_seen_at",
        "first_funding_source", "discovery_sources", "status", "smart_score", "tier",
    ],
    "wallets_updated": ["address", "smart_score", "tier", "labels", "evidence", "updated_at"],
    "events": [
        "tx_hash", "log_index", "wallet_address", "event_time", "event_type",
        "token_address", "token_symbol", "amount_raw", "amount_usd",
        "counterparty", "protocol", "verified", "source",
    ],
    "labels": ["wallet_address", "label", "confidence", "evidence", "assigned_at", "model_version"],
    "telegram_messages": [
        "message_id", "source_chat_id", "timestamp", "text",
        "addresses", "tx_hashes", "forwarded", "reply_to_id",
    ],
    "ocr_results": ["timestamp", "screenshot_path", "ocr_text", "addresses_found", "confidence"],
}


def _existing_sheet_titles(service, spreadsheet_id):
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    return {s["properties"]["title"] for s in meta.get("sheets", [])}


def setup_sheets():
    spreadsheet_id = config.require("GOOGLE_SHEET_ID")
    service = get_sheets_service()
    existing = _existing_sheet_titles(service, spreadsheet_id)

    additions = [
        {"addSheet": {"properties": {"title": name}}}
        for name in SHEET_SCHEMAS
        if name not in existing
    ]
    if additions:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id, body={"requests": additions}
        ).execute()
        print(f"Created tabs: {[a['addSheet']['properties']['title'] for a in additions]}")

    for name, headers in SHEET_SCHEMAS.items():
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{name}!A1",
            valueInputOption="RAW",
            body={"values": [headers]},
        ).execute()

    print("Sheet setup complete.")


if __name__ == "__main__":
    setup_sheets()

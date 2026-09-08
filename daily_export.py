"""Exports the events and ocr_results tabs to dated CSV files in Google
Drive, keeping the live Sheets small. Intended to run daily (e.g. 11 PM)."""
from datetime import datetime

import pandas as pd

import config
from google_sheets import get_all_rows, upload_csv_to_drive


def _export_tab(sheet_name: str, filename: str):
    rows = get_all_rows(config.GOOGLE_SHEET_ID, sheet_name)
    if not rows:
        return
    df = pd.DataFrame(rows[1:], columns=rows[0])
    upload_csv_to_drive(config.GOOGLE_DRIVE_FOLDER_ID, filename, df.to_csv(index=False))


def export_daily():
    today = datetime.now().strftime("%Y-%m-%d")
    _export_tab("events", f"events_{today}.csv")
    _export_tab("ocr_results", f"ocr_results_{today}.csv")
    print(f"Daily export complete for {today}")


if __name__ == "__main__":
    export_daily()

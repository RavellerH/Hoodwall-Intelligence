"""Scrapes hood.vantis.sh for wallet addresses using Playwright.

Intended to be scheduled every 10 minutes (cron / Windows Task Scheduler).
"""
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

import config
from google_sheets import append_rows, get_existing_addresses
from regex_utils import ADDRESS_RE


def scrape_hood():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        print(f"Scraping {config.HOOD_BASE_URL}...")
        page.goto(config.HOOD_BASE_URL, wait_until="networkidle")
        text = page.content()

        browser.close()

    addresses = set(ADDRESS_RE.findall(text))
    print(f"Found {len(addresses)} addresses on the page")

    existing = get_existing_addresses(config.GOOGLE_SHEET_ID, "candidates")
    new_addresses = sorted(addresses - existing)

    if new_addresses:
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            [addr, now, "hood.vantis.sh", config.HOOD_BASE_URL, "candidate", now]
            for addr in new_addresses
        ]
        append_rows(config.GOOGLE_SHEET_ID, "candidates", rows)

    print(f"Scrape complete: {len(new_addresses)} new candidates added ({len(addresses)} total found)")
    return new_addresses


if __name__ == "__main__":
    scrape_hood()

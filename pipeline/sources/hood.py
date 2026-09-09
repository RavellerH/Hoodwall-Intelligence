"""hood.vantis.sh candidate scraper.

Tries a plain HTTP fetch first and only falls back to a headless browser if
that yields nothing. The HTTP path is roughly 20x faster in CI and needs no
browser download, so it is worth attempting even on a client-rendered site -
many such sites still ship addresses in embedded JSON or __NEXT_DATA__.
"""
import json
import re

import requests

from .. import config
from ..regexes import find_addresses
from ..store import upsert, utcnow

SOURCE_NAME = "hood.vantis.sh"
HTTP_TIMEOUT = 30

# Candidate JSON endpoints probed before falling back to a browser. Unknown
# paths simply 404 and are skipped, so listing extras costs nothing.
API_PATHS = ["/api/wallets", "/api/leaderboard", "/api/holders", "/api/data"]


def _fetch_text(url):
    try:
        response = requests.get(
            url,
            timeout=HTTP_TIMEOUT,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; hoodwall-intelligence/2.0)",
                "Accept": "text/html,application/json",
            },
        )
        if response.status_code != 200:
            return None
        return response.text
    except requests.RequestException as exc:
        print(f"  ! {url}: {exc}")
        return None


def _scrape_http():
    """Collect addresses from the raw HTML and any JSON endpoints."""
    found = {}
    base = config.HOOD_BASE_URL.rstrip("/")

    html = _fetch_text(base)
    if html:
        for address in find_addresses(html):
            found[address] = base
        print(f"  html: {len(found)} address(es)")

    for path in API_PATHS:
        url = f"{base}{path}"
        text = _fetch_text(url)
        if not text:
            continue
        addresses = find_addresses(text)
        if addresses:
            print(f"  {path}: {len(addresses)} address(es)")
            for address in addresses:
                found.setdefault(address, url)

    return found


def _scrape_browser():
    """Render the page with Playwright, for a fully client-side site."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ! playwright not installed; skipping browser fallback")
        return {}

    base = config.HOOD_BASE_URL.rstrip("/")
    found = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent="Mozilla/5.0 (compatible; hoodwall-intelligence/2.0)")
            page.goto(base, wait_until="networkidle", timeout=60_000)
            # Give late XHR-driven tables a moment to paint.
            page.wait_for_timeout(3000)
            content = page.content()
            browser.close()
        for address in find_addresses(content):
            found[address] = base
        print(f"  browser: {len(found)} address(es)")
    except Exception as exc:  # a scraper failure must not kill the whole run
        print(f"  ! browser scrape failed: {exc}")
    return found


def run():
    print(f"[hood] scraping {config.HOOD_BASE_URL}")

    found = {} if config.HOOD_FORCE_BROWSER else _scrape_http()
    if not found:
        print("  no addresses over HTTP; falling back to headless browser")
        found = _scrape_browser()

    if not found:
        print("[hood] no addresses found")
        return 0

    now = utcnow()
    records = [
        {
            "address": address,
            "first_seen_at": now,
            "last_seen_at": now,
            "source": SOURCE_NAME,
            "source_url": url,
            "status": "candidate",
        }
        for address, url in sorted(found.items())
    ]
    new = upsert("candidates", records)
    print(f"[hood] {len(records)} address(es) seen, {new} new")
    return new


if __name__ == "__main__":
    run()

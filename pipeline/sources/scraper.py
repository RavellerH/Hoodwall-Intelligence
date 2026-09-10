"""Generic vendor-site candidate scraper.

Every wallet-tracking site this project ingests has the same shape: a page
of addresses, sometimes an unauthenticated JSON endpoint behind it,
sometimes rendered entirely client-side, and frequently addresses printed
truncated for display. Only the base URL and the endpoint guesses differ.

So the mechanism lives here once and each source is a few lines of
configuration. A second vendor should cost a config entry, not a copy of
this file - the first duplicate is where two scrapers start drifting apart
and only one of them gets the bug fix.

Masks are collected as well as full addresses: a scanner UI that shortens
"0x3475831749…3a12" for display is publishing a 32-bit identifier, and
masks.resolve_all() turns those into real addresses once the rest of the
pipeline has seen them on-chain. Dropping them loses the sighting entirely.
"""
import requests

from .. import masks
from ..regexes import find_addresses
from ..store import upsert, utcnow

HTTP_TIMEOUT = 30

# Probed on every source before falling back to a browser. Unknown paths
# simply 404 and are skipped, so listing extras costs nothing.
DEFAULT_API_PATHS = [
    "/api/wallets", "/api/leaderboard", "/api/holders", "/api/data",
    "/api/traders", "/api/addresses", "/api/stats",
]


def _fetch_text(url, timeout=HTTP_TIMEOUT):
    try:
        response = requests.get(
            url,
            timeout=timeout,
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


def _scrape_http(base, api_paths):
    """Addresses and masks from the raw HTML plus any JSON endpoints."""
    found, seen_masks = {}, {}

    html = _fetch_text(base)
    if html:
        for address in find_addresses(html):
            found[address] = base
        for mask in masks.find_masks(html):
            seen_masks[mask[0]] = mask
        print(f"  html: {len(found)} address(es), {len(seen_masks)} mask(s)")

    for path in api_paths:
        url = f"{base}{path}"
        text = _fetch_text(url)
        if not text:
            continue
        addresses = find_addresses(text)
        found_masks = masks.find_masks(text)
        if addresses or found_masks:
            print(f"  {path}: {len(addresses)} address(es), {len(found_masks)} mask(s)")
        for address in addresses:
            found.setdefault(address, url)
        for mask in found_masks:
            seen_masks.setdefault(mask[0], mask)

    return found, list(seen_masks.values())


def _scrape_browser(base):
    """Render the page with Playwright, for a fully client-side site."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  ! playwright not installed; skipping browser fallback")
        return {}, []

    found, seen_masks = {}, {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (compatible; hoodwall-intelligence/2.0)")
            page.goto(base, wait_until="networkidle", timeout=60_000)
            # Give late XHR-driven tables a moment to paint.
            page.wait_for_timeout(3000)
            content = page.content()
            browser.close()
        for address in find_addresses(content):
            found[address] = base
        for mask in masks.find_masks(content):
            seen_masks[mask[0]] = mask
        print(f"  browser: {len(found)} address(es), {len(seen_masks)} mask(s)")
    except Exception as exc:  # a scraper failure must not kill the whole run
        print(f"  ! browser scrape failed: {exc}")
    return found, list(seen_masks.values())


def run(source_name, base_url, api_paths=None, force_browser=False, tag=None):
    """Scrape one vendor site into the candidates table.

    Returns the number of genuinely new candidates, so `run.py` can report
    a total across sources.
    """
    tag = tag or source_name
    base = (base_url or "").rstrip("/")
    if not base:
        print(f"[{tag}] no base URL configured; skipping")
        return 0

    print(f"[{tag}] scraping {base}")
    found, seen_masks = ({}, []) if force_browser else _scrape_http(base, api_paths
                                                                   or DEFAULT_API_PATHS)
    if not found and not seen_masks:
        print("  no addresses over HTTP; falling back to headless browser")
        found, seen_masks = _scrape_browser(base)

    new_masks = masks.record(seen_masks, source_name, base) if seen_masks else 0

    if not found:
        print(f"[{tag}] no full addresses found"
              + (f"; {new_masks} new mask(s) recorded" if new_masks else ""))
        return new_masks

    now = utcnow()
    records = [
        {
            "address": address,
            "first_seen_at": now,
            "last_seen_at": now,
            "source": source_name,
            "source_url": url,
            "status": "candidate",
        }
        for address, url in sorted(found.items())
    ]
    new = upsert("candidates", records)
    print(f"[{tag}] {len(records)} address(es) seen, {new} new"
          + (f"; {new_masks} new mask(s)" if new_masks else ""))
    return new + new_masks

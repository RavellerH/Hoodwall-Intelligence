#!/usr/bin/env python3
"""Probe candidate Blockscout endpoints and report which one works.

The first production run failed with 403 on every call. Rather than change
one variable and wait 30 minutes for the next scheduled run, this probes
every plausible base URL and auth combination in a single job and prints a
table, so the working configuration is identified in one shot.

    python scripts/diagnose_chain.py            # uses BLOCKSCOUT_API_KEY if set
"""
import os
import sys

import requests

CHAIN_ID = os.environ.get("BLOCKSCOUT_CHAIN_ID", "4663")
API_KEY = os.environ.get("BLOCKSCOUT_API_KEY", "")
TIMEOUT = 20

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

# A known-active address is more informative than /stats alone, but stats is
# the cheapest liveness probe, so each base is checked with both.
SAMPLE_ADDRESS = os.environ.get(
    "SAMPLE_ADDRESS", "0x020bfc650a365f8bb26819deaabf3e21291018b4"
)

BASES = [
    ("pro-api (path chain id)", f"https://api.blockscout.com/{CHAIN_ID}/api/v2"),
    ("per-instance v2", "https://robinhoodchain.blockscout.com/api/v2"),
    ("per-instance v1", "https://robinhoodchain.blockscout.com/api"),
    ("testnet v2", "https://explorer.testnet.chain.robinhood.com/api/v2"),
]

HEADER_SETS = [
    ("bot UA, no key", {"User-Agent": "hoodwall-intelligence/2.0", "Accept": "application/json"}),
    ("browser UA, no key", {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*"}),
]
if API_KEY:
    HEADER_SETS.append((
        "browser UA + Bearer key",
        {"User-Agent": BROWSER_UA, "Accept": "application/json, text/plain, */*",
         "Authorization": f"Bearer {API_KEY}"},
    ))


def probe(base, path, headers, params=None):
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    try:
        r = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return "ERR", type(exc).__name__, url
    body = (r.text or "")[:90].replace("\n", " ")
    return r.status_code, body, url


def main():
    print(f"chain id : {CHAIN_ID}")
    print(f"api key  : {'set (' + str(len(API_KEY)) + ' chars)' if API_KEY else 'NOT SET'}")
    print(f"probing  : /stats and /addresses/{SAMPLE_ADDRESS}\n")

    working = []
    for base_name, base in BASES:
        for header_name, headers in HEADER_SETS:
            label = f"{base_name} | {header_name}"
            status, body, url = probe(base, "stats", headers)
            print(f"{str(status):>5}  {label}")
            print(f"       {url}")
            if status == 200:
                # Confirm the address endpoint too - /stats can be public
                # while per-address data is gated.
                addr_status, _, addr_url = probe(base, f"addresses/{SAMPLE_ADDRESS}", headers)
                print(f"       -> addresses/: {addr_status}")
                if addr_status in (200, 404):
                    working.append((label, base, headers))
            else:
                print(f"       {body}")
            print()

    # The query-param auth variant the Pro API also accepts.
    if API_KEY:
        status, body, url = probe(
            f"https://api.blockscout.com/{CHAIN_ID}/api/v2", "stats",
            {"User-Agent": BROWSER_UA}, params={"apikey": API_KEY})
        print(f"{str(status):>5}  pro-api | ?apikey= query param")
        print(f"       {url}\n")

    print("=" * 66)
    if working:
        print("WORKING CONFIGURATIONS:")
        for label, base, headers in working:
            print(f"  * {label}")
            print(f"    BLOCKSCOUT_BASE={base}")
            print(f"    auth: {'Bearer key' if 'Authorization' in headers else 'none needed'}")
        return 0

    print("NO WORKING CONFIGURATION FOUND.")
    if not API_KEY:
        print("  No API key was set. Robinhood Chain is served through the")
        print("  Blockscout Pro API - get a free key at https://blockscout.com")
        print("  and set it as the BLOCKSCOUT_API_KEY secret, then re-run.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

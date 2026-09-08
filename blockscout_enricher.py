"""Enriches candidate wallets with on-chain data from Blockscout."""
import json
from datetime import datetime, timezone

import requests

import config
from google_sheets import append_row, append_rows, get_all_rows, get_existing_addresses

REQUEST_TIMEOUT = 15
MAX_EVENTS_PER_WALLET = 50


def get_address_info(address: str):
    resp = requests.get(f"{config.BLOCKSCOUT_BASE}/addresses/{address}", timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def get_address_transactions(address: str):
    resp = requests.get(
        f"{config.BLOCKSCOUT_BASE}/addresses/{address}/transactions", timeout=REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()


def _address_field(value):
    """Blockscout v2 returns from/to as {"hash": ..., "is_contract": ...} objects."""
    if isinstance(value, dict):
        return value.get("hash", "")
    return value or ""


def enrich_candidate(address: str) -> bool:
    print(f"Enriching {address}...")
    try:
        info = get_address_info(address)
        txs = get_address_transactions(address)
    except requests.RequestException as e:
        print(f"Error enriching {address}: {e}")
        return False

    now = datetime.now(timezone.utc).isoformat()
    items = txs.get("items", [])

    append_row(
        config.GOOGLE_SHEET_ID,
        "wallets",
        [
            address,
            "contract" if info.get("is_contract") else "eoa",
            info.get("creation_date") or "",
            now,
            _address_field(items[0].get("from")) if items else "",
            json.dumps([{"source": "blockscout", "verified": True}]),
            "qualified",
            0,
            "candidate",
        ],
    )

    event_rows = [
        [
            tx["hash"], 0, address, tx.get("timestamp", ""), "contract_call",
            _address_field(tx.get("to")), "", "", "",
            _address_field(tx.get("to")), "", True, "blockscout",
        ]
        for tx in items[:MAX_EVENTS_PER_WALLET]
    ]
    append_rows(config.GOOGLE_SHEET_ID, "events", event_rows)

    print(f"Enrichment complete for {address}: {len(event_rows)} events recorded")
    return True


def run():
    candidates = get_all_rows(config.GOOGLE_SHEET_ID, "candidates")
    known_wallets = get_existing_addresses(config.GOOGLE_SHEET_ID, "wallets")

    for candidate in candidates[1:]:
        address = candidate[0]
        if address not in known_wallets:
            enrich_candidate(address)


if __name__ == "__main__":
    run()

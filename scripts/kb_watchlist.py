#!/usr/bin/env python3
"""Export the knowledge base as a watchlist for the realtime alert relay.

The Worker needs a flat list of addresses per chain family, plus enough
context to say WHO moved rather than just printing a hex string. Writes
JSON to stdout, or POSTs it straight to the Worker with --push.

    python scripts/kb_watchlist.py                      # print
    python scripts/kb_watchlist.py --push https://... --token $TOKEN
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.kb import load  # noqa: E402

# Chains whose alerting is handled by each transport.
ALCHEMY_CHAINS = {"ethereum", "arbitrum", "base", "optimism", "robinhood"}


def build():
    kb = load()
    out = {"evm": [], "solana": [], "hyperliquid": [], "bitcoin": [], "meta": {}}

    for wallet in kb.wallets.values():
        address = wallet.get("address")
        if not address:
            continue  # masked wallets cannot be watched until resolved
        chain = wallet["chain"]
        if chain == "hyperliquid":
            out["hyperliquid"].append(address)
        elif chain == "solana":
            out["solana"].append(address)
        elif chain == "bitcoin":
            out["bitcoin"].append(address)
        elif chain in ALCHEMY_CHAINS:
            out["evm"].append(address)

        entity = kb.entities.get(wallet.get("entity") or "")
        out["meta"][address.lower()] = {
            "chain": chain,
            "entity": entity["name"] if entity else None,
            "handle": wallet.get("handle"),
            "labels": wallet.get("labels", []),
        }

    # Entity-level BTC holders have no addresses, so they cannot be watched;
    # they are tracked in the KB but never appear in the watchlist.
    for key in ("evm", "solana", "hyperliquid", "bitcoin"):
        out[key] = sorted(set(out[key]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--push", metavar="WORKER_URL", help="POST to the Worker's /watchlist")
    ap.add_argument("--token", help="WATCHLIST_TOKEN for the push")
    args = ap.parse_args()

    watchlist = build()
    counts = {k: len(v) for k, v in watchlist.items() if k != "meta"}
    print(f"watchlist: {counts}", file=sys.stderr)

    if not args.push:
        print(json.dumps(watchlist, indent=1, sort_keys=True))
        return 0

    import urllib.request
    request = urllib.request.Request(
        args.push.rstrip("/") + "/watchlist",
        data=json.dumps(watchlist).encode(),
        headers={"authorization": f"Bearer {args.token or ''}",
                 "content-type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(f"pushed: {response.status}", file=sys.stderr)
    except Exception as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

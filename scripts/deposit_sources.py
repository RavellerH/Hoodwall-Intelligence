#!/usr/bin/env python3
"""Where did each tracked wallet's money come from?

Answers the funding question for every wallet in the knowledge base and on
the watchlist: who sent it value, how much, when it was first funded, and
whether each source is an exchange, a bridge, a mixer or another wallet.

The Hyperliquid case is the one worth understanding. The venue has no
transfer graph to walk - positions are internal - so flow tracing cannot
follow those accounts. But a Hyperliquid account is funded by bridging USDC
in from Arbitrum, and the depositor there is the same address. So the
question is answered on the on-ramp: this script redirects a hyperliquid
wallet to arbitrum automatically. The venue's own ledger is read too, when
--venue is passed, as corroboration on amounts and dates.

    python scripts/deposit_sources.py                    # everything tracked
    python scripts/deposit_sources.py --wallet 0x77ee... --venue
    python scripts/deposit_sources.py --entity theunipcs
    python scripts/deposit_sources.py --chain hyperliquid --limit 5
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import chain, config, flow, store  # noqa: E402
from pipeline.adapters import hyperliquid as hl  # noqa: E402
from pipeline.kb import addresses as addr  # noqa: E402
from pipeline.kb import loader  # noqa: E402

WATCHLIST = ROOT / "knowledge" / "watchlist.yml"


def targets(kb, args):
    """(address, chain) pairs to investigate, from the KB and the watchlist.

    A watchlist seed has no chain yet, so its chain_hint is used - that is
    what the hint is for, and without it a Hyperliquid seed would be looked
    up on chains that cannot see it.
    """
    out = {}
    for wallet in kb.wallets.values():
        if not wallet.get("address"):
            continue
        if args.entity and wallet.get("entity") != args.entity:
            continue
        out[(wallet["address"], wallet["chain"])] = None

    if WATCHLIST.exists() and not args.entity:
        rows = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or []
        for row in rows if isinstance(rows, list) else []:
            address = (row or {}).get("address")
            if address:
                out[(address.lower(), row.get("chain_hint") or "ethereum")] = None
    elif WATCHLIST.exists() and args.entity:
        rows = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or []
        for row in rows if isinstance(rows, list) else []:
            if (row or {}).get("entity") == args.entity and row.get("address"):
                out[(row["address"].lower(), row.get("chain_hint") or "ethereum")] = None

    pairs = sorted(out)
    if args.chain:
        pairs = [p for p in pairs if p[1] in args.chain]
    if args.wallet:
        wanted = {addr.normalize(w, "evm") for w in args.wallet}
        pairs = [p for p in pairs if p[0] in wanted]
    return pairs[:args.limit] if args.limit else pairs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wallet", action="append", default=[],
                    help="restrict to these addresses (repeatable)")
    ap.add_argument("--entity", help="restrict to one entity key")
    ap.add_argument("--chain", action="append", default=[],
                    help="restrict to wallets recorded on these chains")
    ap.add_argument("--limit", type=int, help="max wallets to investigate")
    ap.add_argument("--sources", type=int, default=config.MAX_FUNDING_SOURCES,
                    help="max funding sources kept per wallet, largest first")
    ap.add_argument("--venue", action="store_true",
                    help="also read the Hyperliquid ledger for venue accounts")
    ap.add_argument("--out", help="report path (default data/funding.json)")
    args = ap.parse_args()

    kb = loader.load()
    pairs = targets(kb, args)
    if not pairs:
        return print("no wallets matched") or 1

    print(f"investigating {len(pairs)} wallet(s)")
    try:
        report = flow.trace_funding(pairs, kb=kb, limit=args.sources)
    except chain.ChainAuthError as exc:
        return print(f"chain API refused the request: {exc}") or 2
    except flow.FlowError as exc:
        return print(f"{exc}") or 1

    if args.venue:
        print("\nreading the Hyperliquid ledger for venue accounts")
        for address, chain_key in pairs:
            if chain_key != "hyperliquid":
                continue
            summary = hl.funding_summary(address)
            report["wallets"].setdefault(f"{chain_key}:{address}", {})["venue"] = summary
            print(f"  {addr.shorten(address)}: {summary['deposit_count']} deposit(s), "
                  f"{summary['deposit_total']:,.2f} USDC in")

    out = Path(args.out) if args.out else Path(config.DATA_DIR) / "funding.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print("\n" + "=" * 60)
    print(flow.summarize_funding(report))
    print("=" * 60)
    print(f"report written to {out}")
    known = len(store.load("deposits"))
    print(f"deposit registry now holds {known} address(es)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

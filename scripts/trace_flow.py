#!/usr/bin/env python3
"""Trace money flow out from seed wallets to find the rest of the cluster.

Seeds come from knowledge/watchlist.yml (the recorded, human-curated list)
or from --seed on the command line. Each seed is probed across every
Blockscout-readable EVM chain first, because a bare 0x address carries no
chain with it, then its value edges are walked and every counterparty is
scored as "same owner" with the evidence attached.

    python scripts/trace_flow.py                     # every watchlist seed
    python scripts/trace_flow.py --seed 0xF64d... --depth 1
    python scripts/trace_flow.py --chain base --chain arbitrum
    python scripts/trace_flow.py --write-kb          # emit KB records

Nothing touches knowledge/ unless --write-kb is passed. Inferred links are
written with `confidence: inferred` and their evidence in `notes`, so a
heuristic can never be mistaken for a confirmed fact later.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import chain, flow, store  # noqa: E402
from pipeline.kb import addresses as addr  # noqa: E402

WATCHLIST = ROOT / "knowledge" / "watchlist.yml"
KNOWLEDGE = ROOT / "knowledge"


def load_watchlist():
    if not WATCHLIST.exists():
        return []
    rows = yaml.safe_load(WATCHLIST.read_text(encoding="utf-8")) or []
    return rows if isinstance(rows, list) else rows.get("seeds", [])


def merge_wallets(chain_key, records, write):
    """Add wallet records to knowledge/wallets/<chain>.yml without clobbering.

    Existing records win on collision: the KB is hand-authored and an
    inferred link must never overwrite a confirmed one.
    """
    path = KNOWLEDGE / "wallets" / f"{chain_key}.yml"
    existing = []
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        existing = loaded if isinstance(loaded, list) else []
    known = {(r.get("address") or r.get("masked") or "").lower() for r in existing}
    added = [r for r in records if r["address"] not in known]
    if added and write:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(existing + added, sort_keys=False,
                           allow_unicode=True, width=100),
            encoding="utf-8")
    return added


def kb_records(report, seed_meta):
    """Turn a trace report into wallet records, grouped by chain."""
    by_chain = {}
    today = store.utcnow()[:10]

    # The seeds themselves: now that probing has told us which chain they
    # are actually on, they can be recorded as real wallets rather than as
    # chainless watchlist entries.
    for seed, activity in report["activity"].items():
        meta = seed_meta.get(seed, {})
        for chain_key, info in activity.items():
            if info.get("is_contract"):
                continue
            record = {
                "chain": chain_key, "address": seed,
                "labels": meta.get("labels") or ["smart_money"],
                "confidence": meta.get("confidence", "reported"),
                "added": today,
                "notes": (meta.get("note") or "Seed wallet submitted for flow tracing.")
                         + f" Active on {chain_key}: {info['transactions']} tx.",
            }
            if meta.get("handle"):
                record["handle"] = meta["handle"]
            if meta.get("entity"):
                record["entity"] = meta["entity"]
            by_chain.setdefault(chain_key, []).append(record)

    for link in report["links"].values():
        if link["verdict"] != "probable":
            continue
        origin_meta = seed_meta.get(link["linked_to"], {})
        record = {
            "chain": link["chain"], "address": link["address"],
            "labels": ["fresh_emerging"],
            # Our own clustering heuristic - never "confirmed".
            "confidence": "inferred",
            "added": today,
            "notes": (f"Flow-linked to {addr.shorten(link['linked_to'])} "
                      f"(score {link['score']}): " + "; ".join(link["evidence"]) + "."),
        }
        if origin_meta.get("entity"):
            record["entity"] = origin_meta["entity"]
        by_chain.setdefault(link["chain"], []).append(record)
    return by_chain


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", action="append", default=[],
                    help="seed address (repeatable); defaults to knowledge/watchlist.yml")
    ap.add_argument("--chain", action="append", default=[],
                    help="restrict to these chain keys (repeatable)")
    ap.add_argument("--depth", type=int, help="hops out from each seed")
    ap.add_argument("--budget", type=int, help="max addresses examined in one run")
    ap.add_argument("--out", default=None, help="report path (default data/flow_links.json)")
    ap.add_argument("--write-kb", action="store_true",
                    help="write probable links into knowledge/wallets/")
    args = ap.parse_args()

    watchlist = load_watchlist()
    seed_meta = {}
    if args.seed:
        seeds = args.seed
        by_address = {(r.get("address") or "").lower(): r for r in watchlist}
        for s in seeds:
            seed_meta[addr.normalize(s, "evm")] = by_address.get(s.lower(), {})
    else:
        seeds = [r["address"] for r in watchlist if r.get("address")]
        seed_meta = {addr.normalize(r["address"], "evm"): r
                     for r in watchlist if r.get("address")}
    if not seeds:
        return print("no seeds: pass --seed or add entries to knowledge/watchlist.yml") or 1

    chains = flow.evm_chains()
    if args.chain:
        unknown = [c for c in args.chain if c not in chains]
        if unknown:
            return print(f"unknown or unreadable chain(s): {', '.join(unknown)}. "
                         f"Available: {', '.join(sorted(chains))}") or 1
        chains = {k: v for k, v in chains.items() if k in args.chain}

    try:
        report = flow.trace(seeds, chains=chains, depth=args.depth, budget=args.budget)
    except chain.ChainAuthError as exc:
        # A gated explorer is a configuration problem, not a finding.
        return print(f"chain API refused the request: {exc}") or 2
    except (flow.FlowError, addr.AddressError) as exc:
        return print(f"{exc}") or 1

    out = Path(args.out) if args.out else Path(store.config.DATA_DIR) / "flow_links.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print("\n" + "=" * 60)
    print(flow.summarize(report))
    print("=" * 60)
    print(f"report written to {out}")

    by_chain = kb_records(report, seed_meta)
    if not by_chain:
        return 0
    print("\nDRY RUN - nothing written (pass --write-kb to apply)"
          if not args.write_kb else "\nWRITING knowledge records")
    for chain_key, records in sorted(by_chain.items()):
        added = merge_wallets(chain_key, records, args.write_kb)
        print(f"  wallets/{chain_key}.yml: +{len(added)} new (of {len(records)} seen)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

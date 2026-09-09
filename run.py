#!/usr/bin/env python3
"""Hoodwall Intelligence pipeline entrypoint.

Every scheduled workflow invokes this with a stage name, so the schedule
lives in the workflow files and the logic lives here.

    python run.py ingest    # all candidate sources
    python run.py enrich    # on-chain enrichment
    python run.py score     # deterministic scoring (EVM)
    python run.py hyperliquid  # perps enrichment (no API key needed)
    python run.py publish   # build site/data/*.json
    python run.py digest    # daily Telegram digest
    python run.py all       # ingest -> enrich -> score -> publish
"""
import argparse
import sys
import time
import traceback

from pipeline import enrich, enrich_hl, masks, publish, score
from pipeline.sources import discovery, hood, telegram


def _ingest():
    """Run every candidate source.

    Sources are independent: one failing (a site redesign, an expired
    Telegram session) must never stop the others from contributing.
    """
    total, failures = 0, []
    for name, source in (("hood", hood), ("telegram", telegram), ("discovery", discovery)):
        try:
            total += source.run() or 0
        except Exception:
            failures.append(name)
            print(f"[{name}] UNHANDLED ERROR:\n{traceback.format_exc()}")
    # Resolution runs last: discovery has just widened the address universe,
    # so masks seen in earlier runs may now be resolvable.
    try:
        resolved, _ = masks.resolve_all()
        total += resolved
    except Exception:
        print(f"[masks] UNHANDLED ERROR:\n{traceback.format_exc()}")

    print(f"[ingest] {total} new candidate(s)"
          + (f"; failed sources: {', '.join(failures)}" if failures else ""))
    # Fail the job only if every source broke - that signals a real outage
    # rather than one flaky upstream.
    if failures and len(failures) == 3:
        raise RuntimeError("every ingestion source failed")
    return total


STAGES = {
    "ingest": _ingest,
    "enrich": enrich.run,
    "score": score.run,
    "hyperliquid": enrich_hl.run,
    "publish": publish.run,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=list(STAGES) + ["digest", "all"])
    args = parser.parse_args()

    if args.stage == "digest":
        from pipeline import digest
        digest.run()
        return 0

    stages = list(STAGES) if args.stage == "all" else [args.stage]
    started = time.time()

    for name in stages:
        print(f"\n{'=' * 60}\n{name.upper()}\n{'=' * 60}")
        stage_start = time.time()
        STAGES[name]()
        print(f"-- {name} finished in {time.time() - stage_start:.1f}s")

    print(f"\nTotal: {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

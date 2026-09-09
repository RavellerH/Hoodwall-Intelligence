#!/usr/bin/env python3
"""Validate the knowledge base. Used by CI and safe to run locally.

Exits non-zero on any validation error, so a malformed record fails the pull
request instead of silently vanishing from the dashboard.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.kb import load  # noqa: E402
from pipeline.kb.schema import ValidationError  # noqa: E402


def main():
    try:
        kb = load()
    except ValidationError as exc:
        print(f"FATAL: {exc}")
        return 1

    stats = kb.stats()
    print("Knowledge base")
    print(f"  chains     : {stats['chains']}")
    print(f"  wallets    : {stats['wallets']} "
          f"({stats['resolved']} resolved, {stats['unresolved']} masked)")
    print(f"  entities   : {stats['entities']}")
    print(f"  narratives : {stats['narratives']}")
    print(f"  sources    : {stats['sources']}")
    print(f"\n  by chain   : {stats['wallets_by_chain']}")
    print(f"  by label   : {stats['wallets_by_label']}")
    print(f"  narrative  : {stats['narratives_by_status']}")

    clustered = [(e['key'], len(e['cluster'])) for e in kb.entities.values() if e['cluster']]
    if clustered:
        print(f"\n  entity clusters ({len(clustered)}):")
        for key, count in sorted(clustered, key=lambda x: -x[1])[:8]:
            print(f"    {key}: {count} wallet(s)")

    if kb.errors:
        print(f"\n{len(kb.errors)} VALIDATION ERROR(S):")
        for err in kb.errors:
            print(f"  - {err}")
        return 1

    print("\nAll records valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

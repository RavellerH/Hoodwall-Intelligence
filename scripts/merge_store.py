#!/usr/bin/env python3
"""Merge two snapshots of the JSON store, key by key.

The pipeline commits its store back to the repo, so a run whose push is
rejected has to reconcile with whatever landed meanwhile. Rebasing cannot
do that: every store file is rewritten in full by both sides, so a rebase
conflicts every time and leaves the tree detached and unmerged - which is
exactly how a recoverable push rejection turned into a hard CI failure.

Every store table is a JSON object keyed by primary key, so the correct
merge is a key-wise union. Where both sides hold the same key, the local
(newer) record wins, since it was produced by the run that is still going.

    python scripts/merge_store.py <remote_dir> <local_dir>

Merges remote into local, in place, and reports what changed.
"""
import json
import sys
from pathlib import Path


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  ! {path.name}: unreadable ({exc}); treating as empty")
        return {}
    return data if isinstance(data, dict) else {}


def save(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1, sort_keys=True, ensure_ascii=False, default=str)
        fh.write("\n")


def merge(remote_dir, local_dir):
    remote_dir, local_dir = Path(remote_dir), Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    names = sorted({p.name for p in remote_dir.glob("*.json")} |
                   {p.name for p in local_dir.glob("*.json")})
    if not names:
        print("  nothing to merge")
        return 0

    total_added = 0
    for name in names:
        remote = load(remote_dir / name) if (remote_dir / name).exists() else {}
        local = load(local_dir / name) if (local_dir / name).exists() else {}

        # Start from the remote and let local records win: local is the
        # output of the run currently pushing, so it is the newer view.
        merged = dict(remote)
        merged.update(local)

        added = len(set(remote) - set(local))
        total_added += added
        save(local_dir / name, merged)
        print(f"  {name}: {len(local)} local + {added} kept from remote "
              f"-> {len(merged)}")
    return total_added


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    added = merge(sys.argv[1], sys.argv[2])
    print(f"merged: {added} record(s) preserved from the remote")
    return 0


if __name__ == "__main__":
    sys.exit(main())

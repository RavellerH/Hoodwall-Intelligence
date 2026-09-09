"""Git-committed JSON store - the cloud replacement for Google Sheets.

Why files instead of a database: the pipeline runs in ephemeral GitHub
Actions containers, so state has to live somewhere durable between runs.
Committing JSON back to the repo gives durability, a full audit history for
free (every run is a diff), and zero external services.

Design notes:
  * Every table is a JSON object keyed by its primary key, not a list of
    rows. The old Sheets design was append-only, which forced every reader
    to re-scan and de-duplicate ("keep the latest row per address"). Keyed
    upserts make that class of bug impossible.
  * Writes are atomic (temp file + os.replace) so an interrupted job can
    never leave a half-written table behind.
  * Dumps are sorted with a one-space indent so git diffs stay line-oriented
    and reviewable rather than one giant changed line.
"""
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config

# Primary key field for each table.
TABLES = {
    "candidates": "address",
    "wallets": "address",
    "events": "event_id",
    "scores": "address",
    "hl_scores": "address",
    "market": "chain",
    "telegram_messages": "message_id",
    "masked": "mask",
    "state": "key",
}


def utcnow():
    """Timezone-aware UTC timestamp in ISO-8601, used for every *_at field."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(table):
    return Path(config.DATA_DIR) / f"{table}.json"


def load(table):
    """Read a table. Missing or corrupt files degrade to empty rather than
    crashing the run - a scheduled job should never wedge on bad state."""
    path = _path(table)
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  ! {table}.json unreadable ({exc}); starting from empty")
        return {}
    return data if isinstance(data, dict) else {}


def save(table, rows):
    """Atomically write a table to disk."""
    path = _path(table)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def upsert(table, records):
    """Insert or merge records into a table. Returns the number of records
    that were genuinely new (used for "N new candidates" reporting)."""
    key_field = TABLES[table]
    rows = load(table)
    new_count = 0
    for record in records:
        key = str(record[key_field])
        if key in rows:
            rows[key].update(record)
        else:
            rows[key] = dict(record)
            new_count += 1
    save(table, rows)
    return new_count


def keys(table):
    """All primary keys in a table, as a set - used to skip already-seen work."""
    return set(load(table).keys())


def values(table):
    """All records in a table, as a list."""
    return list(load(table).values())


# --- Small key/value state table (cursors, run timestamps) -----------------

def get_state(key, default=None):
    row = load("state").get(key)
    return row.get("value", default) if row else default


def set_state(key, value):
    upsert("state", [{"key": key, "value": value, "updated_at": utcnow()}])


# --- Events need a cap; everything else grows slowly ----------------------

def event_id(tx_hash, log_index=0):
    return f"{tx_hash}:{log_index}"


def prune_events(max_per_wallet=None):
    """Keep only the most recent N events per wallet.

    Events are by far the fastest-growing table, and unbounded growth would
    eventually make every commit enormous. Enrichment only ever reads recent
    activity, so older events are dropped rather than archived.
    """
    limit = max_per_wallet or config.MAX_EVENTS_PER_WALLET
    events = load("events")

    by_wallet = {}
    for eid, event in events.items():
        by_wallet.setdefault(event.get("wallet_address", ""), []).append((eid, event))

    kept = {}
    for wallet_events in by_wallet.values():
        wallet_events.sort(key=lambda pair: pair[1].get("event_time") or "", reverse=True)
        for eid, event in wallet_events[:limit]:
            kept[eid] = event

    if len(kept) != len(events):
        print(f"  pruned events: {len(events)} -> {len(kept)}")
        save("events", kept)
    return kept


def events_by_wallet():
    """Group all events by wallet address, newest first."""
    grouped = {}
    for event in load("events").values():
        grouped.setdefault(event.get("wallet_address", ""), []).append(event)
    for wallet_events in grouped.values():
        wallet_events.sort(key=lambda e: e.get("event_time") or "", reverse=True)
    return grouped

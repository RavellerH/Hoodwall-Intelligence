"""Resolution of masked wallet addresses.

Alert feeds routinely publish addresses truncated for display, e.g.
"0x3475…3a12" - 4 leading and 4 trailing hex digits out of 40. A plain
40-hex regex extracts nothing from such a feed, which would silently make
the Telegram source contribute zero candidates.

A mask is still highly identifying: 8 hex digits is 32 bits, so against a
universe of even a million known addresses an accidental collision is
unlikely (~0.02% per lookup). This module therefore stores unresolved masks
and matches them against the full addresses that on-chain discovery finds,
promoting a mask to a real candidate once exactly one address matches.

Ambiguous masks (2+ matches) are deliberately left unresolved rather than
guessed - a wrong address is worse than a missing one.
"""
import re

from .store import load, save, upsert, utcnow

# Matches 0x1234…abcd / 0x1234...abcd / 0x1234โ€ฆabcd with 3-8 hex either side.
MASKED_RE = re.compile(
    r"\b(0x[a-fA-F0-9]{3,8})\s*(?:\.{2,3}|[…⋯])\s*([a-fA-F0-9]{3,8})\b"
)

# Below this many revealed hex digits a mask is too weak to identify anything.
MIN_MASK_HEX = 6


def find_masks(text):
    """Extract (mask_key, prefix, suffix) triples from text.

    mask_key is the canonical lowercase form used as the table's primary key.
    """
    if not text:
        return []
    out = {}
    for prefix, suffix in MASKED_RE.findall(text):
        prefix = prefix.lower()
        suffix = suffix.lower()
        revealed = len(prefix) - 2 + len(suffix)
        if revealed < MIN_MASK_HEX:
            continue
        out[f"{prefix}…{suffix}"] = (prefix, suffix)
    return [(key, p, s) for key, (p, s) in sorted(out.items())]


def matches(prefix, suffix, address):
    address = address.lower()
    return (
        len(address) == 42
        and address.startswith(prefix)
        and address.endswith(suffix)
    )


def known_addresses():
    """Every full address the system has seen, from any table.

    Counterparties are included: a masked wallet the feed mentions is very
    often already visible on-chain as the counterparty of a transaction we
    have already stored.
    """
    universe = set(load("candidates")) | set(load("wallets"))
    for event in load("events").values():
        counterparty = event.get("counterparty")
        if counterparty:
            universe.add(counterparty)
    return {a for a in universe if len(a) == 42 and a.startswith("0x")}


def record(masks, source, source_url=""):
    """Store newly-seen masks for later resolution."""
    if not masks:
        return 0
    now = utcnow()
    return upsert("masked", [
        {
            "mask": key,
            "prefix": prefix,
            "suffix": suffix,
            "source": source,
            "source_url": source_url,
            "first_seen_at": now,
            "status": "unresolved",
        }
        for key, prefix, suffix in masks
    ])


def resolve_all():
    """Try to resolve every unresolved mask against the known address universe.

    Returns (resolved_count, ambiguous_count).
    """
    masked = load("masked")
    pending = {k: v for k, v in masked.items() if v.get("status") == "unresolved"}
    if not pending:
        return 0, 0

    universe = known_addresses()
    if not universe:
        print(f"[masks] {len(pending)} unresolved, but no known addresses to match against")
        return 0, 0

    resolved, ambiguous = [], 0
    now = utcnow()

    for key, row in pending.items():
        hits = [a for a in universe if matches(row["prefix"], row["suffix"], a)]
        if len(hits) == 1:
            masked[key].update({"status": "resolved", "address": hits[0], "resolved_at": now})
            resolved.append((key, hits[0], row))
        elif len(hits) > 1:
            # Two real addresses share this mask; guessing would be wrong half
            # the time, so record the ambiguity and move on.
            masked[key].update({
                "status": "ambiguous", "candidates": sorted(hits), "resolved_at": now,
            })
            ambiguous += 1

    if resolved or ambiguous:
        save("masked", masked)

    if resolved:
        upsert("candidates", [
            {
                "address": address,
                "first_seen_at": row.get("first_seen_at", now),
                "last_seen_at": now,
                "source": row.get("source", "mask"),
                "source_url": row.get("source_url", ""),
                "status": "candidate",
                "resolved_from_mask": key,
            }
            for key, address, row in resolved
        ])

    print(f"[masks] {len(pending)} pending -> {len(resolved)} resolved, "
          f"{ambiguous} ambiguous, {len(pending)-len(resolved)-ambiguous} still unknown")
    return len(resolved), ambiguous

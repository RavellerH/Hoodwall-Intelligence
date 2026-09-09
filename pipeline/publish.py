"""Builds the static JSON artifacts the dashboard fetches.

The site is plain static files, so all shaping happens here rather than in
the browser. Raw Telegram message text is deliberately excluded: the site is
public, and republishing a channel's contents verbatim is both a privacy
and a copyright problem. Set PUBLISH_RAW_TEXT=true to override.
"""
import json
from pathlib import Path

from . import config, scoring
from .store import load, utcnow

# Wallets below this tier are omitted from the published feed to keep it
# small; they remain in the repo's data/ store.
PUBLISHED_TIERS = ("elite", "watch", "candidate")


def _write(name, payload):
    directory = Path(config.SITE_DATA_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    print(f"  wrote {path.relative_to(config.ROOT)} ({path.stat().st_size:,} bytes)")


def run():
    print("[publish] building site data")
    scores = load("scores")
    wallets = load("wallets")
    candidates = load("candidates")

    rows = []
    for address, score in scores.items():
        if score["tier"] not in PUBLISHED_TIERS:
            continue
        wallet = wallets.get(address, {})
        f = score["features"]
        rows.append({
            "address": address,
            "smart_score": score["smart_score"],
            "tier": score["tier"],
            "labels": [
                {"name": l["name"], "confidence": l["confidence"], "evidence": l["evidence"]}
                for l in score["labels"]
            ],
            "components": score["components"],
            "penalties": score["penalties"],
            "address_type": wallet.get("address_type", "unknown"),
            "balance_native": wallet.get("balance_native", 0),
            "source": candidates.get(address, {}).get("source", "unknown"),
            "tx_count": f["tx_count"],
            "active_days": f["active_days"],
            "distinct_counterparties": f["distinct_counterparties"],
            "contract_ratio": f["contract_ratio"],
            "volume_native": f["volume_native"],
            "net_flow_native": f["net_flow_native"],
            "first_seen": f["first_seen"],
            "last_seen": f["last_seen"],
            "recency_days": f["recency_days"],
            "hour_entropy": f["hour_entropy"],
            "gap_cv": f["gap_cv"],
        })

    rows.sort(key=lambda r: r["smart_score"], reverse=True)

    tier_counts, label_counts, source_counts = {}, {}, {}
    for row in rows:
        tier_counts[row["tier"]] = tier_counts.get(row["tier"], 0) + 1
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
        for label in row["labels"]:
            label_counts[label["name"]] = label_counts.get(label["name"], 0) + 1

    all_tiers = {}
    for score in scores.values():
        all_tiers[score["tier"]] = all_tiers.get(score["tier"], 0) + 1

    meta = {
        "title": config.SITE_TITLE,
        "generated_at": utcnow(),
        "scorer_version": "deterministic_v2",
        "explorer_url": config.CHAIN_EXPLORER_URL,
        "native_symbol": config.NATIVE_SYMBOL,
        "counts": {
            "candidates": len(candidates),
            "enriched": len(wallets),
            "scored": len(scores),
            "published": len(rows),
        },
        "tiers": all_tiers,
        "published_tiers": tier_counts,
        "labels": label_counts,
        "sources": source_counts,
        "thresholds": {
            "elite": config.ELITE_SCORE,
            "watch": config.WATCH_SCORE,
            "candidate": config.CANDIDATE_SCORE,
        },
        "weights": dict(scoring.WEIGHTS),
        "saturation": dict(scoring.SATURATION),
    }

    _write("wallets.json", rows)
    _write("meta.json", meta)
    print(f"[publish] {len(rows)} wallet(s) published of {len(scores)} scored")
    return len(rows)


if __name__ == "__main__":
    run()

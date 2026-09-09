"""Builds the static JSON artifacts the dashboard fetches.

The site is plain static files, so all shaping happens here rather than in
the browser. Raw Telegram message text is deliberately excluded: the site is
public, and republishing a channel's contents verbatim is both a privacy
and a copyright problem. Set PUBLISH_RAW_TEXT=true to override.
"""
import json
from pathlib import Path

from . import config, graph, scoring, sentiment
from .kb import load as load_kb
from .kb.schema import ValidationError
from .store import load, utcnow

# Wallets below this tier are omitted from the published feed to keep it
# small; they remain in the repo's data/ store.
PUBLISHED_TIERS = ("elite", "watch", "candidate")


def _write(name, payload):
    directory = Path(config.SITE_DATA_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    with open(path, "w", encoding="utf-8") as fh:
        # default=str is a safety net: a stray date or Decimal from a YAML
        # record must not take down the whole publish step.
        json.dump(payload, fh, indent=1, sort_keys=True,
                  ensure_ascii=False, default=str)
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

    kb_payload = _build_kb(rows)
    meta["knowledge"] = kb_payload["stats"]

    graph_payload, sentiment_payload = _build_graph_and_sentiment(rows)
    meta["graph"] = graph_payload.get("stats", {})
    meta["sentiment"] = {
        "score": sentiment_payload.get("overall", {}).get("score"),
        "label": sentiment_payload.get("overall", {}).get("label"),
        "confidence": sentiment_payload.get("overall", {}).get("confidence"),
    }

    _write("wallets.json", rows)
    _write("kb.json", kb_payload)
    _write("graph.json", graph_payload)
    _write("sentiment.json", sentiment_payload)
    _write("meta.json", meta)
    print(f"[publish] {len(rows)} wallet(s) published of {len(scores)} scored")
    return len(rows)


if __name__ == "__main__":
    run()


def _build_kb(scored_rows):
    """Shape the knowledge base for the dashboard.

    Observed on-chain data is merged onto curated records here rather than
    in the browser, so the terminal only has to render. A KB wallet that has
    also been enriched carries its score; one that has not simply lacks it,
    which is the honest representation - most curated wallets are on chains
    with no live adapter yet.
    """
    try:
        kb = load_kb()
    except ValidationError as exc:
        print(f"  ! knowledge base failed to load: {exc}")
        return {"stats": {}, "wallets": [], "entities": [], "narratives": [],
                "chains": {}, "sources": [], "errors": [str(exc)]}

    if kb.errors:
        for err in kb.errors:
            print(f"  ! kb: {err}")

    observed = {r["address"]: r for r in scored_rows if r.get("address")}
    # Hyperliquid has its own feature space and scorer, so its observations
    # are merged separately rather than forced into the EVM row shape.
    hl_observed = load("hl_scores")

    wallets = []
    for wallet in kb.wallets.values():
        row = {
            "key": wallet["key"],
            "chain": wallet["chain"],
            "address": wallet["address"],
            "masked": wallet.get("masked"),
            "display": wallet["display"],
            "resolved": wallet["resolved"],
            "entity": wallet.get("entity"),
            "handle": wallet.get("handle"),
            "labels": [{"name": l, "source": "curated"}
                       for l in wallet.get("labels", [])],
            "confidence": wallet.get("confidence"),
            "conviction": wallet.get("conviction"),
            "narratives": wallet.get("narratives", []),
            "source": wallet.get("source"),
            "notes": wallet.get("notes"),
        }
        address = wallet["address"]
        match = observed.get(address) if address else None
        if match:
            row["observed"] = {
                "kind": "evm",
                "smart_score": match["smart_score"],
                "tier": match["tier"],
                "tx_count": match["tx_count"],
                "active_days": match["active_days"],
                "volume_native": match["volume_native"],
                "last_seen": match["last_seen"],
            }
        hl_match = hl_observed.get(address) if address else None
        if hl_match:
            f = hl_match["features"]
            row["observed"] = {
                "kind": "perps",
                "smart_score": hl_match["smart_score"],
                "tier": hl_match["tier"],
                "components": hl_match["components"],
                "equity": f["equity"],
                "notional": f["notional"],
                "leverage": f["account_leverage"],
                "positions": f["positions"],
                "position_count": f["position_count"],
                "net_bias": f["net_bias"],
                "concentration": f["concentration"],
                "unrealized_pnl": f["unrealized_pnl"],
                "realized_pnl": f["realized_pnl"],
                "win_rate": f["win_rate"],
                "fill_count": f["fill_count"],
                "top_coins": f["top_coins"],
            }
            # Adapter labels augment the curated ones rather than replace
            # them, and carry their evidence plus an "observed" provenance so
            # the terminal can show which assertions are ours vs measured.
            existing = {l["name"] for l in row["labels"]}
            row["labels"] = row["labels"] + [
                {**l, "source": "observed"}
                for l in hl_match["labels"] if l["name"] not in existing]
        wallets.append(row)
    wallets.sort(key=lambda w: (w["chain"], w["display"] or ""))

    entities = []
    for entity in kb.entities.values():
        entities.append({
            "key": entity["key"],
            "name": entity["name"],
            "type": entity["type"],
            "confidence": entity["confidence"],
            "handles": entity.get("handles", {}),
            "cluster": entity.get("cluster", []),
            "cluster_size": len(entity.get("cluster", [])),
            "holdings": entity.get("holdings", []),
            "narratives": entity.get("narratives", []),
            "notes": entity.get("notes"),
        })
    entities.sort(key=lambda e: (-e["cluster_size"], e["name"].lower()))

    narratives = []
    for narrative in kb.narratives.values():
        narratives.append({
            "key": narrative["key"],
            "title": narrative["title"],
            "status": narrative["status"],
            "conviction": narrative["conviction"],
            "chains": narrative.get("chains", []),
            "tokens": narrative.get("tokens", []),
            "entities": narrative.get("entities", []),
            "linked_wallets": narrative.get("linked_wallets", []),
            "opened": narrative.get("opened"),
            "closed": narrative.get("closed"),
            "outcomes": narrative.get("outcomes", []),
            "updates": narrative.get("updates", []),
            "body": narrative.get("body", ""),
        })
    narratives.sort(key=lambda n: (n["status"] != "active", n["key"]))

    stats = kb.stats()
    stats["errors"] = len(kb.errors)
    return {
        "stats": stats,
        "chains": kb.chains,
        "wallets": wallets,
        "entities": entities,
        "narratives": narratives,
        "sources": list(kb.sources.values()),
        "errors": kb.errors,
    }


def _build_graph_and_sentiment(scored_rows):
    """Assemble the relationship graph and the sentiment read.

    Both degrade gracefully: with no enrichment yet the graph is entirely
    behavioural and the flow signal reports zero confidence rather than a
    fabricated neutral reading.
    """
    try:
        kb = load_kb()
    except ValidationError as exc:
        print(f"  ! graph skipped, knowledge base failed: {exc}")
        return {"nodes": [], "edges": [], "clusters": [], "stats": {}}, {}

    events = load("events")
    hl_scores = load("hl_scores")
    evm_scores = {r["address"]: r for r in load("scores").values() if r.get("address")}

    graph_payload = graph.build(kb, events, hl_scores, evm_scores)
    stats = graph_payload["stats"]
    print(f"  graph: {stats['nodes']} nodes, {stats['edges']} edges "
          f"({stats['transfer_edges']} transfer / {stats['behavioural_edges']} behavioural), "
          f"{stats['clusters']} clusters")

    messages = list(load("telegram_messages").values())
    source_trust = None
    for source in kb.sources.values():
        if source.get("trust"):
            source_trust = source["trust"]
            break

    overall = sentiment.analyze(
        graph_payload["nodes"], hl_scores, messages, quotes=[], posts=[],
        source_trust=source_trust)
    by_narrative = sentiment.per_narrative(kb, graph_payload["nodes"], hl_scores, messages)

    print(f"  sentiment: {overall['label']} {overall['score']:+.2f} "
          f"(confidence {overall['confidence']:.2f}, "
          f"sources reporting: {', '.join(overall['reporting_sources']) or 'none'})")

    return graph_payload, {"overall": overall, "by_narrative": by_narrative}

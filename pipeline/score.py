"""Scores every enriched wallet using the deterministic scorer.

No model, no API, no network: this step is a pure function of the stored
events, so it is fast, free, and reproducible. Re-running it on unchanged
data produces byte-identical output.
"""
from . import features, scoring
from .store import events_by_wallet, load, save, utcnow


def run():
    wallets = load("wallets")
    grouped = events_by_wallet()

    if not wallets:
        print("[score] no enriched wallets yet")
        return 0

    # Volume percentile is a cohort statistic, so features for every wallet
    # must be computed before any wallet can be labeled a whale.
    vectors = {
        address: features.extract(address, grouped.get(address, []))
        for address in wallets
    }
    volume_p95 = scoring.percentile(
        [f["volume_native"] for f in vectors.values()], 95
    )

    now = utcnow()
    scores = {}
    for address, vector in vectors.items():
        result = scoring.score_wallet(vector)
        labels = scoring.label_wallet(vector, result["smart_score"], volume_p95)
        scores[address] = {
            "address": address,
            "smart_score": result["smart_score"],
            "tier": result["tier"],
            "base_score": result["base_score"],
            "components": result["components"],
            "penalties": result["penalties"],
            "insufficient_history": result["insufficient_history"],
            "labels": labels,
            "features": vector,
            "scored_at": now,
            "scorer_version": "deterministic_v2",
        }

    save("scores", scores)

    tiers = {}
    for row in scores.values():
        tiers[row["tier"]] = tiers.get(row["tier"], 0) + 1
    summary = ", ".join(f"{tier}={count}" for tier, count in sorted(tiers.items()))
    print(f"[score] scored {len(scores)} wallet(s): {summary}")
    return len(scores)


if __name__ == "__main__":
    run()

"""Deterministic Smart Score and behavioural labels.

The score is a weighted sum of six normalized components, each derived from
the feature vector in features.py. Every wallet's score ships with its
component breakdown, so the dashboard can always answer "why is this an 82?"
- something the previous LLM-generated score could never do.

Saturation constants below are the tuning surface: SATURATION["tx_count"]
= 500 means "500 transactions is as much credit as volume of activity can
earn". Adjust them against real data rather than adjusting the weights.
"""
import math

from . import config

# Component weights; must sum to 1.0 (asserted at import).
WEIGHTS = {
    "activity_scale": 0.25,      # how much has this wallet done
    "sustained_presence": 0.20,  # consistently, over time - not one burst
    "counterparty_breadth": 0.20,  # dealing with many distinct parties
    "protocol_engagement": 0.15,   # contract interaction vs plain transfers
    "economic_weight": 0.15,       # value actually moved
    "recency": 0.05,               # still active today
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "component weights must sum to 1.0"

# Log-scale saturation points: the value at which a component earns ~full credit.
SATURATION = {
    "tx_count": 500,
    "counterparties": 100,
    "volume_native": 1000.0,
    "active_days": 90,
}

# Half-life in days for the recency decay.
RECENCY_HALFLIFE_DAYS = 14.0

# A wallet with fewer than this many events cannot be meaningfully profiled.
MIN_EVENTS_FOR_SIGNAL = 3
NOISE_SCORE_CAP = 20.0


def _log_norm(value, saturation):
    """Map [0, inf) onto [0, 1] on a log curve that reaches ~1 at saturation.

    Log rather than linear because the difference between 10 and 50
    transactions is far more meaningful than between 1000 and 1040.
    """
    if not value or value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(saturation))


def components(f):
    """Compute each normalized 0-1 score component from a feature vector."""
    activity_scale = _log_norm(f["tx_count"], SATURATION["tx_count"])

    # Presence rewards both breadth of active days and day-to-day density,
    # so a wallet that fired 200 transactions in one hour and vanished does
    # not outrank one that has traded steadily for three months.
    sustained_presence = 0.6 * _log_norm(
        f["active_days"], SATURATION["active_days"]
    ) + 0.4 * min(1.0, f["activity_density"])

    # Breadth combines absolute reach with per-transaction diversity, which
    # separates a hub wallet from one looping against a single counterparty.
    counterparty_breadth = 0.7 * _log_norm(
        f["distinct_counterparties"], SATURATION["counterparties"]
    ) + 0.3 * min(1.0, f["counterparty_diversity"])

    protocol_engagement = min(1.0, f["contract_ratio"])
    economic_weight = _log_norm(f["volume_native"], SATURATION["volume_native"])

    recency_days = f.get("recency_days")
    if recency_days is None:
        recency = 0.0
    else:
        recency = 0.5 ** (max(0.0, recency_days) / RECENCY_HALFLIFE_DAYS)

    return {
        "activity_scale": round(activity_scale, 4),
        "sustained_presence": round(sustained_presence, 4),
        "counterparty_breadth": round(counterparty_breadth, 4),
        "protocol_engagement": round(protocol_engagement, 4),
        "economic_weight": round(economic_weight, 4),
        "recency": round(recency, 4),
    }


def penalties(f):
    """Multiplicative penalties, each returned with a human-readable reason."""
    applied = []

    if f["fail_ratio"] > 0.30:
        applied.append(("high_failure_rate", 0.70,
                        f"{f['fail_ratio']:.0%} of transactions reverted"))

    # Many transactions but effectively one counterparty is the signature of
    # a faucet, an airdrop sink, or spam - not of a wallet worth watching.
    if f["tx_count"] >= 10 and f["top_counterparty_share"] > 0.90:
        applied.append(("single_counterparty", 0.70,
                        f"{f['top_counterparty_share']:.0%} of activity with one address"))

    # Zero value moved across many transactions means dust or spam.
    if f["tx_count"] >= 10 and f["volume_native"] <= 0:
        applied.append(("no_value_moved", 0.75,
                        "no native value moved across all transactions"))

    return applied


def tier_for(score):
    if score >= config.ELITE_SCORE:
        return "elite"
    if score >= config.WATCH_SCORE:
        return "watch"
    if score >= config.CANDIDATE_SCORE:
        return "candidate"
    return "archive"


def score_wallet(f):
    """Score one wallet from its feature vector.

    Returns the score, tier, per-component breakdown and applied penalties,
    so every number in the dashboard is traceable back to its inputs.
    """
    comps = components(f)
    base = sum(WEIGHTS[name] * value for name, value in comps.items()) * 100

    applied = penalties(f)
    multiplier = 1.0
    for _, factor, _ in applied:
        multiplier *= factor
    score = base * multiplier

    # Too little history to judge: cap rather than let a sparse wallet with
    # one lucky contract call rank alongside a profiled one.
    capped = False
    if f["tx_count"] < MIN_EVENTS_FOR_SIGNAL:
        score = min(score, NOISE_SCORE_CAP)
        capped = True

    score = round(max(0.0, min(100.0, score)), 1)
    return {
        "smart_score": score,
        "tier": tier_for(score),
        "components": comps,
        "weights": dict(WEIGHTS),
        "base_score": round(base, 1),
        "penalties": [
            {"name": name, "factor": factor, "reason": reason}
            for name, factor, reason in applied
        ],
        "insufficient_history": capped,
    }


# --- Rule-based labels ----------------------------------------------------
# Each rule returns (confidence, evidence) or None. Confidence is a stated
# strength of the rule, not a probability - it is used only for ranking.

def _label_noise(f):
    if f["tx_count"] < MIN_EVENTS_FOR_SIGNAL:
        return 0.9, f"Only {f['tx_count']} recorded transaction(s)"
    if f["tx_count"] >= 10 and f["volume_native"] <= 0 and f["contract_ratio"] < 0.1:
        return 0.7, "Many transactions but no value moved and no contract activity"
    return None


def _label_trading_bot(f):
    """Machine-driven timing.

    Two independent signatures, either of which is sufficient:
      1. Metronomic gaps between transactions (low coefficient of variation).
         Note a fixed-interval bot can concentrate in a handful of clock
         hours - a 4.8h cycle only ever touches 5 - so regularity must NOT
         be gated on round-the-clock hour coverage.
      2. Irregular gaps but genuinely continuous 24/7 operation, which no
         hand-trading human sustains.
    """
    if f["gap_cv"] is None or f["tx_count"] < 20:
        return None

    if f["gap_cv"] < 0.6:
        return 0.85, (
            f"Metronomic transaction timing (gap CV {f['gap_cv']:.2f}; "
            f"a human trader typically exceeds 1.0)"
        )

    # A coefficient of variation near 1.0 is simply random arrival, which is
    # also what unstructured human activity looks like - so it carries no
    # evidence on its own. The second signature therefore rests on volume
    # and coverage instead: never sleeping AND transacting many times a day.
    daily_rate = f["tx_count"] / f["active_days"] if f["active_days"] else 0
    if f["hour_entropy"] > 0.93 and f["active_days"] >= 14 and daily_rate >= 5:
        return 0.7, (
            f"Never idle: {daily_rate:.1f} transactions/day across "
            f"{f['active_days']} days at all hours "
            f"(hour entropy {f['hour_entropy']:.2f})"
        )
    return None


def _label_mev_bot(f):
    if f["tx_count"] < 50:
        return None
    if f["contract_ratio"] > 0.9 and f["max_daily_tx"] >= 50 and f["hour_entropy"] > 0.8:
        return 0.7, (
            f"{f['max_daily_tx']} contract calls in a single day, "
            "continuous 24h operation"
        )
    return None


def _label_whale(f, volume_p95):
    if volume_p95 and f["volume_native"] >= volume_p95 and f["volume_native"] > 0:
        return 0.75, (
            f"{f['volume_native']:.2f} {config.NATIVE_SYMBOL} moved - "
            "top 5% of tracked wallets by volume"
        )
    return None


def _label_accumulator(f):
    if f["volume_native"] <= 0:
        return None
    ratio = f["net_flow_native"] / f["volume_native"]
    if ratio > 0.4 and f["tx_count"] >= 5:
        return round(min(0.9, ratio), 2), (
            f"Net inflow of {f['net_flow_native']:.2f} {config.NATIVE_SYMBOL} "
            f"({ratio:.0%} of total volume)"
        )
    return None


def _label_distributor(f):
    if f["volume_native"] <= 0:
        return None
    ratio = -f["net_flow_native"] / f["volume_native"]
    if ratio > 0.4 and f["tx_count"] >= 5:
        return round(min(0.9, ratio), 2), (
            f"Net outflow of {abs(f['net_flow_native']):.2f} {config.NATIVE_SYMBOL} "
            f"({ratio:.0%} of total volume)"
        )
    return None


def _label_fresh_emerging(f):
    if f["age_days"] and f["age_days"] < 30 and f["tx_count"] >= 10 and f["activity_density"] > 0.5:
        return 0.7, (
            f"{f['tx_count']} transactions in {f['age_days']:.0f} days since first activity"
        )
    return None


def _label_lp_mm(f):
    if f["tx_count"] < 20 or f["contract_ratio"] < 0.7:
        return None
    # A market maker cycles a small set of methods against few venues.
    if f["distinct_methods"] and f["distinct_methods"] <= 4 and f["distinct_counterparties"] <= 5:
        return 0.6, (
            f"Repetitive contract activity: {f['distinct_methods']} method(s) "
            f"across {f['distinct_counterparties']} venue(s)"
        )
    return None


def _label_smart_money(f, score):
    if score < config.WATCH_SCORE:
        return None
    if f["contract_ratio"] > 0.5 and f["distinct_counterparties"] >= 10 and f["active_days"] >= 14:
        return 0.7, (
            f"Sustained, diverse protocol activity: {f['distinct_counterparties']} "
            f"counterparties over {f['active_days']} active days"
        )
    return None


def label_wallet(f, score, volume_p95=None):
    """Apply every label rule; returns labels sorted by confidence."""
    labels = []

    def add(name, result):
        if result:
            confidence, evidence = result
            labels.append({"name": name, "confidence": confidence, "evidence": evidence})

    noise = _label_noise(f)
    if noise:
        add("noise", noise)
        return labels  # a noise wallet gets no further characterization

    add("trading_bot", _label_trading_bot(f))
    add("mev_bot", _label_mev_bot(f))
    add("whale", _label_whale(f, volume_p95))
    add("accumulator", _label_accumulator(f))
    add("distributor", _label_distributor(f))
    add("fresh_emerging", _label_fresh_emerging(f))
    add("lp_mm", _label_lp_mm(f))
    add("smart_money", _label_smart_money(f, score))

    labels.sort(key=lambda item: item["confidence"], reverse=True)
    return labels


def percentile(values, pct):
    """Nearest-rank percentile of a list of numbers."""
    numbers = sorted(v for v in values if v is not None)
    if not numbers:
        return None
    index = min(len(numbers) - 1, max(0, int(round(pct / 100 * len(numbers))) - 1))
    return numbers[index]

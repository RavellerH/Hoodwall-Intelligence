"""Behavioural feature extraction from a wallet's on-chain event history.

These features are the input to both the Smart Score and the rule-based
labels. Everything here is a pure function of the stored events: given the
same events you get the same features, every run, forever. That
reproducibility is the whole point of replacing the LLM-invented score.
"""
import math
from collections import Counter
from datetime import datetime, timezone


def _parse_time(value):
    """Parse a Blockscout/ISO timestamp into an aware UTC datetime, or None."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _shannon_entropy(counts, buckets):
    """Normalized Shannon entropy (0 = all in one bucket, 1 = perfectly flat)."""
    total = sum(counts.values())
    if total <= 1 or buckets <= 1:
        return 0.0
    entropy = -sum(
        (n / total) * math.log2(n / total) for n in counts.values() if n > 0
    )
    return min(1.0, entropy / math.log2(buckets))


def _coefficient_of_variation(gaps):
    """Std/mean of inter-transaction gaps.

    Near zero means machine-regular timing (a cron-driven bot); a human
    trading by hand produces a value comfortably above 1.
    """
    if len(gaps) < 3:
        return None
    mean = sum(gaps) / len(gaps)
    if mean <= 0:
        return None
    variance = sum((g - mean) ** 2 for g in gaps) / len(gaps)
    return math.sqrt(variance) / mean


def extract(address, events, now=None):
    """Compute the full feature vector for one wallet."""
    now = now or datetime.now(timezone.utc)
    address = (address or "").lower()

    times = sorted(t for t in (_parse_time(e.get("event_time")) for e in events) if t)
    tx_count = len(events)

    first_seen = times[0] if times else None
    last_seen = times[-1] if times else None
    span_days = ((last_seen - first_seen).total_seconds() / 86400) if len(times) > 1 else 0.0
    age_days = ((now - first_seen).total_seconds() / 86400) if first_seen else 0.0
    recency_days = ((now - last_seen).total_seconds() / 86400) if last_seen else None

    active_days = len({t.date() for t in times})
    # Density: of the days this wallet has existed, how many did it act on?
    density = active_days / max(span_days, 1.0) if span_days > 0 else (1.0 if active_days else 0.0)

    counterparties = Counter(
        cp for cp in (e.get("counterparty") for e in events) if cp and cp != address
    )
    distinct_counterparties = len(counterparties)
    top_counterparty_share = (
        counterparties.most_common(1)[0][1] / tx_count if counterparties and tx_count else 0.0
    )

    contract_calls = sum(1 for e in events if e.get("to_is_contract"))
    contract_ratio = contract_calls / tx_count if tx_count else 0.0

    failures = sum(1 for e in events if e.get("success") is False)
    fail_ratio = failures / tx_count if tx_count else 0.0

    inflow = sum(float(e.get("value_native") or 0) for e in events if e.get("direction") == "in")
    outflow = sum(float(e.get("value_native") or 0) for e in events if e.get("direction") == "out")
    volume_native = inflow + outflow
    net_flow_native = inflow - outflow

    hour_counts = Counter(t.hour for t in times)
    hour_entropy = _shannon_entropy(hour_counts, 24)
    weekday_entropy = _shannon_entropy(Counter(t.weekday() for t in times), 7)

    gaps = [
        (times[i + 1] - times[i]).total_seconds() for i in range(len(times) - 1)
    ]
    gap_cv = _coefficient_of_variation([g for g in gaps if g > 0])
    median_gap_hours = (sorted(gaps)[len(gaps) // 2] / 3600) if gaps else None

    day_counts = Counter(t.date() for t in times)
    max_daily_tx = max(day_counts.values()) if day_counts else 0

    method_counts = Counter(e.get("method") for e in events if e.get("method"))

    return {
        "address": address,
        "tx_count": tx_count,
        "first_seen": first_seen.isoformat() if first_seen else None,
        "last_seen": last_seen.isoformat() if last_seen else None,
        "age_days": round(age_days, 2),
        "span_days": round(span_days, 2),
        "recency_days": round(recency_days, 2) if recency_days is not None else None,
        "active_days": active_days,
        "activity_density": round(density, 4),
        "distinct_counterparties": distinct_counterparties,
        "counterparty_diversity": round(distinct_counterparties / tx_count, 4) if tx_count else 0.0,
        "top_counterparty_share": round(top_counterparty_share, 4),
        "contract_ratio": round(contract_ratio, 4),
        "fail_ratio": round(fail_ratio, 4),
        "inflow_native": round(inflow, 6),
        "outflow_native": round(outflow, 6),
        "volume_native": round(volume_native, 6),
        "net_flow_native": round(net_flow_native, 6),
        "hour_entropy": round(hour_entropy, 4),
        "weekday_entropy": round(weekday_entropy, 4),
        "gap_cv": round(gap_cv, 4) if gap_cv is not None else None,
        "median_gap_hours": round(median_gap_hours, 3) if median_gap_hours is not None else None,
        "max_daily_tx": max_daily_tx,
        "distinct_methods": len(method_counts),
        "top_methods": [m for m, _ in method_counts.most_common(5)],
    }

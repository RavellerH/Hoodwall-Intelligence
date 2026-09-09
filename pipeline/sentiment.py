"""Deterministic sentiment from measurable behaviour.

Sentiment here is not opinion mining - it is what tracked money actually
did, plus what the feeds said, weighted by how reliable those feeds have
proven. Every signal is a pure function of stored data, so the same inputs
always produce the same reading.

Each source returns a Signal:
    score      -1.0 (bearish) .. +1.0 (bullish)
    confidence  0.0 .. 1.0 - how much this source deserves to count here
    evidence    a human-readable string citing the actual numbers

The composite is confidence-weighted, so a source with no data contributes
nothing rather than dragging the reading toward neutral.

An LLM narrator can later be dropped in at `narrate()` without touching any
of this: the numbers stay deterministic and the model only writes prose.
"""
import math
import re
from collections import defaultdict

# Relative trust in each source before its own confidence is applied.
SOURCE_WEIGHTS = {
    "flow": 1.0,        # what wallets did - hardest to fake
    "positioning": 0.8,  # what perps traders are betting
    "price": 0.6,       # momentum
    "feed": 0.3,        # what a vendor claimed
    "social": 0.3,      # external chatter
}

# Lexicon for feed text. Deterministic and auditable; it is not trying to be
# a language model, only to register explicit directional claims.
BULLISH_TERMS = {
    "smart money": 2.0, "accumulating": 2.0, "accumulation": 2.0, "buying": 1.5,
    "runner": 1.5, "momentum": 1.2, "breakout": 1.5, "ath": 1.2, "alpha": 1.0,
    "bullish": 2.0, "long": 1.0, "aped": 1.0, "sending": 1.0, "2x": 1.0,
    "5x": 1.5, "10x": 2.0, "moon": 1.0,
}
BEARISH_TERMS = {
    "rug": -3.0, "rugged": -3.0, "honeypot": -3.0, "scam": -2.5, "dump": -2.0,
    "dumping": -2.0, "selling": -1.5, "exit": -1.5, "bearish": -2.0,
    "short": -1.0, "down": -1.0, "dead": -1.5, "-9": -1.5, "rekt": -2.0,
}

NEUTRAL_BAND = 0.12   # |score| below this reads as neutral


class Signal:
    __slots__ = ("source", "score", "confidence", "evidence")

    def __init__(self, source, score, confidence, evidence):
        self.score = max(-1.0, min(1.0, score))
        self.confidence = max(0.0, min(1.0, confidence))
        self.source = source
        self.evidence = evidence

    def as_dict(self):
        return {"source": self.source, "score": round(self.score, 3),
                "confidence": round(self.confidence, 3), "evidence": self.evidence}


def _saturate(value, scale):
    """Map an unbounded quantity onto (-1, 1) without a hard cliff."""
    if not scale:
        return 0.0
    return math.tanh(value / scale)


# --- individual sources ---------------------------------------------------

def flow_signal(nodes):
    """Net accumulation vs distribution across tracked wallets.

    Uses wallet count as well as value: fifty wallets quietly accumulating
    is a different signal from one whale doing the same size, and value
    alone would hide that.
    """
    active = [n for n in nodes if n.get("inflow") or n.get("outflow")]
    if not active:
        return Signal("flow", 0.0, 0.0, "No transfer data yet - enrichment has not run")

    inflow = sum(n.get("inflow", 0) for n in active)
    outflow = sum(n.get("outflow", 0) for n in active)
    total = inflow + outflow
    if total <= 0:
        return Signal("flow", 0.0, 0.1, "No value moved across tracked wallets")

    value_bias = (inflow - outflow) / total
    accumulating = sum(1 for n in active if n.get("net_flow", 0) > 0)
    count_bias = (2 * accumulating / len(active)) - 1

    score = 0.6 * value_bias + 0.4 * count_bias
    # Confidence grows with sample size and saturates around 40 wallets.
    confidence = min(1.0, len(active) / 40)
    return Signal("flow", score, confidence,
                  f"{accumulating}/{len(active)} wallets net-accumulating; "
                  f"value bias {value_bias:+.0%}")


def positioning_signal(hl_scores):
    """Directional bias of tracked perps books, weighted by size.

    A $40M book leaning short says more than a $40k one, so bias is
    equity-weighted rather than counted per account.
    """
    accounts = [r for r in hl_scores.values() if r["features"]["position_count"] > 0]
    if not accounts:
        return Signal("positioning", 0.0, 0.0, "No open perps positions tracked")

    weighted, total_equity = 0.0, 0.0
    longs = shorts = 0
    for record in accounts:
        features = record["features"]
        equity = max(features["equity"], 1.0)
        weighted += features["net_bias"] * equity
        total_equity += equity
        if features["net_bias"] > 0.2:
            longs += 1
        elif features["net_bias"] < -0.2:
            shorts += 1

    score = weighted / total_equity if total_equity else 0.0
    confidence = min(1.0, len(accounts) / 15)
    return Signal("positioning", score, confidence,
                  f"{longs} long / {shorts} short across {len(accounts)} books "
                  f"(${total_equity:,.0f} equity), size-weighted bias {score:+.2f}")


def feed_signal(messages, source_trust=None):
    """Lexicon reading of ingested feed text.

    Deliberately damped: this repo's own analysis found the tracked feed's
    median call was 0.23x, so a source with measured poor reliability has
    its confidence cut rather than its sentiment inverted - claiming to know
    the sign of a bad signal is its own overreach.
    """
    if not messages:
        return Signal("feed", 0.0, 0.0, "No feed messages ingested")

    total, hits = 0.0, 0
    for message in messages:
        text = (message.get("text") or "").lower()
        if not text:
            continue
        for term, weight in BULLISH_TERMS.items():
            count = text.count(term)
            if count:
                total += weight * count
                hits += count
        for term, weight in BEARISH_TERMS.items():
            count = text.count(term)
            if count:
                total += weight * count
                hits += count

    if not hits:
        return Signal("feed", 0.0, 0.1, f"No directional terms in {len(messages)} message(s)")

    score = _saturate(total / max(1, len(messages)), 3.0)
    confidence = min(1.0, len(messages) / 200)
    if source_trust == "low":
        confidence *= 0.4
    return Signal("feed", score, confidence,
                  f"{hits} directional term(s) across {len(messages)} message(s)"
                  + (" (source reliability low - damped)" if source_trust == "low" else ""))


def price_signal(quotes):
    """Momentum from a price feed.

    `quotes` is a list of {symbol, change_24h, volume_24h}. Empty until a
    price adapter is configured; returns zero confidence rather than a
    fabricated neutral reading.
    """
    usable = [q for q in (quotes or []) if q.get("change_24h") is not None]
    if not usable:
        return Signal("price", 0.0, 0.0, "No price feed configured")

    # Volume-weighted so a thin token cannot dominate the reading.
    weighted = sum(q["change_24h"] * max(q.get("volume_24h") or 1, 1) for q in usable)
    volume = sum(max(q.get("volume_24h") or 1, 1) for q in usable)
    average = (weighted / volume) if volume else 0.0
    score = _saturate(average, 25.0)   # +-25% 24h move saturates
    up = sum(1 for q in usable if q["change_24h"] > 0)
    return Signal("price", score, min(1.0, len(usable) / 10),
                  f"{up}/{len(usable)} tokens up; volume-weighted 24h "
                  f"move {average:+.1f}%")


def social_signal(posts):
    """External social chatter.

    `posts` is a list of {text, weight}. Always empty unless a social
    adapter is configured - X's API is paid, so this returns zero confidence
    rather than implying coverage that does not exist.
    """
    if not posts:
        return Signal("social", 0.0, 0.0,
                      "No social adapter configured (X API requires a paid plan)")
    return feed_signal([{"text": p.get("text", "")} for p in posts])


# --- composite ------------------------------------------------------------

def composite(signals):
    """Confidence-weighted blend of every available signal."""
    numerator = denominator = 0.0
    for signal in signals:
        weight = SOURCE_WEIGHTS.get(signal.source, 0.5) * signal.confidence
        numerator += signal.score * weight
        denominator += weight

    score = (numerator / denominator) if denominator else 0.0
    if score > NEUTRAL_BAND:
        label = "bullish"
    elif score < -NEUTRAL_BAND:
        label = "bearish"
    else:
        label = "neutral"

    # Overall confidence is capped by how many sources actually reported.
    reporting = [s for s in signals if s.confidence > 0]
    coverage = len(reporting) / max(1, len(signals))

    return {
        "score": round(score, 3),
        "label": label,
        "confidence": round(min(1.0, denominator / 2.0) * coverage, 3),
        "coverage": round(coverage, 3),
        "reporting_sources": [s.source for s in reporting],
        "signals": [s.as_dict() for s in signals],
        "narrative": narrate(score, label, signals),
    }


def narrate(score, label, signals):
    """Plain-language summary, assembled from the signals themselves.

    This is the seam where an LLM narrator would slot in later: it would
    receive exactly these signals and return prose. Until then the summary
    is deterministic, which means it can never overstate what the data says.
    """
    reporting = sorted((s for s in signals if s.confidence > 0.05),
                       key=lambda s: -abs(s.score) * s.confidence)
    if not reporting:
        return "No sentiment sources are reporting yet."

    lead = reporting[0]
    direction = {"bullish": "leaning bullish", "bearish": "leaning bearish",
                 "neutral": "roughly neutral"}[label]
    parts = [f"Overall {direction} ({score:+.2f}), led by {lead.source}: {lead.evidence}."]

    disagreeing = [s for s in reporting[1:]
                   if s.score * lead.score < 0 and abs(s.score) > 0.15]
    if disagreeing:
        parts.append("Disagreement: " + "; ".join(
            f"{s.source} reads {s.score:+.2f}" for s in disagreeing[:2]) + ".")

    silent = [s.source for s in signals if s.confidence <= 0.05]
    if silent:
        parts.append(f"No data from: {', '.join(silent)}.")
    return " ".join(parts)


def analyze(nodes, hl_scores, messages=None, quotes=None, posts=None, source_trust=None):
    """Full sentiment read across every configured source."""
    return composite([
        flow_signal(nodes),
        positioning_signal(hl_scores),
        feed_signal(messages or [], source_trust),
        price_signal(quotes or []),
        social_signal(posts or []),
    ])


def per_narrative(kb, nodes, hl_scores, messages=None):
    """Sentiment scoped to each narrative's own wallets."""
    by_id = {n["id"]: n for n in nodes}
    out = {}
    for narrative in kb.narratives.values():
        members = [by_id[w] for w in narrative.get("linked_wallets", []) if w in by_id]
        if not members:
            continue
        addresses = {m["address"] for m in members if m.get("address")}
        scoped_hl = {a: r for a, r in hl_scores.items() if a in addresses}
        tokens = {t.lower() for t in narrative.get("tokens", [])}
        scoped_messages = [
            m for m in (messages or [])
            if tokens and any(t in (m.get("text") or "").lower() for t in tokens)
        ]
        out[narrative["key"]] = analyze(members, scoped_hl, scoped_messages)
    return out

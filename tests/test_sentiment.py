"""Sentiment engine tests.

The critical property is honesty about absence: a source with no data must
report zero confidence, not a neutral reading that dilutes the sources that
do have data.
"""
import pytest

from pipeline import sentiment


def node(net_flow=0.0, inflow=0.0, outflow=0.0):
    return {"net_flow": net_flow, "inflow": inflow, "outflow": outflow}


def hl(equity, net_bias, positions=1):
    return {"features": {"equity": equity, "net_bias": net_bias,
                         "position_count": positions}}


# --- absence is reported, not faked -------------------------------------

@pytest.mark.parametrize("fn,args", [
    (sentiment.flow_signal, ([],)),
    (sentiment.positioning_signal, ({},)),
    (sentiment.feed_signal, ([],)),
    (sentiment.price_signal, ([],)),
    (sentiment.social_signal, ([],)),
])
def test_missing_source_reports_zero_confidence(fn, args):
    signal = fn(*args)
    assert signal.confidence == 0.0
    assert signal.score == 0.0
    assert signal.evidence, "absence must still be explained"


def test_silent_sources_do_not_dilute_the_reading():
    """Three silent sources must not drag a strong signal toward neutral."""
    strong = sentiment.Signal("flow", 0.9, 1.0, "strong")
    silent = [sentiment.Signal(s, 0.0, 0.0, "no data")
              for s in ("feed", "price", "social")]
    result = sentiment.composite([strong] + silent)
    assert result["score"] == pytest.approx(0.9, abs=0.01)
    assert result["label"] == "bullish"
    assert result["coverage"] == pytest.approx(0.25)


def test_confidence_reflects_coverage():
    strong = sentiment.Signal("flow", 0.9, 1.0, "x")
    full = sentiment.composite([strong])
    partial = sentiment.composite([strong] + [sentiment.Signal("feed", 0, 0, "")] * 3)
    assert partial["confidence"] < full["confidence"]


# --- flow ----------------------------------------------------------------

def test_flow_reads_accumulation_as_bullish():
    nodes = [node(net_flow=5, inflow=5) for _ in range(10)]
    signal = sentiment.flow_signal(nodes)
    assert signal.score > 0.5


def test_flow_reads_distribution_as_bearish():
    nodes = [node(net_flow=-5, outflow=5) for _ in range(10)]
    assert sentiment.flow_signal(nodes).score < -0.5


def test_flow_counts_wallets_not_just_value():
    """One whale accumulating should not outvote many wallets distributing."""
    nodes = [node(net_flow=100, inflow=100)] + [node(net_flow=-5, outflow=5) for _ in range(9)]
    signal = sentiment.flow_signal(nodes)
    # Value bias is strongly positive but 9/10 wallets are selling, so the
    # blended reading must be pulled well below the value-only view.
    assert signal.score < 0.6


# --- positioning ---------------------------------------------------------

def test_positioning_is_equity_weighted():
    """A $40M short outweighs several tiny longs."""
    scores = {"a": hl(40_000_000, -1.0)}
    for i in range(5):
        scores[f"s{i}"] = hl(40_000, 1.0)
    assert sentiment.positioning_signal(scores).score < -0.5


def test_positioning_ignores_accounts_with_no_positions():
    assert sentiment.positioning_signal({"a": hl(1_000, 0.0, positions=0)}).confidence == 0.0


# --- feed ----------------------------------------------------------------

def test_feed_detects_direction():
    bull = sentiment.feed_signal([{"text": "smart money accumulating, bullish breakout"}] * 20)
    bear = sentiment.feed_signal([{"text": "rug pull, dumping, scam"}] * 20)
    assert bull.score > 0
    assert bear.score < 0


def test_low_trust_source_is_damped_not_inverted():
    """A source measured unreliable loses confidence; its sign is not flipped.

    Claiming to know the sign of a bad signal is its own overreach.
    """
    messages = [{"text": "smart money accumulating"}] * 100
    normal = sentiment.feed_signal(messages)
    damped = sentiment.feed_signal(messages, source_trust="low")
    assert damped.confidence < normal.confidence
    assert damped.score == pytest.approx(normal.score), "sign must be unchanged"


def test_feed_with_no_directional_terms():
    signal = sentiment.feed_signal([{"text": "block 12345 processed"}] * 5)
    assert signal.score == 0.0
    assert signal.confidence < 0.2


# --- composite -----------------------------------------------------------

def test_neutral_band():
    weak = sentiment.Signal("flow", 0.05, 1.0, "x")
    assert sentiment.composite([weak])["label"] == "neutral"


def test_score_is_bounded():
    extreme = [sentiment.Signal(s, 5.0, 1.0, "x") for s in sentiment.SOURCE_WEIGHTS]
    assert sentiment.composite(extreme)["score"] <= 1.0


def test_narrative_mentions_disagreement():
    signals = [sentiment.Signal("flow", 0.8, 1.0, "buying"),
               sentiment.Signal("positioning", -0.7, 1.0, "short")]
    assert "Disagreement" in sentiment.composite(signals)["narrative"]


def test_narrative_names_silent_sources():
    signals = [sentiment.Signal("flow", 0.8, 1.0, "buying"),
               sentiment.Signal("social", 0.0, 0.0, "none")]
    assert "No data from" in sentiment.composite(signals)["narrative"]


def test_analyze_runs_with_nothing_configured():
    result = sentiment.analyze([], {})
    assert result["label"] == "neutral"
    assert result["confidence"] == 0.0
    assert "No sentiment sources" in result["narrative"]

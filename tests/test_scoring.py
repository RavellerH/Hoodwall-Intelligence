"""Regression tests for the deterministic scorer.

These pin the behaviour that matters: archetypes must rank in the right
order, and label rules must not fire on wallets they do not describe. The
bot rules in particular are easy to make too loose - random transaction
arrival looks identical to unstructured human activity, so a rule keyed on
gap regularity alone will label every human a bot.
"""
import random
from datetime import datetime, timedelta, timezone

import pytest

from pipeline import features, scoring

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def make_events(count, days, counterparties, *, contract=True, value=1.0,
                regular=False, hours=None, success=True, seed=42,
                methods=("swap", "mint", "stake", "claim")):
    """Synthesize a wallet's event history with controllable behaviour."""
    rng = random.Random(seed)
    events = []
    for i in range(count):
        if regular:
            when = NOW - timedelta(days=days) + timedelta(
                seconds=i * (days * 86400 / max(count, 1))
            )
        else:
            when = NOW - timedelta(days=rng.uniform(0, days))
            if hours:
                when = when.replace(hour=rng.choice(hours))
        events.append({
            "event_time": when.isoformat(),
            "counterparty": f"0x{rng.randint(1, counterparties):040x}",
            "to_is_contract": contract,
            "value_native": value,
            "direction": rng.choice(["in", "out"]),
            "success": success,
            "method": rng.choice(methods),
        })
    return events


ARCHETYPES = {
    "noise": make_events(2, 5, 1),
    "dust_spammer": make_events(60, 30, 1, contract=False, value=0.0),
    "failing_bot": make_events(80, 20, 3, success=False),
    "regular_bot": make_events(300, 60, 4, regular=True),
    "casual_human": make_events(25, 90, 12, value=0.5, hours=[14, 15, 16, 19, 20, 21]),
    "defi_trader": make_events(220, 120, 45, value=8.0, hours=list(range(9, 23))),
    "whale": make_events(90, 100, 25, value=400.0),
    "fresh": make_events(40, 20, 15, value=3.0),
}


@pytest.fixture(scope="module")
def profiled():
    """Feature vectors, scores and labels for every archetype."""
    vectors = {
        name: features.extract("0xabc", events, now=NOW)
        for name, events in ARCHETYPES.items()
    }
    p95 = scoring.percentile([f["volume_native"] for f in vectors.values()], 95)
    return {
        name: {
            "features": f,
            "score": scoring.score_wallet(f),
            "labels": {
                lab["name"]: lab["confidence"]
                for lab in scoring.label_wallet(f, scoring.score_wallet(f)["smart_score"], p95)
            },
        }
        for name, f in vectors.items()
    }


def test_scores_are_bounded(profiled):
    for name, data in profiled.items():
        assert 0.0 <= data["score"]["smart_score"] <= 100.0, name


def test_archetype_ranking(profiled):
    """A sustained, diverse, high-volume trader must outrank noise."""
    def score(name):
        return profiled[name]["score"]["smart_score"]

    assert score("defi_trader") > score("casual_human")
    assert score("casual_human") > score("dust_spammer")
    assert score("dust_spammer") > score("noise")
    assert score("whale") > score("fresh")


def test_tiers(profiled):
    assert profiled["defi_trader"]["score"]["tier"] == "elite"
    assert profiled["noise"]["score"]["tier"] == "archive"
    assert profiled["dust_spammer"]["score"]["tier"] == "archive"


def test_noise_is_capped_and_labeled(profiled):
    noise = profiled["noise"]
    assert noise["score"]["insufficient_history"] is True
    assert noise["score"]["smart_score"] <= scoring.NOISE_SCORE_CAP
    assert "noise" in noise["labels"]


def test_noise_suppresses_other_labels(profiled):
    """A wallet with no meaningful history gets characterized once, not eight times."""
    assert list(profiled["noise"]["labels"]) == ["noise"]


def test_failure_penalty_applies(profiled):
    penalties = profiled["failing_bot"]["score"]["penalties"]
    assert any(p["name"] == "high_failure_rate" for p in penalties)


def test_dust_spammer_penalties(profiled):
    names = {p["name"] for p in profiled["dust_spammer"]["score"]["penalties"]}
    assert "single_counterparty" in names
    assert "no_value_moved" in names


def test_metronomic_timing_is_labeled_a_bot(profiled):
    """A fixed-interval bot must be caught even though its rigid cycle
    touches only a handful of distinct clock hours."""
    assert "trading_bot" in profiled["regular_bot"]["labels"]


@pytest.mark.parametrize("name", ["casual_human", "defi_trader", "whale", "fresh"])
def test_non_bots_are_not_labeled_bots(profiled, name):
    """Regression: random inter-transaction gaps are NOT bot evidence."""
    assert "trading_bot" not in profiled[name]["labels"]
    assert "mev_bot" not in profiled[name]["labels"]


def test_whale_label_needs_top_volume(profiled):
    assert "whale" in profiled["whale"]["labels"]
    assert "whale" not in profiled["casual_human"]["labels"]


def test_components_are_traceable(profiled):
    """Every score must decompose into its weighted components."""
    for name, data in profiled.items():
        result = data["score"]
        recomputed = sum(
            scoring.WEIGHTS[k] * v for k, v in result["components"].items()
        ) * 100
        assert result["base_score"] == pytest.approx(round(recomputed, 1), abs=0.11), name


def test_weights_sum_to_one():
    assert sum(scoring.WEIGHTS.values()) == pytest.approx(1.0)


def test_empty_wallet_does_not_crash():
    f = features.extract("0xabc", [], now=NOW)
    result = scoring.score_wallet(f)
    assert result["smart_score"] == 0.0
    assert result["tier"] == "archive"


def test_accumulator_and_distributor_are_exclusive():
    """A wallet cannot be both net-buying and net-selling."""
    inflow = [{"event_time": NOW.isoformat(), "counterparty": f"0x{i:040x}",
               "to_is_contract": True, "value_native": 10.0,
               "direction": "in", "success": True, "method": "swap"}
              for i in range(10)]
    f = features.extract("0xabc", inflow, now=NOW)
    labels = {l["name"] for l in scoring.label_wallet(f, 70)}
    assert "accumulator" in labels
    assert "distributor" not in labels

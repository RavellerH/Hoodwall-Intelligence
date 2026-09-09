"""Hyperliquid adapter tests.

The live API is not reachable from CI here, so these pin behaviour against
payloads shaped like the real clearinghouseState / userFills responses.
The scoring rules matter most: perps are leveraged by design, so leverage
must not be treated as inherently bad, and win rate alone must not imply
profitability - this repo's own data showed those two correlating at
r = -0.001.
"""
import pytest

from pipeline.adapters import hyperliquid as hl

WHALE_STATE = {
    "marginSummary": {"accountValue": "42980000", "totalNtlPos": "99220000",
                      "totalMarginUsed": "12000000"},
    "withdrawable": "8000000",
    "assetPositions": [
        {"position": {"coin": "ETH", "szi": "-18000", "positionValue": "70820000",
                      "entryPx": "3900", "unrealizedPnl": "-13170000",
                      "returnOnEquity": "-0.31", "leverage": {"type": "cross", "value": "3"},
                      "liquidationPx": "5200"}},
        {"position": {"coin": "BTC", "szi": "300", "positionValue": "28400000",
                      "entryPx": "94000", "unrealizedPnl": "420000",
                      "returnOnEquity": "0.05", "leverage": {"type": "cross", "value": "2"}}},
    ],
}

FILLS = [
    {"coin": "ETH", "closedPnl": "1500", "fee": "12", "time": 1757000000000, "sz": "10"},
    {"coin": "ETH", "closedPnl": "-400", "fee": "9",  "time": 1757200000000, "sz": "5"},
    {"coin": "BTC", "closedPnl": "2200", "fee": "20", "time": 1757400000000, "sz": "2"},
    {"coin": "SOL", "closedPnl": "0",    "fee": "3",  "time": 1757500000000, "sz": "50"},
]


@pytest.fixture
def whale():
    return hl.extract("0xABC", WHALE_STATE, FILLS)


# --- extraction -----------------------------------------------------------

def test_address_lowercased(whale):
    assert whale["address"] == "0xabc"


def test_equity_and_notional(whale):
    assert whale["equity"] == 42980000
    assert whale["notional"] == 99220000


def test_direction_from_sign_of_size(whale):
    """szi is negative for shorts - the only place direction comes from."""
    directions = {p["coin"]: p["direction"] for p in whale["positions"]}
    assert directions["ETH"] == "short"
    assert directions["BTC"] == "long"


def test_net_bias_and_concentration(whale):
    gross = 70820000 + 28400000
    assert whale["net_bias"] == pytest.approx((28400000 - 70820000) / gross, abs=1e-3)
    assert whale["concentration"] == pytest.approx(70820000 / gross, abs=1e-3)


def test_account_leverage_is_notional_over_equity(whale):
    assert whale["account_leverage"] == pytest.approx(99220000 / 42980000, abs=1e-3)


def test_win_rate_counts_only_closing_fills(whale):
    """A fill with closedPnl == 0 is not a close and must not dilute win rate."""
    assert whale["close_count"] == 3
    assert whale["win_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_realized_pnl_and_fees(whale):
    assert whale["realized_pnl"] == pytest.approx(3300)
    assert whale["fees_paid"] == pytest.approx(44)


def test_zero_size_positions_are_dropped():
    f = hl.extract("0x1", {"marginSummary": {"accountValue": "100"},
                           "assetPositions": [{"position": {"coin": "ETH", "szi": "0"}}]}, [])
    assert f["position_count"] == 0


def test_empty_account_does_not_crash():
    f = hl.extract("0x1", {}, [])
    assert f["equity"] == 0
    assert f["win_rate"] is None
    assert hl.score(f)["smart_score"] >= 0


def test_malformed_numbers_degrade():
    f = hl.extract("0x1", {"marginSummary": {"accountValue": "not-a-number"}}, [])
    assert f["equity"] == 0


# --- scoring --------------------------------------------------------------

def test_score_bounded(whale):
    assert 0 <= hl.score(whale)["smart_score"] <= 100


def test_weights_sum_to_one():
    assert sum(hl.WEIGHTS.values()) == pytest.approx(1.0)


def test_components_decompose_to_score(whale):
    result = hl.score(whale)
    recomputed = sum(hl.WEIGHTS[k] * v for k, v in result["components"].items()) * 100
    assert result["smart_score"] == pytest.approx(round(recomputed, 1), abs=0.11)


@pytest.mark.parametrize("leverage,expected", [(2, 1.0), (5, 1.0), (20, 0.0), (35, 0.0)])
def test_risk_control_leverage_bands(leverage, expected):
    """Moderate leverage is fine; 20x+ is one wick from liquidation."""
    f = hl.extract("0x1", {"marginSummary": {
        "accountValue": "1000", "totalNtlPos": str(1000 * leverage)}}, [])
    assert hl.components(f)["risk_control"] == pytest.approx(expected, abs=0.01)


def test_no_open_risk_is_neutral_not_zero():
    f = hl.extract("0x1", {"marginSummary": {"accountValue": "1000", "totalNtlPos": "0"}}, [])
    assert hl.components(f)["risk_control"] == 0.5


def test_empty_account_is_capped():
    f = hl.extract("0x1", {}, [])
    assert hl.score(f)["insufficient_history"] is True
    assert hl.score(f)["smart_score"] <= 20


# --- labels ---------------------------------------------------------------

def test_whale_and_trader_labels(whale):
    names = {l["name"] for l in hl.label(whale)}
    assert "whale" in names
    assert "perps_trader" in names


def test_short_bias_detected():
    f = hl.extract("0x1", {
        "marginSummary": {"accountValue": "1000", "totalNtlPos": "5000"},
        "assetPositions": [{"position": {"coin": "ETH", "szi": "-10",
                                         "positionValue": "5000"}}]}, [])
    names = {l["name"] for l in hl.label(f)}
    assert "short_bias" in names
    assert "long_bias" not in names


def test_market_neutral_detected():
    f = hl.extract("0x1", {
        "marginSummary": {"accountValue": "1000", "totalNtlPos": "2000"},
        "assetPositions": [
            {"position": {"coin": "ETH", "szi": "10", "positionValue": "1000"}},
            {"position": {"coin": "BTC", "szi": "-1", "positionValue": "1000"}}]}, [])
    assert "market_neutral" in {l["name"] for l in hl.label(f)}


def test_high_leverage_labeled():
    f = hl.extract("0x1", {"marginSummary": {
        "accountValue": "1000", "totalNtlPos": "25000"}}, [])
    labels = {l["name"]: l for l in hl.label(f)}
    assert "high_leverage" in labels
    assert "liquidation risk" in labels["high_leverage"]["evidence"]


def test_losing_account_labeled_unprofitable():
    fills = [{"coin": "ETH", "closedPnl": "-100", "fee": "1", "time": 1757000000000}
             for _ in range(12)]
    f = hl.extract("0x1", {"marginSummary": {"accountValue": "5000"}}, fills)
    assert "unprofitable" in {l["name"] for l in hl.label(f)}


def test_high_winrate_but_losing_is_not_smart_money():
    """Win rate alone must never imply profitability."""
    fills = ([{"coin": "ETH", "closedPnl": "1", "fee": "0", "time": 1757000000000}] * 9 +
             [{"coin": "ETH", "closedPnl": "-500", "fee": "0", "time": 1757000000000}])
    f = hl.extract("0x1", {"marginSummary": {"accountValue": "5000"}}, fills)
    assert f["win_rate"] == pytest.approx(0.9)
    assert f["realized_pnl"] < 0
    names = {l["name"] for l in hl.label(f)}
    assert "smart_money" not in names
    assert "unprofitable" in names

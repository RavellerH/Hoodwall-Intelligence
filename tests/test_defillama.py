"""DefiLlama adapter tests.

Network to api.llama.fi / coins.llama.fi is unreachable from this sandbox
(same as Blockscout and Hyperliquid earlier in this project), so these pin
behaviour against payloads shaped like DefiLlama's documented responses.
"""
import pytest

from pipeline.adapters import defillama as dl


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.reason = {404: "Not Found", 429: "Too Many Requests", 200: "OK"}.get(status_code, "?")
        self._payload = payload if payload is not None else {}
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture(autouse=True)
def reset_session(monkeypatch):
    monkeypatch.setattr(dl, "_session", None)
    monkeypatch.setattr(dl, "DELAY", 0)
    # Real retry backoff sleeps 1s/2s/4s; tests exercising the retry path
    # (a transient 5xx) do not need to actually wait for it.
    monkeypatch.setattr("time.sleep", lambda *_: None)


def _stub(monkeypatch, response_for_url):
    class FakeSession:
        headers = {}

        def get(self, url, params=None, timeout=None):
            return response_for_url(url)

    monkeypatch.setattr(dl, "_get_session", lambda: FakeSession())


CHAINS_PAYLOAD = [
    {"name": "Ethereum", "tvl": 55_000_000_000.0, "gecko_id": "ethereum",
     "tokenSymbol": "ETH", "chainId": 1},
    {"name": "Solana", "tvl": 8_200_000_000.0, "gecko_id": "solana",
     "tokenSymbol": "SOL", "chainId": None},
    {"name": "Arbitrum", "tvl": 3_100_000_000.0, "gecko_id": "arbitrum",
     "tokenSymbol": "ARB", "chainId": 42161},
]


def test_get_chains_returns_list(monkeypatch):
    _stub(monkeypatch, lambda url: FakeResponse(200, CHAINS_PAYLOAD))
    assert dl.get_chains() == CHAINS_PAYLOAD


def test_chain_tvl_by_name_case_insensitive(monkeypatch):
    _stub(monkeypatch, lambda url: FakeResponse(200, CHAINS_PAYLOAD))
    result = dl.chain_tvl_by_name(["ethereum", "Solana", "ARBITRUM"])
    assert result["ethereum"]["tvl"] == 55_000_000_000.0
    assert result["Solana"]["tvl"] == 8_200_000_000.0
    assert result["ARBITRUM"]["tvl"] == 3_100_000_000.0


def test_chain_not_tracked_maps_to_none(monkeypatch):
    """Bitcoin and brand-new L2s are not DeFi chains - must be None, not absent."""
    _stub(monkeypatch, lambda url: FakeResponse(200, CHAINS_PAYLOAD))
    result = dl.chain_tvl_by_name(["Bitcoin", "Ethereum"])
    assert result["Bitcoin"] is None
    assert result["Ethereum"] is not None


def test_chain_tvl_failure_returns_all_none(monkeypatch):
    """A dead endpoint must not crash the market stage - just no data."""
    _stub(monkeypatch, lambda url: FakeResponse(500))
    result = dl.chain_tvl_by_name(["Ethereum"])
    assert result == {"Ethereum": None}


CURRENT_PRICES = {
    "coins": {
        "coingecko:bitcoin": {"price": 95000.0, "symbol": "BTC", "confidence": 0.99},
        "coingecko:ethereum": {"price": 3800.0, "symbol": "ETH", "confidence": 0.99},
    }
}
HISTORICAL_PRICES = {
    "coins": {
        "coingecko:bitcoin": {"price": 92000.0, "symbol": "BTC"},
        "coingecko:ethereum": {"price": 3900.0, "symbol": "ETH"},
    }
}


def test_get_current_prices_shape(monkeypatch):
    _stub(monkeypatch, lambda url: FakeResponse(200, CURRENT_PRICES))
    result = dl.get_current_prices(["coingecko:bitcoin", "coingecko:ethereum"])
    assert result["coingecko:bitcoin"]["price"] == 95000.0


def test_get_current_prices_empty_input_short_circuits(monkeypatch):
    calls = []
    _stub(monkeypatch, lambda url: (calls.append(url), FakeResponse(200, {}))[1])
    assert dl.get_current_prices([]) == {}
    assert calls == []


def test_price_snapshot_computes_24h_change(monkeypatch):
    def respond(url):
        return FakeResponse(200, HISTORICAL_PRICES if "historical" in url else CURRENT_PRICES)
    _stub(monkeypatch, respond)

    snap = dl.price_snapshot(["coingecko:bitcoin", "coingecko:ethereum"])
    assert snap["coingecko:bitcoin"]["price"] == 95000.0
    # (95000 - 92000) / 92000 * 100
    assert snap["coingecko:bitcoin"]["change_24h"] == pytest.approx(3.261, abs=0.01)
    # ETH went down: (3800 - 3900) / 3900 * 100
    assert snap["coingecko:ethereum"]["change_24h"] == pytest.approx(-2.564, abs=0.01)


def test_price_snapshot_survives_historical_failure(monkeypatch):
    """Momentum is a bonus; losing the historical call must not lose the price."""
    def respond(url):
        if "historical" in url:
            return FakeResponse(500)
        return FakeResponse(200, CURRENT_PRICES)
    _stub(monkeypatch, respond)

    snap = dl.price_snapshot(["coingecko:bitcoin"])
    assert snap["coingecko:bitcoin"]["price"] == 95000.0
    assert snap["coingecko:bitcoin"]["change_24h"] is None


def test_price_snapshot_empty_coin_ids():
    assert dl.price_snapshot([]) == {}


def test_permanent_error_not_retried(monkeypatch):
    calls = []
    def respond(url):
        calls.append(url)
        return FakeResponse(404)
    _stub(monkeypatch, respond)
    with pytest.raises(dl.DefiLlamaError):
        dl.get_current_prices(["coingecko:nonexistent"])
    assert len(calls) == 1

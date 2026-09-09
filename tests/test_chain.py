"""Tests for the Blockscout client's failure handling.

The first production run spent 183 seconds retrying a 403 sixty times and
then reported success. These tests pin the two fixes: permanent refusals
are not retried, and they are distinguishable from transient faults.
"""
import pytest
import requests

from pipeline import chain


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.reason = {403: "Forbidden", 401: "Unauthorized",
                       400: "Bad Request", 200: "OK"}.get(status_code, "?")
        self._payload = payload or {}
        self.headers = {}
        self.text = str(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture(autouse=True)
def reset_session(monkeypatch):
    monkeypatch.setattr(chain, "_session", None)


def _stub(monkeypatch, response, counter):
    class FakeSession:
        headers = {}

        def get(self, url, params=None, timeout=None):
            counter.append(url)
            return response

    monkeypatch.setattr(chain, "_get_session", lambda: FakeSession())


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_are_not_retried(monkeypatch, status):
    """A gated endpoint must fail on the first attempt, not after N retries."""
    calls = []
    _stub(monkeypatch, FakeResponse(status), calls)
    with pytest.raises(chain.ChainAuthError):
        chain.get_address("0x" + "a" * 40)
    assert len(calls) == 1


def test_auth_error_message_names_the_fix(monkeypatch):
    calls = []
    _stub(monkeypatch, FakeResponse(403), calls)
    with pytest.raises(chain.ChainAuthError) as exc:
        chain.get_address("0x" + "a" * 40)
    assert "BLOCKSCOUT_API_KEY" in str(exc.value)


def test_auth_error_is_a_chain_error():
    """Callers catching ChainError must still catch auth failures."""
    assert issubclass(chain.ChainAuthError, chain.ChainError)


def test_other_4xx_not_retried(monkeypatch):
    calls = []
    _stub(monkeypatch, FakeResponse(400), calls)
    with pytest.raises(chain.ChainError):
        chain.get_address("0x" + "a" * 40)
    assert len(calls) == 1


def test_404_returns_none(monkeypatch):
    """An address the chain has never seen is normal, not an error."""
    calls = []
    _stub(monkeypatch, FakeResponse(404), calls)
    assert chain.get_address("0x" + "a" * 40) is None


def test_success_returns_payload(monkeypatch):
    calls = []
    _stub(monkeypatch, FakeResponse(200, {"is_contract": False}), calls)
    monkeypatch.setattr(chain.config, "BLOCKSCOUT_DELAY", 0)
    assert chain.get_address("0x" + "a" * 40) == {"is_contract": False}


def test_default_base_is_the_pro_api():
    """Robinhood Chain is only reachable through the multichain Pro API."""
    assert "api.blockscout.com" in chain.config.BLOCKSCOUT_BASE
    assert "4663" in chain.config.BLOCKSCOUT_BASE

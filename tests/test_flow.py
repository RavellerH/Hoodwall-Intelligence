"""Flow-tracing tests.

These pin the distinctions that make a cluster believable rather than
merely large: a contract is not a wallet, a busy address is a service, a
CEX deposit address is exclusive to its user without belonging to them,
and "its only funder is the seed" may only be claimed when the whole
inbound history was actually read.
"""
import pytest

from pipeline import chain, flow

CHAINS = {"testnet": "https://example.invalid/api/v2"}

SEED = "0x" + "a1" * 20
SIBLING = "0x" + "b2" * 20
DEPOSIT = "0x" + "c3" * 20
HOTWALLET = "0x" + "d4" * 20
ROUTER = "0x" + "e5" * 20
SEED2 = "0x" + "f6" * 20


def tx(sender, receiver, value="1000000000000000000", when="2026-09-01T00:00:00.000000Z",
       tx_hash="0xdead"):
    return {"from": {"hash": sender, "is_contract": False},
            "to": {"hash": receiver, "is_contract": receiver == ROUTER},
            "value": value, "timestamp": when, "hash": tx_hash}


class FakeChain:
    """A tiny in-memory chain: address -> history, plus a contract flag."""

    def __init__(self, histories, contracts=(), complete=True):
        self.histories = histories
        self.contracts = set(contracts)
        self.complete = complete

    def get_address(self, address, base=None):
        return {"is_contract": address in self.contracts,
                "transactions_count": len(self.histories.get(address, [])),
                "coin_balance": "0"}

    def get_paged(self, path, params=None, base=None, max_pages=1):
        address = path.split("/")[1]
        if path.endswith("token-transfers"):
            return [], True
        return list(self.histories.get(address, [])), self.complete


@pytest.fixture
def wire(monkeypatch):
    def _wire(fake):
        monkeypatch.setattr(chain, "get_address", fake.get_address)
        monkeypatch.setattr(chain, "get_paged", fake.get_paged)
        return fake
    return _wire


def test_contract_counterparty_is_not_a_wallet(wire):
    fake = wire(FakeChain({SEED: [tx(SEED, ROUTER)], ROUTER: [tx(SEED, ROUTER)]},
                          contracts=[ROUTER]))
    kind, _ = flow.classify(ROUTER, "base", [SEED])
    assert kind == "contract"
    report = flow.trace([SEED], chains=CHAINS)
    assert report["links"] == {}
    assert ROUTER in report["skipped"]["contract"]
    assert fake  # the fake really was used


def test_busy_address_is_a_service(wire):
    peers = ["0x%040x" % i for i in range(flow.SERVICE_DEGREE + 5)]
    wire(FakeChain({HOTWALLET: [tx(p, HOTWALLET) for p in peers]}))
    kind, _ = flow.classify(HOTWALLET, "base", [SEED])
    assert kind == "service"


def test_deposit_address_is_excluded_but_shared_use_is_reported(wire):
    """One deposit address funded by two seeds means one exchange account."""
    history = {
        SEED: [tx(SEED, DEPOSIT)],
        SEED2: [tx(SEED2, DEPOSIT)],
        # Receives from both seeds, forwards everything to the hot wallet,
        # never sends back: a deposit sweep, not a sibling wallet.
        DEPOSIT: [tx(SEED, DEPOSIT), tx(SEED2, DEPOSIT), tx(DEPOSIT, HOTWALLET)],
    }
    wire(FakeChain(history))
    assert flow.classify(DEPOSIT, "base", [SEED, SEED2])[0] == "deposit"

    report = flow.trace([SEED, SEED2], chains=CHAINS)
    assert DEPOSIT in report["skipped"]["deposit"]
    assert report["links"] == {}
    shared = report["shared"]
    assert len(shared) == 1
    assert shared[0]["kind"] == "shared_deposit"
    assert sorted(shared[0]["addresses"]) == sorted([SEED, SEED2])


def test_sole_funder_and_round_trip_is_probable(wire):
    history = {
        SEED: [tx(SEED, SIBLING), tx(SIBLING, SEED)],
        SIBLING: [tx(SEED, SIBLING), tx(SIBLING, SEED)],
    }
    wire(FakeChain(history))
    report = flow.trace([SEED], chains=CHAINS)
    link = report["links"][f"testnet:{SIBLING}"]
    assert link["verdict"] == "probable"
    assert link["score"] >= flow.PROBABLE
    assert any("both ways" in e for e in link["evidence"])
    assert any("every inbound transfer" in e for e in link["evidence"])


def test_partial_history_never_claims_sole_funding(wire):
    """Blockscout still had a next page, so exclusivity is unknowable."""
    history = {
        SEED: [tx(SEED, SIBLING), tx(SIBLING, SEED)],
        SIBLING: [tx(SEED, SIBLING), tx(SIBLING, SEED)],
    }
    wire(FakeChain(history, complete=False))
    report = flow.trace([SEED], chains=CHAINS)
    link = report["links"][f"testnet:{SIBLING}"]
    assert not any("every inbound" in e for e in link["evidence"])
    assert link["verdict"] == "possible"


def test_one_way_payment_is_not_a_link(wire):
    """Paying someone once, with them dealing elsewhere, is commerce."""
    other = "0x" + "07" * 20
    history = {
        SEED: [tx(SEED, SIBLING)],
        SIBLING: [tx(SEED, SIBLING), tx(other, SIBLING), tx(SIBLING, other)],
    }
    wire(FakeChain(history))
    report = flow.trace([SEED], chains=CHAINS)
    assert report["links"] == {}


def test_probe_reports_where_a_seed_is_active(wire):
    wire(FakeChain({SEED: [tx(SEED, SIBLING)]}))
    seen = flow.probe(SEED, {"one": "https://a.invalid", "two": "https://b.invalid"})
    assert set(seen) == {"one", "two"}
    assert seen["one"]["transactions"] == 1


def test_evm_chains_excludes_venues_without_an_explorer():
    chains = flow.evm_chains()
    assert "hyperliquid" not in chains, "perps venue has no transfer graph to walk"
    assert "ethereum" in chains

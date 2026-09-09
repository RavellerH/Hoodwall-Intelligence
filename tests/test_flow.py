"""Flow-tracing tests.

These pin the distinctions that make a cluster believable rather than
merely large: a contract is not a wallet, a busy address is a service, a
CEX deposit address is exclusive to its user without belonging to them,
and "its only funder is the seed" may only be claimed when the whole
inbound history was actually read.
"""
import pytest

from pipeline import chain, config, flow, store

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


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    """Tracing now persists a deposit registry, so tests must not write to
    the repo's own data/ - an earlier version committed fixture addresses."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


class FakeKB:
    """Just enough knowledge base to answer role_of()."""

    def __init__(self, roles=None):
        self.roles = {k.lower(): v for k, v in (roles or {}).items()}

    def role_of(self, address, chain=None):
        return self.roles.get((address or "").lower())


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
    report = flow.trace([SEED], chains=CHAINS, kb=FakeKB())
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

    report = flow.trace([SEED, SEED2], chains=CHAINS, kb=FakeKB())
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
    report = flow.trace([SEED], chains=CHAINS, kb=FakeKB())
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
    report = flow.trace([SEED], chains=CHAINS, kb=FakeKB())
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
    report = flow.trace([SEED], chains=CHAINS, kb=FakeKB())
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


def count_address_calls(fake, monkeypatch):
    """Count chain.get_address calls, patching the module the code calls.

    Wrapping fake.get_address after wire() does nothing: monkeypatch has
    already bound the original method onto the chain module, so the wrapper
    is never reached and a "no calls were made" assertion passes vacuously.
    """
    calls = []

    def counting(address, base=None):
        calls.append(address)
        return fake.get_address(address, base=base)
    monkeypatch.setattr(chain, "get_address", counting)
    return calls


def test_curated_role_beats_the_heuristic(wire, monkeypatch):
    """A registered router is a contract even though nothing on chain says so."""
    fake = wire(FakeChain({ROUTER: [tx(SEED, ROUTER)]}))
    calls = count_address_calls(fake, monkeypatch)

    kind, detail = flow.classify(ROUTER, "base", [SEED],
                                 kb=FakeKB({ROUTER: "router"}), chain_key="testnet")
    assert kind == "contract"
    assert detail["role"] == "router"
    assert calls == [], "a curated answer must cost no API call"


def test_mixer_is_its_own_class_not_a_wallet(wire):
    wire(FakeChain({}))
    kind, _ = flow.classify(HOTWALLET, "base", [SEED],
                            kb=FakeKB({HOTWALLET: "mixer"}), chain_key="testnet")
    assert kind == "mixer"


def test_deposit_registry_accumulates_across_runs(wire):
    """The funder list must merge, not replace - that is the whole point."""
    wire(FakeChain({}))
    flow.record_deposit("testnet", DEPOSIT, SEED)
    funders = flow.record_deposit("testnet", DEPOSIT, SEED2)
    assert sorted(funders) == sorted([SEED.lower(), SEED2.lower()])
    assert sorted(flow.deposit_funders("testnet", DEPOSIT)) == sorted(funders)
    assert [r["address"] for r in flow.known_deposits("testnet")] == [DEPOSIT.lower()]
    assert flow.known_deposits("otherchain") == []


def test_reverse_lookup_finds_a_wallet_no_trace_walked_to(wire):
    """The registry running backwards: a stranger funding a known deposit
    address is the same exchange account, and so the same person."""
    stranger = "0x" + "9a" * 20
    wire(FakeChain({DEPOSIT: [tx(SEED, DEPOSIT), tx(stranger, DEPOSIT),
                              tx(DEPOSIT, HOTWALLET)]}))
    flow.record_deposit("testnet", DEPOSIT, SEED)

    report = {"links": {}}
    found = flow.expand_from_deposits(CHAINS, [SEED], kb=FakeKB(),
                                      report=report)
    assert [f["address"] for f in found] == [stranger]
    link = report["links"][f"testnet:{stranger}"]
    assert link["verdict"] == "probable"
    assert link["linked_to"] == SEED.lower()
    assert "already attributed" in link["evidence"][0]


def test_reverse_lookup_ignores_deposits_we_cannot_attribute(wire):
    """A deposit address with no seed among its funders is somebody else's."""
    wire(FakeChain({DEPOSIT: [tx(SEED2, DEPOSIT)]}))
    flow.record_deposit("testnet", DEPOSIT, SEED2)
    assert flow.expand_from_deposits(CHAINS, [SEED], kb=FakeKB()) == []


# --- funding sources ------------------------------------------------------

FUNDER = "0x" + "11" * 20
EXCHANGE = "0x" + "22" * 20
BRIDGE = "0x" + "33" * 20


def test_hyperliquid_is_redirected_to_its_on_ramp():
    """The venue has no transfer graph; its Arbitrum on-ramp does."""
    assert flow.on_ramp_chain("hyperliquid") == "arbitrum"
    assert flow.on_ramp_chain("base") == "base"


def test_funding_sources_rank_by_value_and_name_the_origin(wire):
    history = {
        SEED: [tx(FUNDER, SEED, value="5000000000000000000"),
               tx(EXCHANGE, SEED, value="1000000000000000000"),
               tx(SEED, SIBLING, value="2000000000000000000")],
        FUNDER: [tx(FUNDER, SEED)],
        EXCHANGE: [tx(EXCHANGE, SEED)],
    }
    wire(FakeChain(history))
    result = flow.funding_sources(SEED, "testnet", "base",
                                  kb=FakeKB({EXCHANGE: "cex_hot"}))
    assert [r["address"] for r in result["sources"]] == [FUNDER, EXCHANGE]
    assert result["sources"][0]["total"] == 5.0
    assert result["sources"][0]["origin"] == "sent by another wallet"
    assert "exchange" in result["sources"][1]["origin"]
    # Outbound is what the wallet spent; it says nothing about its origin.
    assert SIBLING not in [r["address"] for r in result["sources"]]


def test_first_funder_is_withheld_on_a_truncated_history(wire):
    history = {SEED: [tx(FUNDER, SEED)], FUNDER: [tx(FUNDER, SEED)]}
    wire(FakeChain(history, complete=False))
    result = flow.funding_sources(SEED, "testnet", "base", kb=FakeKB())
    assert result["complete"] is False
    assert result["first_funder"] is None, "the oldest page is not the oldest transfer"

    wire(FakeChain(history))
    assert flow.funding_sources(SEED, "testnet", "base",
                                kb=FakeKB())["first_funder"] == FUNDER


def test_a_mixer_source_says_the_trail_ends(wire):
    wire(FakeChain({SEED: [tx(HOTWALLET, SEED)], HOTWALLET: []}))
    result = flow.funding_sources(SEED, "testnet", "base",
                                  kb=FakeKB({HOTWALLET: "mixer"}))
    assert "trail ends" in result["sources"][0]["origin"]


def test_one_wallet_funding_two_tracked_accounts_is_reported(wire):
    history = {
        SEED: [tx(FUNDER, SEED)],
        SEED2: [tx(FUNDER, SEED2)],
        FUNDER: [tx(FUNDER, SEED), tx(FUNDER, SEED2)],
    }
    wire(FakeChain(history))
    report = flow.trace_funding([(SEED, "testnet"), (SEED2, "testnet")],
                                chains=CHAINS, kb=FakeKB())
    assert len(report["shared_funders"]) == 1
    assert report["shared_funders"][0]["funder"] == FUNDER
    assert len(report["shared_funders"][0]["funded"]) == 2


def test_classification_is_shared_across_wallets_in_one_run(wire, monkeypatch):
    """Funding sources overlap by design; classifying each one per wallet
    would spend the budget re-deriving the same answer."""
    history = {
        SEED: [tx(EXCHANGE, SEED)],
        SEED2: [tx(EXCHANGE, SEED2)],
        EXCHANGE: [tx(EXCHANGE, SEED)],
    }
    fake = wire(FakeChain(history))
    calls = count_address_calls(fake, monkeypatch)

    flow.trace_funding([(SEED, "testnet"), (SEED2, "testnet")],
                       chains=CHAINS, kb=FakeKB())
    assert calls.count(EXCHANGE) == 1

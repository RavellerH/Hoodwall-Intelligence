"""Relationship graph tests.

The load-bearing property is that evidence and inference never blend: a
transfer edge means value actually moved, a behavioural edge means two
wallets share an attribute. If those merged, inference would read as proof.
"""
import pytest

from pipeline import graph


class FakeKB:
    def __init__(self, wallets, narratives=None):
        self.wallets = {w["key"]: w for w in wallets}
        self.narratives = narratives or {}
        self.entities = {}


def wallet(key, chain="robinhood", address=None, **kw):
    return {"key": key, "chain": chain, "address": address, "display": key,
            "resolved": bool(address), "labels": [], "narratives": [],
            "tokens": [], **kw}


def test_transfer_edges_only_between_tracked_wallets():
    """An external counterparty must not become a node or an edge."""
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    kb = FakeKB([wallet("robinhood:" + a, address=a), wallet("robinhood:" + b, address=b)])
    events = {
        "1": {"wallet_address": a, "counterparty": b, "direction": "out",
              "value_native": 5.0},
        "2": {"wallet_address": a, "counterparty": "0x" + "f" * 40,  # untracked
              "direction": "out", "value_native": 99.0},
    }
    g = graph.build(kb, events, {}, {})
    transfers = [e for e in g["edges"] if e["kind"] == "transfer"]
    assert len(transfers) == 1
    assert transfers[0]["value"] == 5.0


def test_transfer_direction_normalized():
    """An 'in' event from B's history is the same edge as an 'out' from A's."""
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    kb = FakeKB([wallet("robinhood:" + a, address=a), wallet("robinhood:" + b, address=b)])
    events = {
        "1": {"wallet_address": a, "counterparty": b, "direction": "out", "value_native": 3.0},
        "2": {"wallet_address": b, "counterparty": a, "direction": "in", "value_native": 4.0},
    }
    g = graph.build(kb, events, {}, {})
    transfers = [e for e in g["edges"] if e["kind"] == "transfer"]
    assert len(transfers) == 1, "both events describe A -> B"
    assert transfers[0]["source"].endswith(a)
    assert transfers[0]["value"] == 7.0


def test_self_transfers_ignored():
    a = "0x" + "a" * 40
    kb = FakeKB([wallet("robinhood:" + a, address=a)])
    events = {"1": {"wallet_address": a, "counterparty": a, "direction": "out",
                    "value_native": 5.0}}
    assert not [e for e in graph.build(kb, events, {}, {})["edges"]]


def test_shared_token_creates_behavioural_edge():
    kb = FakeKB([wallet("robinhood:w1", tokens=["OG"]), wallet("robinhood:w2", tokens=["OG"])])
    edges = graph.build(kb, {}, {}, {})["edges"]
    assert len(edges) == 1
    assert edges[0]["kind"] == "behavioural"
    assert "token:OG" in edges[0]["reasons"]


def test_edge_kinds_never_merge():
    """A pair sharing both a transfer and an attribute yields two edges."""
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    kb = FakeKB([
        wallet("robinhood:" + a, address=a, tokens=["OG"], entity="alice"),
        wallet("robinhood:" + b, address=b, tokens=["OG"], entity="alice"),
    ])
    events = {"1": {"wallet_address": a, "counterparty": b, "direction": "out",
                    "value_native": 2.0}}
    kinds = [e["kind"] for e in graph.build(kb, events, {}, {})["edges"]]
    assert sorted(kinds) == ["behavioural", "transfer"]


def test_large_shared_bucket_is_damped_away():
    """Fifty wallets sharing one feed must not become 1,225 edges."""
    wallets = [wallet(f"robinhood:w{i}", source="feed") for i in range(50)]
    edges = graph.build(FakeKB(wallets), {}, {}, {})["edges"]
    assert edges == [], "a source shared by everyone carries no information"


def test_transfer_coverage_reported():
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    kb = FakeKB([
        wallet("robinhood:" + a, address=a, tokens=["OG"]),
        wallet("robinhood:" + b, address=b, tokens=["OG"]),
    ])
    events = {"1": {"wallet_address": a, "counterparty": b, "direction": "out",
                    "value_native": 1.0}}
    stats = graph.build(kb, events, {}, {})["stats"]
    assert stats["transfer_coverage"] == pytest.approx(0.5)


def test_clusters_are_deterministic():
    wallets = [wallet(f"robinhood:w{i}", tokens=["OG" if i < 4 else "ZZZ"]) for i in range(8)]
    kb = FakeKB(wallets)
    first = graph.build(kb, {}, {}, {})["clusters"]
    second = graph.build(FakeKB(wallets), {}, {}, {})["clusters"]
    assert [c["members"] for c in first] == [c["members"] for c in second]


def test_cluster_not_named_after_a_minority_entity():
    """Naming 10 wallets after the single attributed member is misleading."""
    wallets = [wallet(f"robinhood:w{i}", tokens=["OG"]) for i in range(10)]
    wallets[0]["entity"] = "bizyugo"
    clusters = graph.build(FakeKB(wallets), {}, {}, {})["clusters"]
    assert clusters, "wallets sharing a token should cluster"
    assert clusters[0]["name"].startswith("cluster-")
    assert clusters[0]["name"] != "bizyugo"


def test_cluster_named_after_dominant_entity():
    wallets = [wallet(f"robinhood:w{i}", tokens=["OG"], entity="alice") for i in range(4)]
    clusters = graph.build(FakeKB(wallets), {}, {}, {})["clusters"]
    assert clusters[0]["name"] == "alice"


def test_flow_is_computed_per_node():
    a, b = "0x" + "a" * 40, "0x" + "b" * 40
    kb = FakeKB([wallet("robinhood:" + a, address=a), wallet("robinhood:" + b, address=b)])
    events = {"1": {"wallet_address": a, "counterparty": b, "direction": "out",
                    "value_native": 9.0}}
    nodes = {n["id"]: n for n in graph.build(kb, events, {}, {})["nodes"]}
    assert nodes["robinhood:" + a]["net_flow"] == -9.0
    assert nodes["robinhood:" + b]["net_flow"] == 9.0


def test_masked_wallets_appear_but_cannot_have_transfers():
    kb = FakeKB([wallet("robinhood:0x34…3a12", masked="0x34…3a12", tokens=["OG"]),
                 wallet("robinhood:w2", tokens=["OG"])])
    g = graph.build(kb, {}, {}, {})
    assert len(g["nodes"]) == 2
    assert not [e for e in g["edges"] if e["kind"] == "transfer"]


def test_empty_graph_does_not_crash():
    g = graph.build(FakeKB([]), {}, {}, {})
    assert g["nodes"] == [] and g["edges"] == [] and g["stats"]["transfer_coverage"] == 0.0

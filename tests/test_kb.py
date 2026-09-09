"""Knowledge-base loading, validation and cross-linking tests.

These files are hand-edited in the GitHub web UI, so validation quality IS
the user interface. A wrong chain name must produce a message naming the
file, the record and the likely fix - not a stack trace.
"""
import textwrap

import pytest
import yaml

from pipeline.kb import addresses, loader, schema

CHAINS = {
    "robinhood": {"family": "evm"},
    "solana": {"family": "svm"},
    "bitcoin": {"family": "utxo"},
}

EVM = "0x" + "a" * 40
SVM = "So11111111111111111111111111111111111111112"
BTC = "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"


# --- address handling -----------------------------------------------------

@pytest.mark.parametrize("address,family,valid", [
    (EVM, "evm", True),
    (EVM.upper().replace("0X", "0x"), "evm", True),
    ("0x123", "evm", False),
    (SVM, "svm", True),
    (EVM, "svm", False),          # 0x prefix is not base58
    (BTC, "utxo", True),
    ("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4", "utxo", True),
    (SVM, "utxo", False),
])
def test_address_validity(address, family, valid):
    assert addresses.is_valid(address, family) is valid


def test_evm_normalizes_to_lowercase():
    """Checksummed and lowercase forms must not become two records."""
    mixed = "0xAbCdEf" + "1" * 34
    assert addresses.normalize(mixed, "evm") == mixed.lower()


def test_base58_case_is_preserved():
    """Solana addresses are case-sensitive; lowercasing corrupts them."""
    assert addresses.normalize(SVM, "svm") == SVM


def test_invalid_address_raises():
    with pytest.raises(addresses.AddressError):
        addresses.validate("nonsense", "evm")


# --- schema validation ----------------------------------------------------

def test_unknown_chain_suggests_correction():
    with pytest.raises(schema.ValidationError) as exc:
        schema.validate_wallet({"chain": "solna", "address": SVM}, "w #1", CHAINS)
    assert "solana" in str(exc.value)          # suggestion present
    assert "w #1" in str(exc.value)            # location present


def test_wallet_requires_address_or_mask():
    with pytest.raises(schema.ValidationError) as exc:
        schema.validate_wallet({"chain": "robinhood"}, "w #1", CHAINS)
    assert "masked" in str(exc.value)


def test_masked_wallet_is_accepted_but_unresolved():
    """A masked sighting is worth recording before it can be resolved."""
    out = schema.validate_wallet(
        {"chain": "robinhood", "masked": "0x3475…3a12"}, "w #1", CHAINS)
    assert out["resolved"] is False
    assert out["address"] is None


def test_address_must_match_chain_family():
    with pytest.raises(schema.ValidationError) as exc:
        schema.validate_wallet({"chain": "solana", "address": EVM}, "w #1", CHAINS)
    assert "svm" in str(exc.value)


def test_unknown_label_rejected():
    with pytest.raises(schema.ValidationError):
        schema.validate_wallet(
            {"chain": "robinhood", "address": EVM, "labels": ["genius"]}, "w", CHAINS)


def test_entity_holdings_without_addresses():
    """A BTC treasury has holdings but no tracked addresses."""
    out = schema.validate_entity(
        {"key": "blackrock", "name": "BlackRock", "type": "etf",
         "holdings": [{"chain": "bitcoin", "asset": "BTC", "amount": 761801}]},
        "e #1")
    assert out["wallets"] == []
    assert out["holdings"][0]["amount"] == 761801


def test_narrative_outcome_requires_metric_and_value():
    with pytest.raises(schema.ValidationError) as exc:
        schema.validate_narrative(
            {"key": "n", "title": "T", "outcomes": [{"metric": "median_multiple"}]},
            "n.md", CHAINS)
    assert "value" in str(exc.value)


def test_narrative_bad_date_rejected():
    with pytest.raises(schema.ValidationError) as exc:
        schema.validate_narrative(
            {"key": "n", "title": "T", "opened": "09/08/2026"}, "n.md", CHAINS)
    assert "ISO date" in str(exc.value)


def test_bad_status_rejected():
    with pytest.raises(schema.ValidationError):
        schema.validate_narrative({"key": "n", "title": "T", "status": "hot"}, "n.md", CHAINS)


# --- loading and cross-linking -------------------------------------------

@pytest.fixture
def kb_dir(tmp_path):
    for sub in ("wallets", "entities", "narratives", "sources"):
        (tmp_path / sub).mkdir()
    (tmp_path / "chains.yml").write_text(yaml.safe_dump({
        "robinhood": {"name": "RH", "family": "evm", "enrichment": "blockscout"},
        "bitcoin": {"name": "BTC", "family": "utxo", "enrichment": "none"},
    }))
    return tmp_path


def test_entity_cluster_is_derived_from_wallets(kb_dir):
    """Cluster membership is derived, never stored twice."""
    (kb_dir / "wallets" / "rh.yml").write_text(yaml.safe_dump([
        {"chain": "robinhood", "address": "0x" + "1" * 40, "entity": "alice"},
        {"chain": "robinhood", "address": "0x" + "2" * 40, "entity": "alice"},
        {"chain": "robinhood", "address": "0x" + "3" * 40, "entity": "bob"},
    ]))
    (kb_dir / "entities" / "alice.yml").write_text(
        yaml.safe_dump({"key": "alice", "name": "Alice", "type": "individual"}))
    (kb_dir / "entities" / "bob.yml").write_text(
        yaml.safe_dump({"key": "bob", "name": "Bob", "type": "individual"}))

    kb = loader.load(kb_dir)
    assert kb.errors == []
    assert len(kb.entities["alice"]["cluster"]) == 2
    assert len(kb.entities["bob"]["cluster"]) == 1
    assert len(kb.wallets_for_entity("alice")) == 2


def test_dangling_entity_reference_is_reported(kb_dir):
    (kb_dir / "wallets" / "rh.yml").write_text(yaml.safe_dump([
        {"chain": "robinhood", "address": EVM, "entity": "ghost"}]))
    kb = loader.load(kb_dir)
    assert any("unknown entity" in e for e in kb.errors)


def test_dangling_narrative_reference_is_reported(kb_dir):
    (kb_dir / "wallets" / "rh.yml").write_text(yaml.safe_dump([
        {"chain": "robinhood", "address": EVM, "narratives": ["nope"]}]))
    kb = loader.load(kb_dir)
    assert any("unknown narrative" in e for e in kb.errors)


def test_duplicate_wallet_reported(kb_dir):
    (kb_dir / "wallets" / "rh.yml").write_text(yaml.safe_dump([
        {"chain": "robinhood", "address": EVM},
        {"chain": "robinhood", "address": EVM.upper().replace("0X", "0x")},
    ]))
    kb = loader.load(kb_dir)
    assert any("duplicate" in e for e in kb.errors)


def test_narrative_without_frontmatter_reported(kb_dir):
    (kb_dir / "narratives" / "bad.md").write_text("just prose, no frontmatter")
    kb = loader.load(kb_dir)
    assert any("frontmatter" in e for e in kb.errors)


def test_narrative_links_wallets_both_ways(kb_dir):
    (kb_dir / "narratives" / "wave.md").write_text(textwrap.dedent("""\
        ---
        key: wave
        title: A wave
        status: active
        ---
        Body text.
        """))
    (kb_dir / "wallets" / "rh.yml").write_text(yaml.safe_dump([
        {"chain": "robinhood", "address": EVM, "narratives": ["wave"]}]))
    kb = loader.load(kb_dir)
    assert kb.errors == []
    assert len(kb.narratives["wave"]["linked_wallets"]) == 1
    assert kb.narratives["wave"]["body"] == "Body text."


def test_strict_mode_raises(kb_dir):
    (kb_dir / "wallets" / "rh.yml").write_text(yaml.safe_dump([
        {"chain": "nonexistent", "address": EVM}]))
    with pytest.raises(schema.ValidationError):
        loader.load(kb_dir, strict=True)


def test_real_knowledge_base_is_valid():
    """The committed knowledge base must always load cleanly."""
    kb = loader.load()
    assert kb.errors == [], f"knowledge base has errors: {kb.errors}"
    assert kb.stats()["wallets"] > 0

"""Loads and cross-links the knowledge base.

The KB is the human-authored half of the system: you assert what a wallet
is and why it matters, and the pipeline attaches whatever it can observe
on-chain. Everything here is read-only with respect to your files - the
pipeline never rewrites knowledge/, so an automated run can never silently
overwrite your analysis.

Cross-linking is bidirectional and derived, not stored twice: a wallet
names its entity, and the entity's cluster is assembled from that. Recording
the same relationship in both places would guarantee they drift apart.
"""
import re
from pathlib import Path

import yaml

from . import schema
from .addresses import shorten

ROOT = Path(__file__).resolve().parent.parent.parent
KNOWLEDGE_DIR = ROOT / "knowledge"

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _read_yaml(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise schema.ValidationError(f"{path.name}: invalid YAML - {exc}")


def _split_frontmatter(path):
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise schema.ValidationError(
            f"{path.name}: missing YAML frontmatter. A narrative file must "
            "start with a '---' block containing at least key: and title:"
        )
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise schema.ValidationError(f"{path.name}: invalid frontmatter YAML - {exc}")
    return meta, match.group(2).strip()


class KnowledgeBase:
    """The loaded, cross-linked knowledge base."""

    def __init__(self, chains, wallets, entities, narratives, sources,
                 infrastructure, errors):
        self.chains = chains
        self.wallets = wallets          # key "chain:address" -> record
        self.entities = entities        # key -> record (with .cluster)
        self.narratives = narratives    # key -> record (with .body)
        self.sources = sources
        self.infrastructure = infrastructure  # key "chain|*:address" -> record
        self.errors = errors

    # --- lookups ---------------------------------------------------------

    def role_of(self, address, chain=None):
        """The curated role of an address, or None if it is not registered.

        A chain-scoped entry wins over a chain-agnostic one: a router that
        happens to be a real wallet on some other chain should be judged
        per chain where we have said so.
        """
        address = (address or "").lower()
        for key in (f"{chain}:{address}", f"*:{address}"):
            record = self.infrastructure.get(key)
            if record:
                return record["role"]
        return None

    def wallets_for_entity(self, entity_key):
        return [w for w in self.wallets.values() if w.get("entity") == entity_key]

    def wallets_for_narrative(self, narrative_key):
        return [w for w in self.wallets.values()
                if narrative_key in (w.get("narratives") or [])]

    def wallets_on_chain(self, chain):
        return [w for w in self.wallets.values() if w["chain"] == chain]

    def unresolved(self):
        """Wallets known only by a masked address."""
        return [w for w in self.wallets.values() if not w.get("resolved")]

    def stats(self):
        by_chain, by_label = {}, {}
        for w in self.wallets.values():
            by_chain[w["chain"]] = by_chain.get(w["chain"], 0) + 1
            for label in w.get("labels", []):
                by_label[label] = by_label.get(label, 0) + 1
        by_status = {}
        for n in self.narratives.values():
            by_status[n["status"]] = by_status.get(n["status"], 0) + 1
        return {
            "chains": len(self.chains),
            "wallets": len(self.wallets),
            "resolved": sum(1 for w in self.wallets.values() if w.get("resolved")),
            "unresolved": len(self.unresolved()),
            "entities": len(self.entities),
            "narratives": len(self.narratives),
            "sources": len(self.sources),
            "infrastructure": len(self.infrastructure),
            "wallets_by_chain": by_chain,
            "wallets_by_label": by_label,
            "narratives_by_status": by_status,
        }


def load(knowledge_dir=None, strict=False):
    """Load the whole knowledge base.

    Invalid records are collected rather than raised so one malformed file
    cannot take the whole pipeline down; pass strict=True (as CI does) to
    fail loudly instead.
    """
    base = Path(knowledge_dir or KNOWLEDGE_DIR)
    errors = []

    def record_error(msg):
        if strict:
            raise schema.ValidationError(msg)
        errors.append(msg)

    # --- chains -----------------------------------------------------------
    chains_path = base / "chains.yml"
    if not chains_path.exists():
        raise schema.ValidationError(f"missing {chains_path}")
    chains = _read_yaml(chains_path)
    for key, chain in chains.items():
        if chain.get("family") not in ("evm", "svm", "utxo"):
            record_error(f"chains.yml: {key}: family must be evm, svm or utxo")

    # --- wallets ----------------------------------------------------------
    wallets = {}
    for path in sorted((base / "wallets").glob("*.yml")):
        data = _read_yaml(path)
        rows = data if isinstance(data, list) else data.get("wallets", [])
        if not isinstance(rows, list):
            record_error(f"{path.name}: expected a list of wallets")
            continue
        for i, row in enumerate(rows):
            where = f"wallets/{path.name} #{i + 1}"
            if not isinstance(row, dict):
                record_error(f"{where}: expected a mapping")
                continue
            try:
                record = schema.validate_wallet(row, where, chains)
            except schema.ValidationError as exc:
                record_error(str(exc))
                continue
            if record["key"] in wallets:
                record_error(f"{where}: duplicate wallet {record['key']}")
                continue
            record["display"] = (
                shorten(record["address"]) if record["address"] else row.get("masked")
            )
            record["source_file"] = path.name
            wallets[record["key"]] = record

    # --- entities ---------------------------------------------------------
    entities = {}
    for path in sorted((base / "entities").glob("*.yml")):
        data = _read_yaml(path)
        rows = data if isinstance(data, list) else [data]
        for i, row in enumerate(rows):
            where = f"entities/{path.name}" + (f" #{i + 1}" if len(rows) > 1 else "")
            if not isinstance(row, dict):
                record_error(f"{where}: expected a mapping")
                continue
            try:
                record = schema.validate_entity(row, where)
            except schema.ValidationError as exc:
                record_error(str(exc))
                continue
            if record["key"] in entities:
                record_error(f"{where}: duplicate entity key {record['key']!r}")
                continue
            record["source_file"] = path.name
            entities[record["key"]] = record

    # --- narratives -------------------------------------------------------
    narratives = {}
    for path in sorted((base / "narratives").glob("*.md")):
        try:
            meta, body = _split_frontmatter(path)
            record = schema.validate_narrative(meta, f"narratives/{path.name}", chains)
        except schema.ValidationError as exc:
            record_error(str(exc))
            continue
        record["body"] = body
        record["source_file"] = path.name
        if record["key"] in narratives:
            record_error(f"narratives/{path.name}: duplicate key {record['key']!r}")
            continue
        narratives[record["key"]] = record

    # --- sources ----------------------------------------------------------
    sources = {}
    for path in sorted((base / "sources").glob("*.yml")):
        data = _read_yaml(path)
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                record = schema.validate_source(row, f"sources/{path.name}")
            except schema.ValidationError as exc:
                record_error(str(exc))
                continue
            sources[record["key"]] = record

    # --- infrastructure --------------------------------------------------
    # One flat file rather than a directory: the list is short, and every
    # entry is read on every trace, so keeping it in one place makes it
    # reviewable as a whole.
    infrastructure = {}
    infra_path = base / "infrastructure.yml"
    if infra_path.exists():
        rows = _read_yaml(infra_path)
        rows = rows if isinstance(rows, list) else rows.get("infrastructure", [])
        for i, row in enumerate(rows or []):
            where = f"infrastructure.yml #{i + 1}"
            if not isinstance(row, dict):
                record_error(f"{where}: expected a mapping")
                continue
            try:
                record = schema.validate_infrastructure(row, where, chains)
            except schema.ValidationError as exc:
                record_error(str(exc))
                continue
            if record["key"] in infrastructure:
                record_error(f"{where}: duplicate address {record['address']}")
                continue
            infrastructure[record["key"]] = record

    kb = KnowledgeBase(chains, wallets, entities, narratives, sources,
                       infrastructure, errors)
    _cross_link(kb, record_error)
    return kb


def _cross_link(kb, record_error):
    """Resolve references between records and build entity clusters.

    A dangling reference is an error worth surfacing: a wallet pointing at a
    narrative that no longer exists silently disappears from that narrative's
    page, which looks like data loss rather than a typo.
    """
    # Wallets -> entities, and the derived cluster on each entity.
    for entity in kb.entities.values():
        entity["cluster"] = []

    for wallet in kb.wallets.values():
        entity_key = wallet.get("entity")
        if entity_key:
            entity = kb.entities.get(entity_key)
            if entity is None:
                record_error(
                    f"{wallet['source_file']}: wallet {wallet['display']} references "
                    f"unknown entity {entity_key!r}"
                )
            else:
                entity["cluster"].append(wallet["key"])

        for narrative_key in wallet.get("narratives", []):
            if narrative_key not in kb.narratives:
                record_error(
                    f"{wallet['source_file']}: wallet {wallet['display']} references "
                    f"unknown narrative {narrative_key!r}"
                )

    # Entities may also declare wallets inline (useful for a cluster whose
    # members are not individually interesting enough for their own record).
    for entity in kb.entities.values():
        for declared in entity.get("wallets", []):
            chain = declared.get("chain")
            address = declared.get("address") or declared.get("masked")
            if not address:
                continue
            key = f"{chain}:{(address.lower() if chain and kb.chains.get(chain, {}).get('family') == 'evm' else address)}"
            if key not in entity["cluster"]:
                entity["cluster"].append(key)

        for narrative_key in entity.get("narratives", []):
            if narrative_key not in kb.narratives:
                record_error(
                    f"{entity['source_file']}: entity {entity['key']!r} references "
                    f"unknown narrative {narrative_key!r}"
                )

    # Narratives -> entities and wallets.
    for narrative in kb.narratives.values():
        narrative["linked_wallets"] = sorted(
            {w["key"] for w in kb.wallets_for_narrative(narrative["key"])}
            | {w for w in narrative.get("wallets", [])}
        )
        for entity_key in narrative.get("entities", []):
            if entity_key not in kb.entities:
                record_error(
                    f"{narrative['source_file']}: references unknown entity {entity_key!r}"
                )

    # A source referenced by a wallet should exist.
    for wallet in kb.wallets.values():
        source = wallet.get("source")
        if source and kb.sources and source not in kb.sources:
            record_error(
                f"{wallet['source_file']}: wallet {wallet['display']} references "
                f"unknown source {source!r}"
            )

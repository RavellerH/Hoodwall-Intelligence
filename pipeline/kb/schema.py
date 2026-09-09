"""Knowledge-base record schemas and validation.

Validation is hand-rolled rather than jsonschema-driven so errors can name
the file, the record and the fix. These files are edited by hand in the
GitHub web UI, so a validation message is the only feedback the author
gets - "wallets/solana.yml #3: chain 'sol' is not in chains.yml (did you
mean 'solana'?)" is worth far more than a JSON Pointer.
"""
import datetime
import difflib

# --- controlled vocabularies ----------------------------------------------

ENTITY_TYPES = {
    "individual",   # a named person
    "fund",         # a trading firm or DAO treasury
    "exchange",     # CEX operational wallets
    "etf",          # spot ETF custodian
    "treasury",     # corporate holder
    "bot",          # automated system
    "unknown",
}

# How much we actually trust that a wallet belongs to the entity.
CONFIDENCE = {
    "confirmed",    # self-disclosed or provably linked on-chain
    "reported",     # a third party asserts it (a vendor feed, a post)
    "inferred",     # our own clustering heuristic
}

CONVICTION = {"none", "low", "medium", "high"}

NARRATIVE_STATUS = {"emerging", "active", "cooling", "dead", "invalidated"}

# What an address is, when it is not a wallet. Curated in
# knowledge/infrastructure.yml and trusted by the tracer without checking,
# which is why the vocabulary is closed.
INFRASTRUCTURE_ROLES = {
    "cex_hot",       # exchange hot/omnibus wallet
    "cex_deposit",   # deposit address confirmed to belong to one account
    "bridge",        # cross-chain bridge or relayer
    "mixer",         # privacy pool - interaction is itself a label
    "router",        # DEX router, aggregator, shared contract
    "burn",          # null / dead
}

WALLET_LABELS = {
    "smart_money", "whale", "accumulator", "distributor", "trading_bot",
    "mev_bot", "lp_mm", "bridge_flow", "fresh_emerging", "insider",
    "market_maker", "perps_trader", "long_term_holder", "noise",
}


class ValidationError(Exception):
    """A KB record that cannot be loaded. Message names file + record + fix."""


def _suggest(value, options):
    match = difflib.get_close_matches(str(value), sorted(options), n=1, cutoff=0.6)
    return f" (did you mean {match[0]!r}?)" if match else ""


def _require(record, field, where):
    if field not in record or record[field] in (None, ""):
        raise ValidationError(f"{where}: missing required field {field!r}")
    return record[field]


def _check_enum(value, options, field, where):
    if value not in options:
        raise ValidationError(
            f"{where}: {field}={value!r} is not valid{_suggest(value, options)}. "
            f"Allowed: {', '.join(sorted(options))}"
        )


def _check_date(value, field, where):
    if value is None:
        return None
    if isinstance(value, datetime.date):
        return value.isoformat()
    try:
        datetime.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(
            f"{where}: {field}={value!r} is not an ISO date (expected YYYY-MM-DD)"
        )
    return str(value)


def _check_list(record, field, where):
    value = record.get(field) or []
    if not isinstance(value, list):
        raise ValidationError(f"{where}: {field} must be a list, got {type(value).__name__}")
    return value


# --- record validators ----------------------------------------------------

def validate_wallet(record, where, chains):
    """A tracked wallet on one chain.

    Either `address` or `masked` is required: a wallet first seen in a feed
    that prints truncated addresses is worth recording before it can be
    resolved, and dropping it until resolution loses the sighting entirely.
    """
    from . import addresses as addr

    chain = _require(record, "chain", where)
    if chain not in chains:
        raise ValidationError(
            f"{where}: chain={chain!r} is not in chains.yml{_suggest(chain, chains)}"
        )
    family = chains[chain]["family"]

    address = record.get("address")
    masked = record.get("masked")
    if not address and not masked:
        raise ValidationError(
            f"{where}: needs either 'address' (full) or 'masked' (e.g. 0x3475…3a12)"
        )

    out = dict(record)
    if address:
        try:
            out["address"] = addr.validate(address, family)
        except addr.AddressError as exc:
            raise ValidationError(f"{where}: {exc} (chain {chain!r} is family {family!r})")
        out["resolved"] = True
    else:
        out["address"] = None
        out["resolved"] = False

    for label in _check_list(record, "labels", where):
        _check_enum(label, WALLET_LABELS, "label", where)

    if record.get("conviction"):
        _check_enum(record["conviction"], CONVICTION, "conviction", where)
    if record.get("confidence"):
        _check_enum(record["confidence"], CONFIDENCE, "confidence", where)

    out["added"] = _check_date(record.get("added"), "added", where)
    out["labels"] = _check_list(record, "labels", where)
    out["narratives"] = _check_list(record, "narratives", where)
    # Tokens this wallet was observed buying. This is the co-occurrence
    # signal the relationship graph uses: two wallets buying the same
    # micro-cap within one feed window is far stronger evidence of
    # coordination than merely sharing a data source.
    out["tokens"] = _check_list(record, "tokens", where)
    out["family"] = family
    out["key"] = f"{chain}:{out['address'] or masked}"
    return out


def validate_entity(record, where):
    """A person or organization that may control several wallets.

    Holdings are supported without addresses: a Bitcoin treasury or ETF is
    tracked as an entity with a size, because its coins are spread across
    many addresses that are not individually interesting.
    """
    out = dict(record)
    out["key"] = _require(record, "key", where)
    out["name"] = _require(record, "name", where)
    _check_enum(record.get("type", "unknown"), ENTITY_TYPES, "type", where)
    out["type"] = record.get("type", "unknown")

    if record.get("confidence"):
        _check_enum(record["confidence"], CONFIDENCE, "confidence", where)
    out["confidence"] = record.get("confidence", "reported")

    handles = record.get("handles") or {}
    if not isinstance(handles, dict):
        raise ValidationError(f"{where}: handles must be a mapping, e.g. {{twitter: '@name'}}")
    out["handles"] = handles

    wallets = _check_list(record, "wallets", where)
    for i, w in enumerate(wallets):
        if not isinstance(w, dict) or "chain" not in w:
            raise ValidationError(
                f"{where}: wallets[{i}] must be a mapping with at least 'chain'"
            )
    out["wallets"] = wallets
    out["holdings"] = _check_list(record, "holdings", where)
    out["narratives"] = _check_list(record, "narratives", where)
    return out


def validate_narrative(meta, where, chains):
    """A thesis being tracked over time, with measurable outcomes."""
    out = dict(meta)
    out["key"] = _require(meta, "key", where)
    out["title"] = _require(meta, "title", where)

    _check_enum(meta.get("status", "emerging"), NARRATIVE_STATUS, "status", where)
    out["status"] = meta.get("status", "emerging")

    if meta.get("conviction"):
        _check_enum(meta["conviction"], CONVICTION, "conviction", where)
    out["conviction"] = meta.get("conviction", "none")

    for chain in _check_list(meta, "chains", where):
        if chain not in chains:
            raise ValidationError(
                f"{where}: chains entry {chain!r} is not in chains.yml"
                f"{_suggest(chain, chains)}"
            )
    out["chains"] = _check_list(meta, "chains", where)
    out["tokens"] = _check_list(meta, "tokens", where)
    out["wallets"] = _check_list(meta, "wallets", where)
    out["entities"] = _check_list(meta, "entities", where)

    out["opened"] = _check_date(meta.get("opened"), "opened", where)
    out["closed"] = _check_date(meta.get("closed"), "closed", where)

    # Outcomes are what make a narrative auditable rather than a story.
    outcomes = _check_list(meta, "outcomes", where)
    for i, o in enumerate(outcomes):
        spot = f"{where}: outcomes[{i}]"
        if not isinstance(o, dict):
            raise ValidationError(f"{spot} must be a mapping")
        _require(o, "metric", spot)
        if "value" not in o:
            raise ValidationError(f"{spot}: missing 'value'")
        # Assign back: PyYAML turns an unquoted YYYY-MM-DD into a date
        # object, which is not JSON-serializable downstream.
        o["date"] = _check_date(o.get("date"), "date", spot)
    out["outcomes"] = outcomes

    updates = _check_list(meta, "updates", where)
    for i, u in enumerate(updates):
        spot = f"{where}: updates[{i}]"
        if not isinstance(u, dict):
            raise ValidationError(f"{spot} must be a mapping with 'date' and 'note'")
        u["date"] = _check_date(u.get("date"), "date", spot)
    out["updates"] = updates
    return out


def validate_infrastructure(record, where, chains):
    """An address that is not a wallet: an exchange, a bridge, a mixer.

    `chain` is optional and means "every EVM chain" when omitted, because a
    router or hot wallet is routinely deployed at one address everywhere.
    """
    from . import addresses as addr

    out = dict(record)
    address = _require(record, "address", where)
    _check_enum(_require(record, "role", where), INFRASTRUCTURE_ROLES, "role", where)

    chain = record.get("chain")
    if chain and chain not in chains:
        raise ValidationError(
            f"{where}: chain={chain!r} is not in chains.yml{_suggest(chain, chains)}"
        )
    family = chains[chain]["family"] if chain else "evm"
    try:
        out["address"] = addr.validate(address, family)
    except addr.AddressError as exc:
        raise ValidationError(f"{where}: {exc}")

    if record.get("confidence"):
        _check_enum(record["confidence"], CONFIDENCE, "confidence", where)
    out["confidence"] = record.get("confidence", "reported")
    out["chain"] = chain
    out["name"] = record.get("name", "")
    out["key"] = f"{chain or '*'}:{out['address']}"
    return out


def validate_source(record, where):
    """A vendor feed or data provider, plus its measured track record."""
    out = dict(record)
    out["key"] = _require(record, "key", where)
    out["name"] = _require(record, "name", where)
    out["reliability"] = record.get("reliability", {})
    if not isinstance(out["reliability"], dict):
        raise ValidationError(f"{where}: reliability must be a mapping of metric -> value")
    return out

"""Money-flow tracing: from a seed wallet to the rest of its cluster.

A handle posts one address. That address is almost never the whole story -
the same person keeps a funding wallet, a trading wallet, a wallet for the
thing they do not want associated with the first one. Those wallets are
linked by the only thing that cannot be denied: the money that moved
between them.

What this module does NOT do is treat "sent value to" as "is the same
person". Most counterparties are exchanges, routers, bridges and other
people, and a naive one-hop expansion produces a cluster of Uniswap. So
every candidate is scored against signals that distinguish self-transfers
from commerce:

  sole funder        the candidate's whole inbound history is this seed
  bidirectional      value moved both ways, which payments rarely do
  exclusive          the candidate deals with nobody else but the seed
  sweep              a transfer that emptied the sender - a migration
  shared funder      two wallets funded by the same non-service EOA
  shared deposit     two wallets depositing to the same CEX deposit
                     address, which means one exchange account

and against the traps that produce false clusters:

  contracts          a contract is not a wallet
  high degree        a counterparty with hundreds of peers is a service
  crowd              a counterparty shared by unrelated seeds is a service
  deposit addresses  exclusive to the seed by construction, yet not theirs

Every link carries its evidence, so a cluster can be argued with rather
than believed.
"""
import datetime

from . import chain, config
from .kb import addresses as addr
from .kb import loader

# Score a candidate must reach before we are willing to call it the same
# owner in a knowledge-base record (as `confidence: inferred`).
PROBABLE = 50
POSSIBLE = 30

# A counterparty with more distinct peers than this in one page of history
# is a service - a router, a hot wallet, a market maker - not a sibling.
SERVICE_DEGREE = 60

# Weights. Tuned so that no single soft signal reaches PROBABLE alone:
# only sole-funding, or two independent mid-weight signals, gets there.
WEIGHTS = {
    "sole_funder": 40,
    "bidirectional": 25,
    "exclusive": 20,
    "sweep": 15,
    "shared_funder": 15,
    "same_window": 5,
}


class FlowError(RuntimeError):
    """Tracing could not proceed (no readable chain, bad seed)."""


# --- chain plumbing --------------------------------------------------------

def evm_chains(kb=None):
    """EVM chains this tracer can actually read, as key -> api_base.

    Hyperliquid is EVM-shaped but its `api_base` is the perps info API, not
    a Blockscout instance, so it is excluded here: flow between Hyperliquid
    accounts is settlement inside the venue, not transfers we can walk.
    """
    chains = (kb or loader.load()).chains
    return {
        key: c["api_base"]
        for key, c in chains.items()
        if c.get("family") == "evm"
        and c.get("enrichment") == "blockscout"
        and c.get("api_base")
    }


def probe(address, chains):
    """Which of these chains has ever seen this address.

    Seeds arrive as bare hex with no chain attached - a handle posts
    "0xabc…", not "0xabc… on Base". Probing is cheaper and more honest than
    assuming, and an address active on three chains is itself a finding.
    """
    address = addr.validate(address, "evm")
    seen = {}
    for key, base in chains.items():
        try:
            summary = chain.get_address(address, base=base)
        except chain.ChainError as exc:
            print(f"  ! {key}: {exc}")
            continue
        if not summary:
            continue
        count = _int(summary.get("transactions_count") or summary.get("transaction_count"))
        if not count and not _int(summary.get("coin_balance")):
            continue
        seen[key] = {
            "transactions": count,
            "is_contract": bool(summary.get("is_contract")),
            "balance": chain.to_native(summary.get("coin_balance") or 0),
            "name": summary.get("name"),
        }
    return seen


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --- transfer extraction ---------------------------------------------------

def _transfers(address, base, pages):
    """Every value-moving edge touching `address`, native and token.

    Zero-value transactions are dropped: they are contract calls, and a
    call is not money flow. Token transfers are kept because a wallet
    migration in 2026 is as likely to be USDC as it is to be ETH.
    """
    address = address.lower()
    edges = []

    txs, _ = chain.get_paged(f"addresses/{address}/transactions",
                             params={"filter": "to|from"}, base=base,
                             max_pages=pages)
    for tx in txs:
        value = _int(tx.get("value"))
        if value <= 0:
            continue
        edges.append(_edge(address, tx, chain.to_native(value),
                           config.NATIVE_SYMBOL, tx.get("hash")))

    transfers, _ = chain.get_paged(f"addresses/{address}/token-transfers",
                                   base=base, max_pages=pages)
    for tr in transfers:
        total = tr.get("total") or {}
        token = tr.get("token") or {}
        # NFTs have no decimals and no meaningful "amount" for flow purposes.
        if total.get("decimals") in (None, ""):
            continue
        try:
            amount = int(total.get("value", 0)) / (10 ** int(total["decimals"]))
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if amount <= 0:
            continue
        edges.append(_edge(address, tr, amount,
                           token.get("symbol") or "?", tr.get("transaction_hash")))

    return [e for e in edges if e]


def _edge(address, item, amount, symbol, tx_hash):
    sender = chain.address_field(item.get("from"))
    receiver = chain.address_field(item.get("to"))
    if not sender or not receiver:
        return None
    if sender == receiver:
        return None
    peer = receiver if sender == address else sender
    if addr.is_burn(peer):
        return None
    peer_field = item.get("to") if sender == address else item.get("from")
    return {
        "peer": peer,
        "direction": "out" if sender == address else "in",
        "amount": amount,
        "symbol": symbol,
        "time": item.get("timestamp") or "",
        "tx": tx_hash,
        "peer_is_contract": chain.is_contract(peer_field),
    }


def counterparties(address, base, pages=None):
    """Aggregate an address's value edges into one row per counterparty."""
    pages = pages or config.MAX_FLOW_PAGES
    agg = {}
    for edge in _transfers(address, base, pages):
        row = agg.setdefault(edge["peer"], {
            "address": edge["peer"], "in_count": 0, "out_count": 0,
            "in_value": {}, "out_value": {}, "first": "", "last": "",
            "is_contract": False, "txs": [],
        })
        side = edge["direction"]
        row[f"{side}_count"] += 1
        values = row[f"{side}_value"]
        values[edge["symbol"]] = values.get(edge["symbol"], 0.0) + edge["amount"]
        row["is_contract"] = row["is_contract"] or edge["peer_is_contract"]
        when = edge["time"]
        if when:
            row["first"] = min(row["first"] or when, when)
            row["last"] = max(row["last"], when)
        if len(row["txs"]) < 5:
            row["txs"].append(edge["tx"])
    return agg


# --- linkage ---------------------------------------------------------------

def _history(address, base, pages):
    """(inbound edges, outbound edges, complete) for a candidate.

    `complete` matters more than the edges: "its only funder is the seed"
    is only true if we have seen every inbound transfer, and Blockscout
    tells us that by omitting the next-page cursor.
    """
    txs, complete = chain.get_paged(f"addresses/{address}/transactions",
                                    params={"filter": "to|from"}, base=base,
                                    max_pages=pages)
    inbound, outbound = [], []
    for tx in txs:
        if _int(tx.get("value")) <= 0:
            continue
        edge = _edge(address.lower(), tx, chain.to_native(tx["value"]),
                     config.NATIVE_SYMBOL, tx.get("hash"))
        if not edge:
            continue
        (inbound if edge["direction"] == "in" else outbound).append(edge)
    return inbound, outbound, complete


def classify(address, base, seeds, pages=None):
    """Decide what a counterparty *is* before asking whose it is.

    Returns one of: 'contract', 'service', 'deposit', 'wallet'. The
    deposit case is the subtle one - a CEX deposit address is funded by
    exactly one person and forwards everything onward, so on funding
    exclusivity alone it looks like a sibling wallet. It is not; but two
    seeds sharing one is the same exchange account, which is stronger
    evidence than any transfer pattern.
    """
    pages = pages or config.MAX_FLOW_PAGES
    summary = chain.get_address(address, base=base) or {}
    if summary.get("is_contract"):
        return "contract", summary

    inbound, outbound, _ = _history(address, base, pages)
    peers = {e["peer"] for e in inbound} | {e["peer"] for e in outbound}
    if len(peers) > SERVICE_DEGREE:
        return "service", summary

    senders = {e["peer"] for e in inbound}
    receivers = {e["peer"] for e in outbound}
    funded_by_seed = bool(senders & set(seeds))
    # Forwards to a single address it never receives from, and never sends
    # back to whoever funded it: the shape of a deposit sweep.
    if (funded_by_seed and len(receivers) == 1
            and not (receivers & senders) and outbound):
        return "deposit", summary

    return "wallet", summary


def score_link(seed, candidate, row, base, seeds, pages=None):
    """Score one counterparty as "same owner as seed", with evidence."""
    pages = pages or config.MAX_FLOW_PAGES
    evidence, score = [], 0

    if row["in_count"] and row["out_count"]:
        score += WEIGHTS["bidirectional"]
        evidence.append(
            f"value moved both ways ({row['out_count']} out / {row['in_count']} in)")

    inbound, outbound, complete = _history(candidate, base, pages)
    senders = {e["peer"] for e in inbound}
    peers = senders | {e["peer"] for e in outbound}

    if complete and senders and senders <= {seed}:
        score += WEIGHTS["sole_funder"]
        evidence.append(f"every inbound transfer came from {addr.shorten(seed)}")
    elif complete and senders and senders <= set(seeds):
        score += WEIGHTS["shared_funder"]
        evidence.append("funded only by seeds in this trace")

    if complete and peers and peers <= set(seeds):
        score += WEIGHTS["exclusive"]
        evidence.append("deals with no address outside this trace")

    if _swept(seed, candidate, row):
        score += WEIGHTS["sweep"]
        evidence.append("a transfer moved the sender's whole balance")

    if _same_window(row):
        score += WEIGHTS["same_window"]
        evidence.append("first and last contact within 24h - a one-off move")

    verdict = ("probable" if score >= PROBABLE
               else "possible" if score >= POSSIBLE else "weak")
    return score, verdict, evidence


def _swept(seed, candidate, row):
    """Did one transfer look like a wallet migration rather than a payment?

    Approximated by round-trip absence plus a single large outbound: we
    cannot see historical balances through this API, so this stays a soft
    signal and is weighted accordingly.
    """
    if row["out_count"] != 1 or row["in_count"]:
        return False
    return any(amount > 0 for amount in row["out_value"].values())


def _same_window(row):
    first, last = row.get("first"), row.get("last")
    if not first or not last:
        return False
    try:
        start = datetime.datetime.fromisoformat(first.replace("Z", "+00:00"))
        end = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (end - start) <= datetime.timedelta(hours=24)


# --- the trace -------------------------------------------------------------

def trace(seeds, chains=None, depth=None, budget=None, pages=None):
    """Walk money flow out from `seeds` and return a linkage report.

    Depth 2 expands only from wallets already judged probable. Expanding
    from everything instead turns the second hop into the whole chain.
    """
    depth = depth or config.MAX_FLOW_DEPTH
    budget = budget or config.MAX_FLOW_ADDRESSES
    pages = pages or config.MAX_FLOW_PAGES
    chains = chains if chains is not None else evm_chains()
    if not chains:
        raise FlowError("no Blockscout-readable EVM chain in knowledge/chains.yml")

    seeds = [addr.validate(s, "evm") for s in seeds]
    report = {
        "generated_at": _now(), "seeds": seeds, "depth": depth,
        "chains_probed": sorted(chains), "activity": {}, "links": {},
        "shared": [], "skipped": {},
    }

    for seed in seeds:
        report["activity"][seed] = probe(seed, chains)

    # Deposit addresses, keyed by address, so two seeds landing on the same
    # one can be reported as one exchange account.
    deposits = {}
    examined = 0

    for chain_key, base in chains.items():
        frontier = [s for s in seeds if chain_key in report["activity"].get(s, {})]
        if not frontier:
            continue
        print(f"\n[{chain_key}] {len(frontier)} active seed(s)")
        walked = set(frontier)
        # Classification is per chain and never changes between hops, so it
        # is memoized - but a candidate reached again from a second origin
        # is still attributed to that origin. Skipping it outright is how an
        # earlier version lost its best signal: a deposit address used by
        # two seeds only looks shared if both uses are counted.
        classified = {}

        for hop in range(depth):
            next_frontier = []
            for origin in frontier:
                if examined >= budget:
                    print(f"  budget reached ({budget} addresses examined)")
                    break
                rows = counterparties(origin, base, pages)
                print(f"  {addr.shorten(origin)}: {len(rows)} counterparties")
                for candidate, row in rows.items():
                    if candidate in classified:
                        kind = classified[candidate]
                    else:
                        if candidate in walked or examined >= budget:
                            continue
                        examined += 1
                        kind, _summary = classify(candidate, base, seeds, pages)
                        classified[candidate] = kind

                    if kind != "wallet":
                        report["skipped"].setdefault(kind, []).append(candidate)
                        if kind == "deposit":
                            deposits.setdefault(candidate, []).append(origin)
                        continue

                    score, verdict, evidence = score_link(
                        origin, candidate, row, base, seeds, pages)
                    if verdict == "weak":
                        continue
                    key = f"{chain_key}:{candidate}"
                    existing = report["links"].get(key)
                    if existing and existing["score"] >= score:
                        continue
                    report["links"][key] = {
                        "chain": chain_key, "address": candidate,
                        "linked_to": origin, "hop": hop + 1,
                        "score": score, "verdict": verdict, "evidence": evidence,
                        "first_seen": row["first"], "last_seen": row["last"],
                        "in": row["in_value"], "out": row["out_value"],
                        "example_txs": row["txs"],
                    }
                    if verdict == "probable" and candidate not in walked:
                        walked.add(candidate)
                        next_frontier.append(candidate)
            frontier = next_frontier
            if not frontier:
                break

    for deposit, users in deposits.items():
        if len(set(users)) > 1:
            report["shared"].append({
                "kind": "shared_deposit", "via": deposit,
                "addresses": sorted(set(users)),
                "note": "these wallets deposit to one address - one exchange account",
            })

    for entry in report["skipped"].values():
        entry[:] = sorted(set(entry))
    return report


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def summarize(report):
    """Human-readable trace summary for a CLI or an Actions log."""
    lines = [f"seeds: {len(report['seeds'])}  chains: {', '.join(report['chains_probed'])}"]
    for seed, activity in report["activity"].items():
        where = ", ".join(f"{k}({v['transactions']} tx)" for k, v in activity.items())
        lines.append(f"  {seed} -> {where or 'no activity found'}")
    probable = [l for l in report["links"].values() if l["verdict"] == "probable"]
    possible = [l for l in report["links"].values() if l["verdict"] == "possible"]
    lines.append(f"\nlinked wallets: {len(probable)} probable, {len(possible)} possible")
    for link in sorted(report["links"].values(), key=lambda l: -l["score"]):
        lines.append(f"  [{link['score']:>3}] {link['verdict']:<8} {link['chain']}:"
                     f"{link['address']}  <- {addr.shorten(link['linked_to'])}")
        for item in link["evidence"]:
            lines.append(f"        - {item}")
    for shared in report["shared"]:
        lines.append(f"\n  {shared['kind']} via {shared['via']}: "
                     + ", ".join(addr.shorten(a) for a in shared["addresses"]))
    skipped = "; ".join(f"{k} {len(v)}" for k, v in sorted(report["skipped"].items()))
    if skipped:
        lines.append(f"\nnot wallets: {skipped}")
    return "\n".join(lines)

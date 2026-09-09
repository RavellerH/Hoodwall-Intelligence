"""Enriches candidate wallets with on-chain history from Blockscout.

Runs under a per-run budget (MAX_ENRICH_PER_RUN) so a scheduled job always
finishes well inside its time limit. Never-enriched candidates are processed
first; after that, the most stale wallets are refreshed, so the dataset
keeps improving run over run instead of going stale after the first pass.
"""
from . import chain, config
from .chain import ChainAuthError
from .store import (event_id, load, prune_events, upsert, utcnow, values)


# Stop the stage after this many consecutive failures.
CIRCUIT_BREAKER_THRESHOLD = 10


def _classify(tx, address):
    """Derive direction and event type for one transaction, from the
    perspective of the wallet being enriched."""
    sender = chain.address_field(tx.get("from"))
    recipient = chain.address_field(tx.get("to"))

    if sender == address and recipient == address:
        direction = "self"
    elif sender == address:
        direction = "out"
    elif recipient == address:
        direction = "in"
    else:
        direction = "unknown"

    counterparty = recipient if direction == "out" else sender

    if not recipient:
        event_type = "contract_creation"
    elif chain.is_contract(tx.get("to")):
        event_type = "contract_call"
    else:
        event_type = "transfer"

    return direction, counterparty, event_type


def _tx_success(tx):
    status = tx.get("status")
    if status is not None:
        return str(status).lower() in ("ok", "success", "1", "true")
    result = tx.get("result")
    if result is not None:
        return str(result).lower() in ("success", "ok")
    return True


def enrich_wallet(address):
    """Fetch and store one wallet's profile and recent events."""
    address = address.lower()
    try:
        info = chain.get_address(address)
        transactions = chain.get_transactions(address, limit=config.MAX_EVENTS_PER_WALLET)
    except ChainAuthError:
        raise   # configuration problem: abort the stage, do not retry per wallet
    except chain.ChainError as exc:
        print(f"  ! {address}: {exc}")
        return False

    if info is None:
        # Blockscout has never seen this address - a scraped false positive.
        upsert("candidates", [{"address": address, "status": "unknown_to_chain",
                               "last_checked_at": utcnow()}])
        return False

    now = utcnow()
    events = []
    for tx in transactions:
        tx_hash = (tx.get("hash") or "").lower()
        if not tx_hash:
            continue
        direction, counterparty, event_type = _classify(tx, address)
        events.append({
            "event_id": event_id(tx_hash),
            "tx_hash": tx_hash,
            "log_index": 0,
            "wallet_address": address,
            "event_time": tx.get("timestamp") or "",
            "event_type": event_type,
            "direction": direction,
            "counterparty": counterparty,
            "to_is_contract": chain.is_contract(tx.get("to")),
            "value_native": chain.to_native(tx.get("value")),
            "method": (tx.get("method") or ""),
            "success": _tx_success(tx),
            "source": "blockscout",
        })

    if events:
        upsert("events", events)

    upsert("wallets", [{
        "address": address,
        "address_type": "contract" if info.get("is_contract") else "eoa",
        "balance_native": chain.to_native(info.get("coin_balance")),
        "chain_tx_count": info.get("transactions_count") or info.get("transaction_count") or 0,
        "is_verified_contract": bool(info.get("is_verified")),
        "enriched_at": now,
        "event_count": len(events),
    }])
    upsert("candidates", [{"address": address, "status": "enriched", "last_checked_at": now}])
    return True


def run():
    candidates = values("candidates")
    wallets = load("wallets")

    # Never-enriched candidates first, then the most stale wallets.
    pending = [c["address"] for c in candidates
               if c["address"] not in wallets and c.get("status") != "unknown_to_chain"]
    refresh = sorted(
        (w for w in wallets.values() if w.get("address")),
        key=lambda w: w.get("enriched_at") or "",
    )

    budget = config.MAX_ENRICH_PER_RUN
    queue = pending[:budget]
    if len(queue) < budget:
        queue += [w["address"] for w in refresh[: budget - len(queue)]]

    print(f"[enrich] {len(pending)} pending, {len(wallets)} known; "
          f"processing {len(queue)} this run")

    ok, consecutive_failures = 0, 0
    for i, address in enumerate(queue, 1):
        try:
            succeeded = enrich_wallet(address)
        except ChainAuthError as exc:
            # Every wallet would fail identically; stop immediately.
            raise RuntimeError(f"chain API refused the request: {exc}") from exc

        if succeeded:
            ok += 1
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            # A long unbroken failure run means the endpoint is down or the
            # response shape changed - grinding through the rest of the queue
            # just burns minutes and produces the same nothing.
            if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                print(f"  ! {consecutive_failures} consecutive failures; "
                      "stopping early (endpoint appears unavailable)")
                break
        if i % 10 == 0:
            print(f"  ... {i}/{len(queue)}")

    prune_events()
    print(f"[enrich] enriched {ok}/{len(queue)}")
    if ok == 0 and queue:
        # Surface this: a run that enriches nothing is a failure even though
        # every individual step "succeeded".
        print("::warning::Enrichment produced no wallets - check BLOCKSCOUT_BASE "
              "and BLOCKSCOUT_API_KEY.")
    return ok


if __name__ == "__main__":
    run()

"""Direct on-chain discovery from Blockscout.

This replaces the local screen-OCR source, which had no cloud equivalent -
there is no screen in a CI container. It is also a strictly better source:
instead of only finding wallets that somebody happened to mention, it walks
recent chain activity and finds active wallets first-hand.

Externally-owned accounts are kept and contracts are skipped, since the
target of the system is traders, not the venues they trade against.
"""
from .. import chain, config
from ..store import keys, upsert, utcnow

SOURCE_NAME = "blockscout"


def _harvest(transactions, seen):
    """Pull EOA senders (and non-contract recipients) out of a tx list."""
    for tx in transactions:
        sender = tx.get("from")
        recipient = tx.get("to")

        address = chain.address_field(sender)
        if address and not chain.is_contract(sender):
            seen.add(address)

        address = chain.address_field(recipient)
        if address and not chain.is_contract(recipient):
            seen.add(address)


def run():
    print("[discovery] walking recent chain activity")
    seen = set()

    try:
        recent = chain.get_main_page_transactions()
        _harvest(recent, seen)
        print(f"  recent transactions: {len(seen)} address(es)")
    except chain.ChainError as exc:
        print(f"  ! recent transactions unavailable: {exc}")

    # Walk back through recent blocks for broader coverage than the single
    # main-page snapshot provides.
    try:
        blocks = chain.get_blocks()[: config.MAX_DISCOVERY_BLOCKS]
        for block in blocks:
            number = block.get("height") or block.get("number")
            if number is None:
                continue
            try:
                _harvest(chain.get_block_transactions(number), seen)
            except chain.ChainError as exc:
                print(f"  ! block {number}: {exc}")
        print(f"  after {len(blocks)} block(s): {len(seen)} address(es)")
    except chain.ChainError as exc:
        print(f"  ! block list unavailable: {exc}")

    if not seen:
        print("[discovery] nothing found")
        return 0

    # Only write genuinely unseen addresses so existing candidates keep the
    # source that originally discovered them.
    fresh = sorted(seen - keys("candidates"))
    if not fresh:
        print(f"[discovery] {len(seen)} address(es), all already known")
        return 0

    now = utcnow()
    new = upsert("candidates", [
        {
            "address": address,
            "first_seen_at": now,
            "last_seen_at": now,
            "source": SOURCE_NAME,
            "source_url": f"{config.CHAIN_EXPLORER_URL}/address/{address}",
            "status": "candidate",
        }
        for address in fresh
    ])
    print(f"[discovery] {len(seen)} address(es) seen, {new} new")
    return new


if __name__ == "__main__":
    run()

"""Hyperliquid enrichment stage.

Reads the Hyperliquid wallets from the knowledge base (the KB is the source
of truth for who to watch on this chain - there is no discovery crawl for
perps), enriches each from the public info API, and stores the result.

Needs no API key, which is why this chain can light up before Blockscout.
"""
from .adapters import hyperliquid as hl
from .kb import load as load_kb
from .kb.schema import ValidationError
from .store import save, utcnow

CIRCUIT_BREAKER_THRESHOLD = 8


def run():
    try:
        kb = load_kb()
    except ValidationError as exc:
        print(f"[hl] knowledge base failed to load: {exc}")
        return 0

    addresses = sorted({
        w["address"] for w in kb.wallets_on_chain("hyperliquid") if w.get("address")
    })
    if not addresses:
        print("[hl] no Hyperliquid wallets in the knowledge base")
        return 0

    print(f"[hl] enriching {len(addresses)} account(s) from the public info API")
    results, consecutive_failures = {}, 0

    for i, address in enumerate(addresses, 1):
        try:
            record = hl.enrich(address)
        except hl.HyperliquidError as exc:
            consecutive_failures += 1
            print(f"  ! {address}: {exc}")
            if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
                print(f"  ! {consecutive_failures} consecutive failures; stopping early")
                break
            continue

        consecutive_failures = 0
        record["scored_at"] = utcnow()
        results[address] = record
        if i % 5 == 0:
            print(f"  ... {i}/{len(addresses)}")

    if results:
        save("hl_scores", results)

    tiers = {}
    for row in results.values():
        tiers[row["tier"]] = tiers.get(row["tier"], 0) + 1
    print(f"[hl] enriched {len(results)}/{len(addresses)}"
          + (f": {', '.join(f'{k}={v}' for k, v in sorted(tiers.items()))}" if tiers else ""))

    if not results and addresses:
        print("::warning::Hyperliquid enrichment produced nothing - the public "
              "info API may be unreachable from this runner.")
    return len(results)


if __name__ == "__main__":
    run()

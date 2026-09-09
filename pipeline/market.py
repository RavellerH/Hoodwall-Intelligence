"""Market data stage: chain TVL and major-cap prices from DefiLlama.

Runs independently of on-chain enrichment - it needs no API key and no
wallet data, so it works on the very first run and never blocks on
Blockscout or Hyperliquid access. Feeds two things: the DASH/CHN ticker,
and pipeline.sentiment.price_signal, which was an always-empty stub before
this existed.
"""
from . import config
from .adapters import defillama
from .kb import load as load_kb
from .kb.schema import ValidationError
from .store import save, utcnow


def run():
    try:
        kb = load_kb()
    except ValidationError as exc:
        print(f"[market] knowledge base failed to load: {exc}")
        return 0

    chains = kb.chains
    tvl_names = sorted({c["defillama_chain"] for c in chains.values()
                        if c.get("defillama_chain")})
    coin_ids = sorted({c["defillama_coin"] for c in chains.values()
                       if c.get("defillama_coin")})

    print(f"[market] fetching TVL for {len(tvl_names)} chain(s), "
          f"prices for {len(coin_ids)} coin(s)")

    try:
        tvl_by_name = defillama.chain_tvl_by_name(tvl_names)
    except defillama.DefiLlamaError as exc:
        print(f"  ! chain TVL fetch failed: {exc}")
        tvl_by_name = {}

    try:
        prices = defillama.price_snapshot(coin_ids)
    except defillama.DefiLlamaError as exc:
        print(f"  ! price fetch failed: {exc}")
        prices = {}

    now = utcnow()
    rows = {}
    for key, chain in chains.items():
        tvl_entry = tvl_by_name.get(chain.get("defillama_chain") or "")
        price_entry = prices.get(chain.get("defillama_coin") or "")
        if not tvl_entry and not price_entry:
            continue
        rows[key] = {
            "chain": key,
            "name": chain.get("name", key),
            "tvl_usd": tvl_entry.get("tvl") if tvl_entry else None,
            "price_usd": price_entry.get("price") if price_entry else None,
            "price_change_24h": price_entry.get("change_24h") if price_entry else None,
            "symbol": (price_entry.get("symbol") if price_entry
                      else chain.get("native_symbol", "")),
            "updated_at": now,
        }

    save("market", rows)
    have_tvl = sum(1 for r in rows.values() if r["tvl_usd"] is not None)
    have_price = sum(1 for r in rows.values() if r["price_usd"] is not None)
    print(f"[market] {len(rows)} chain(s) with data "
          f"({have_tvl} TVL, {have_price} price)")
    if not rows:
        print("::warning::Market stage produced no data - check network access to DefiLlama.")
    return len(rows)


if __name__ == "__main__":
    run()

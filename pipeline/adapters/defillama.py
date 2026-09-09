"""DefiLlama market data: chain TVL and major-cap token prices.

The only tool in the user's shared landscape (FOMO, Kaito, Bubblemaps,
Artemis, GMGN, ...) with a genuinely free, keyless, no-rate-limit-in-
practice API. Everything else in that list is either a consumer app with
no public data API, or gates its API behind signup/payment - see
docs/tool-landscape.md and docs/market-data.md for the research behind
that conclusion.

Two base URLs, both unauthenticated:
    https://api.llama.fi     - protocol/chain TVL
    https://coins.llama.fi   - token prices (current + historical)

This feeds two things: chain-level TVL context (CHN view, DASH ticker) and
real 24h price momentum for pipeline.sentiment.price_signal, which was
previously an always-empty stub because no price adapter existed.
"""
import time

import requests

TVL_BASE = "https://api.llama.fi"
PRICES_BASE = "https://coins.llama.fi"
TIMEOUT = 20
RETRIES = 3
# DefiLlama's free tier has no auth and no rate limit a normal caller would
# hit (~500 req/5min); a small delay is still polite since this fires on
# every scheduled run.
DELAY = 0.2

_session = None


class DefiLlamaError(RuntimeError):
    """A DefiLlama call that failed after exhausting retries."""


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "hoodwall-intelligence/2.0 (+github actions)",
            "Accept": "application/json",
        })
    return _session


def _get(base, path, params=None):
    url = f"{base.rstrip('/')}/{path.lstrip('/')}"
    delay = 1.0
    last_error = None
    for attempt in range(RETRIES):
        try:
            response = _get_session().get(url, params=params, timeout=TIMEOUT)
            if response.status_code == 429:
                time.sleep(min(float(response.headers.get("Retry-After", delay)), 20))
                delay *= 2
                continue
            if 400 <= response.status_code < 500:
                # Permanent for this call - a bad coin id, a typo'd chain
                # name - retrying it three times wastes the run's time.
                raise DefiLlamaError(f"{response.status_code} {response.reason} for {url}")
            response.raise_for_status()
            time.sleep(DELAY)
            return response.json()
        except DefiLlamaError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < RETRIES - 1:
                time.sleep(delay)
                delay *= 2
    raise DefiLlamaError(f"{url} failed after {RETRIES} attempts: {last_error}")


def get_chains():
    """All chains DefiLlama tracks, with current TVL.

    Returns a list of {name, tvl, gecko_id, tokenSymbol, chainId, ...}.
    Bitcoin and brand-new L2s (Robinhood Chain) are not "DeFi chains" in
    DefiLlama's sense and simply will not appear here - callers must treat
    a missing chain as "no TVL data", not an error.
    """
    payload = _get(TVL_BASE, "v2/chains")
    return payload if isinstance(payload, list) else []


def get_current_prices(coin_ids):
    """Current prices for a list of DefiLlama coin ids.

    A coin id is either "coingecko:<slug>" for a major asset or
    "<chain>:<contract-address>" for a specific token. Returns
    {coin_id: {"price": float, "symbol": str, "confidence": float}} -
    ids DefiLlama could not price are simply absent from the result, never
    an error.
    """
    if not coin_ids:
        return {}
    payload = _get(PRICES_BASE, f"prices/current/{','.join(coin_ids)}")
    return (payload or {}).get("coins", {})


def get_historical_prices(coin_ids, timestamp):
    """Prices for a list of coin ids as of a unix timestamp."""
    if not coin_ids:
        return {}
    payload = _get(PRICES_BASE, f"prices/historical/{int(timestamp)}/{','.join(coin_ids)}")
    return (payload or {}).get("coins", {})


def price_snapshot(coin_ids, now=None):
    """Current price plus trailing-24h percent change for each coin id.

    Computed from two point-in-time price calls (now, and now-24h) rather
    than relying on a dedicated percentage endpoint, so this only depends
    on the two request shapes documented and confirmed above.
    """
    now = now or time.time()
    current = get_current_prices(coin_ids)
    if not current:
        return {}

    try:
        past = get_historical_prices(list(current.keys()), now - 86_400)
    except DefiLlamaError:
        # Momentum is a bonus on top of the current price; losing it must
        # not lose the price itself.
        past = {}

    out = {}
    for coin_id, entry in current.items():
        price = entry.get("price")
        if price is None:
            continue
        row = {"symbol": entry.get("symbol", ""), "price": price,
               "confidence": entry.get("confidence"), "change_24h": None}
        old = past.get(coin_id, {}).get("price")
        if old and old > 0:
            row["change_24h"] = round((price - old) / old * 100, 3)
        out[coin_id] = row
    return out


def chain_tvl_by_name(names):
    """TVL lookup for a set of chain display names, case-insensitive.

    Returns {requested_name: {tvl, name, gecko_id, ...} or None}. A chain
    DefiLlama does not track (Bitcoin, a brand-new L2) maps to None rather
    than being silently omitted, so callers can distinguish "no data" from
    "didn't ask".
    """
    wanted = {n.lower(): n for n in names}
    found = {n.lower(): None for n in names}
    try:
        for chain in get_chains():
            key = (chain.get("name") or "").lower()
            if key in wanted:
                found[key] = chain
    except DefiLlamaError as exc:
        print(f"  ! chain TVL unavailable: {exc}")
    return {wanted[k]: v for k, v in found.items()}

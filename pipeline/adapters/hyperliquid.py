"""Hyperliquid enrichment: live perps positions and fill history.

Hyperliquid needs its own adapter because its data is positional, not
transactional. The EVM feature extractor measures counterparty diversity,
contract engagement and transaction cadence - none of which exist here.
What matters instead is capital at risk, leverage, directional bias,
concentration and realized performance.

The public info API needs no key and no account:
    POST https://api.hyperliquid.xyz/info  {"type": "...", "user": "0x..."}
"""
import math
import time
from collections import Counter

import requests

BASE_URL = "https://api.hyperliquid.xyz/info"
TIMEOUT = 20
RETRIES = 3
# The public endpoint is address-rate-limited; pace requests politely.
DELAY = 0.2

_session = None


class HyperliquidError(RuntimeError):
    """An info-API call that failed after exhausting retries."""


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "content-type": "application/json",
            "user-agent": "hoodwall-intelligence/2.0",
        })
    return _session


def _post(payload):
    delay = 1.0
    last_error = None
    for attempt in range(RETRIES):
        try:
            response = _get_session().post(BASE_URL, json=payload, timeout=TIMEOUT)
            if response.status_code == 429:
                time.sleep(min(float(response.headers.get("Retry-After", delay)), 30))
                delay *= 2
                continue
            if 400 <= response.status_code < 500:
                # Client errors are permanent; retrying wastes the budget.
                raise HyperliquidError(
                    f"{response.status_code} {response.reason} for {payload.get('type')}")
            response.raise_for_status()
            time.sleep(DELAY)
            return response.json()
        except HyperliquidError:
            raise
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < RETRIES - 1:
                time.sleep(delay)
                delay *= 2
    raise HyperliquidError(f"{payload.get('type')} failed after {RETRIES} attempts: {last_error}")


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_state(address):
    """Current margin summary and open positions."""
    return _post({"type": "clearinghouseState", "user": address})


def get_fills(address):
    """Recent fills. The API caps this at its own window; treat as 'recent'."""
    data = _post({"type": "userFills", "user": address})
    return data if isinstance(data, list) else []


def exists(address):
    """Does this address have a Hyperliquid account at all?

    Chain resolution for a submitted address normally means probing block
    explorers, and every explorer is blind to Hyperliquid: the activity is
    positions inside the venue, not transactions. So membership has to be
    asked of the venue itself.

    "Has an account" means equity, an open position, or a fill on record.
    An address that merely exists as 40 hex characters returns an empty
    clearinghouse state, which is the answer we want to distinguish.
    """
    try:
        state = get_state(address) or {}
    except HyperliquidError:
        raise

    summary = state.get("marginSummary") or {}
    if _num(summary.get("accountValue")) > 0:
        return True
    if _num(state.get("withdrawable")) > 0:
        return True
    if any((entry.get("position") or entry).get("coin")
           for entry in state.get("assetPositions") or []):
        return True

    # A closed-out account still has history, and is still an account.
    try:
        return bool(get_fills(address))
    except HyperliquidError:
        return False


def ledger(address):
    """Deposits, withdrawals and transfers as the venue itself records them.

    This is the account's cash movement, not its trading: it says when money
    entered and left, which is the venue's half of the funding question. The
    other half - who sent it - lives on Arbitrum, because a Hyperliquid
    account is funded by bridging USDC in from there.

    Returns a list of {type, amount, time, raw}. An unsupported endpoint or
    an unexpected shape yields an empty list rather than an exception: the
    Arbitrum side is the authoritative answer, and this is corroboration.
    """
    try:
        data = _post({"type": "userNonFundingLedgerUpdates", "user": address})
    except HyperliquidError as exc:
        print(f"    (ledger unavailable for {address}: {exc})")
        return []
    if not isinstance(data, list):
        return []

    events = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        delta = entry.get("delta") or {}
        if not isinstance(delta, dict):
            continue
        amount = _num(delta.get("usdc") or delta.get("amount"))
        if amount == 0:
            continue
        events.append({
            "type": delta.get("type") or "?",
            "amount": amount,
            "time": entry.get("time"),
        })
    events.sort(key=lambda e: e["time"] or 0)
    return events


def funding_summary(address):
    """What the venue knows about money entering and leaving this account."""
    events = ledger(address)
    deposits = [e for e in events if "deposit" in (e["type"] or "").lower()]
    withdrawals = [e for e in events if "withdraw" in (e["type"] or "").lower()]
    return {
        "address": address,
        "events": len(events),
        "deposit_count": len(deposits),
        "deposit_total": sum(e["amount"] for e in deposits),
        "withdrawal_count": len(withdrawals),
        "first_deposit_at": deposits[0]["time"] if deposits else None,
    }


# --- feature extraction ---------------------------------------------------

def extract(address, state, fills):
    """Build the perps feature vector for one account."""
    state = state or {}
    fills = fills or []

    summary = state.get("marginSummary") or {}
    equity = _num(summary.get("accountValue"))
    notional = _num(summary.get("totalNtlPos"))
    margin_used = _num(summary.get("totalMarginUsed"))
    withdrawable = _num(state.get("withdrawable"))

    positions = []
    for entry in state.get("assetPositions") or []:
        position = entry.get("position") or entry
        size = _num(position.get("szi"))
        if size == 0:
            continue
        value = abs(_num(position.get("positionValue")))
        leverage = position.get("leverage") or {}
        positions.append({
            "coin": position.get("coin", "?"),
            "size": size,
            "direction": "long" if size > 0 else "short",
            "notional": value,
            "entry_px": _num(position.get("entryPx")),
            "unrealized_pnl": _num(position.get("unrealizedPnl")),
            "roe": _num(position.get("returnOnEquity")),
            "leverage": _num(leverage.get("value")),
            "leverage_type": leverage.get("type", "?"),
            "liquidation_px": _num(position.get("liquidationPx")) or None,
        })

    long_notional = sum(p["notional"] for p in positions if p["direction"] == "long")
    short_notional = sum(p["notional"] for p in positions if p["direction"] == "short")
    gross = long_notional + short_notional
    # +1 fully long, -1 fully short, 0 market neutral.
    net_bias = ((long_notional - short_notional) / gross) if gross else 0.0

    largest = max((p["notional"] for p in positions), default=0.0)
    concentration = (largest / gross) if gross else 0.0
    unrealized = sum(p["unrealized_pnl"] for p in positions)

    # Account-level leverage. Position-level `leverage` is the setting; this
    # is what the account is actually running.
    account_leverage = (notional / equity) if equity > 0 else 0.0

    # Fills: closedPnl is only meaningful on closing fills.
    closes = [f for f in fills if _num(f.get("closedPnl")) != 0]
    wins = [f for f in closes if _num(f.get("closedPnl")) > 0]
    realized = sum(_num(f.get("closedPnl")) for f in fills)
    fees = sum(_num(f.get("fee")) for f in fills)
    coins_traded = Counter(f.get("coin") for f in fills if f.get("coin"))

    times = sorted(int(f["time"]) for f in fills if f.get("time"))
    span_days = ((times[-1] - times[0]) / 86_400_000) if len(times) > 1 else 0.0

    fills_capped = len(fills) >= FILL_PAGE_CAP
    # Guard the divisor: a burst of fills inside one minute would otherwise
    # produce an absurd rate.
    fills_per_day = (len(fills) / max(span_days, 1 / 24)) if fills else 0.0

    return {
        "address": (address or "").lower(),
        "fills_capped": fills_capped,
        "fills_per_day": round(fills_per_day, 2),
        "equity": round(equity, 2),
        "notional": round(notional, 2),
        "margin_used": round(margin_used, 2),
        "withdrawable": round(withdrawable, 2),
        "account_leverage": round(account_leverage, 3),
        "position_count": len(positions),
        "positions": sorted(positions, key=lambda p: -p["notional"])[:12],
        "long_notional": round(long_notional, 2),
        "short_notional": round(short_notional, 2),
        "net_bias": round(net_bias, 3),
        "concentration": round(concentration, 3),
        "unrealized_pnl": round(unrealized, 2),
        "realized_pnl": round(realized, 2),
        "fees_paid": round(fees, 2),
        "fill_count": len(fills),
        "close_count": len(closes),
        "win_rate": round(len(wins) / len(closes), 4) if closes else None,
        "distinct_coins": len(coins_traded),
        "top_coins": [c for c, _ in coins_traded.most_common(5)],
        "activity_span_days": round(span_days, 2),
    }


# --- scoring --------------------------------------------------------------

WEIGHTS = {
    "capital_scale": 0.25,       # size of the book
    "realized_performance": 0.25,  # did it actually make money
    "activity": 0.20,            # is it actually trading
    "diversification": 0.15,     # spread across markets, not one bet
    "risk_control": 0.15,        # leverage sanity
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9

# Observed fill rates across real tracked accounts span 0.1 to 48,000 per
# day, so the saturation point is set high enough to keep that range
# distinguishable instead of pinning every active account at 1.0.
SATURATION = {"equity": 10_000_000.0, "fills_per_day": 50_000.0, "coins": 15}

# userFills returns a bounded page. In production 20 of 22 tracked accounts
# came back with exactly this many fills, so the raw count is a floor
# ("at least N"), identical across every busy account, and cannot
# discriminate between them. Intensity is measured as fills per active day
# instead, which stays meaningful under the cap: 2,000 fills across a day is
# a very different account from 2,000 across three years.
FILL_PAGE_CAP = 2000

# Leverage bands. Perps are leveraged by design, so leverage is not itself
# a negative - but an account running 20x+ is one wick from liquidation and
# should not rank alongside a disciplined book.
LEVERAGE_IDEAL_MAX = 5.0
LEVERAGE_DANGER = 20.0

MIN_FILLS_FOR_SIGNAL = 5


def _log_norm(value, saturation):
    if not value or value <= 0:
        return 0.0
    return min(1.0, math.log1p(value) / math.log1p(saturation))


def components(f):
    capital_scale = _log_norm(f["equity"], SATURATION["equity"])

    # Realized performance: win rate blended with whether the account is
    # actually up. Win rate alone is not enough - the vendor data in this
    # repo showed win rate and PnL correlating at r = -0.001.
    win_rate = f["win_rate"]
    pnl_positive = 1.0 if f["realized_pnl"] > 0 else 0.0
    if win_rate is None:
        realized_performance = 0.0
    else:
        realized_performance = 0.5 * min(1.0, win_rate / 0.6) + 0.5 * pnl_positive

    intensity = _log_norm(f.get("fills_per_day", 0), SATURATION["fills_per_day"])

    if f.get("fills_capped"):
        # The span covers only the returned page, not the account's
        # lifetime. Treating it as longevity actively inverts the signal:
        # the busiest accounts fill their page in hours and would look
        # newest, while a dormant account's page stretches over years.
        # Longevity is simply unknown here, so it is held neutral.
        longevity = 0.5
    else:
        longevity = min(1.0, f["activity_span_days"] / 90.0)

    activity = 0.7 * intensity + 0.3 * longevity

    diversification = 0.5 * _log_norm(f["distinct_coins"], SATURATION["coins"]) + \
                      0.5 * (1.0 - min(1.0, f["concentration"]))

    lev = f["account_leverage"]
    if lev <= 0:
        risk_control = 0.5              # no open risk: neither good nor bad
    elif lev <= LEVERAGE_IDEAL_MAX:
        risk_control = 1.0
    elif lev >= LEVERAGE_DANGER:
        risk_control = 0.0
    else:
        risk_control = 1.0 - (lev - LEVERAGE_IDEAL_MAX) / (LEVERAGE_DANGER - LEVERAGE_IDEAL_MAX)

    return {
        "capital_scale": round(capital_scale, 4),
        "realized_performance": round(realized_performance, 4),
        "activity": round(activity, 4),
        "diversification": round(diversification, 4),
        "risk_control": round(risk_control, 4),
    }


def score(f, elite=80, watch=65, candidate=45):
    comps = components(f)
    value = sum(WEIGHTS[k] * v for k, v in comps.items()) * 100

    insufficient = f["fill_count"] < MIN_FILLS_FOR_SIGNAL and f["position_count"] == 0
    if insufficient:
        value = min(value, 20.0)

    value = round(max(0.0, min(100.0, value)), 1)
    tier = ("elite" if value >= elite else "watch" if value >= watch
            else "candidate" if value >= candidate else "archive")
    return {
        "smart_score": value,
        "tier": tier,
        "components": comps,
        "weights": dict(WEIGHTS),
        "insufficient_history": insufficient,
        "scorer_version": "hyperliquid_v1",
    }


def label(f):
    """Rule-based labels for a perps account, each with its evidence."""
    labels = []

    def add(name, confidence, evidence):
        labels.append({"name": name, "confidence": confidence, "evidence": evidence})

    if f["equity"] >= 1_000_000:
        add("whale", 0.9, f"${f['equity']:,.0f} account equity")
    if f["fill_count"] >= MIN_FILLS_FOR_SIGNAL or f["position_count"] > 0:
        add("perps_trader", 0.9,
            f"{f['position_count']} open position(s), {f['fill_count']} recent fill(s)")

    lev = f["account_leverage"]
    if lev >= LEVERAGE_DANGER:
        add("high_leverage", 0.9, f"{lev:.1f}x account leverage - liquidation risk")
    elif lev >= 10:
        add("high_leverage", 0.6, f"{lev:.1f}x account leverage")

    if f["position_count"] and abs(f["net_bias"]) >= 0.8:
        side = "long" if f["net_bias"] > 0 else "short"
        add(f"{side}_bias", round(abs(f["net_bias"]), 2),
            f"{abs(f['net_bias']):.0%} of gross notional is {side}")
    elif f["position_count"] >= 2 and abs(f["net_bias"]) <= 0.2:
        add("market_neutral", 0.7,
            f"long/short balanced (net bias {f['net_bias']:+.2f})")

    if f["position_count"] >= 2 and f["concentration"] >= 0.8:
        add("concentrated", round(f["concentration"], 2),
            f"{f['concentration']:.0%} of notional in one market "
            f"({f['positions'][0]['coin'] if f['positions'] else '?'})")

    if f["close_count"] >= 10 and f["win_rate"] is not None:
        if f["win_rate"] >= 0.55 and f["realized_pnl"] > 0:
            add("smart_money", round(f["win_rate"], 2),
                f"{f['win_rate']:.0%} win rate over {f['close_count']} closes, "
                f"${f['realized_pnl']:,.0f} realized")
        elif f["realized_pnl"] < 0:
            add("unprofitable", 0.7,
                f"${f['realized_pnl']:,.0f} realized over {f['close_count']} closes")

    if f["fill_count"] >= 200 and f["distinct_coins"] >= 8:
        add("market_maker", 0.6,
            f"{f['fill_count']} fills across {f['distinct_coins']} markets")

    labels.sort(key=lambda item: item["confidence"], reverse=True)
    return labels


def enrich(address):
    """Fetch, extract, score and label one Hyperliquid account."""
    state = get_state(address)
    try:
        fills = get_fills(address)
    except HyperliquidError as exc:
        # Positions alone are still worth recording; fills are a bonus.
        print(f"    (fills unavailable for {address}: {exc})")
        fills = []

    features = extract(address, state, fills)
    result = score(features)
    return {
        "address": features["address"],
        "chain": "hyperliquid",
        "features": features,
        "labels": label(features),
        **result,
    }

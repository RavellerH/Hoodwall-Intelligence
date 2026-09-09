"""Blockscout API client.

Everything that touches the chain goes through here so retries, timeouts,
rate-limiting politeness and response-shape quirks are handled in one place.
Scheduled jobs have no human to hit "retry", so transient failures are
absorbed here and only permanent ones surface to the caller.
"""
import time

import requests

from . import config

_session = None


def _get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        # A browser-like User-Agent matters: Blockscout hosts sit behind a
        # CDN that rejects obvious bot agents outright.
        _session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        })
        if config.BLOCKSCOUT_API_KEY:
            _session.headers["Authorization"] = f"Bearer {config.BLOCKSCOUT_API_KEY}"
    return _session


class ChainError(RuntimeError):
    """A Blockscout call that failed after exhausting retries."""


class ChainAuthError(ChainError):
    """The endpoint refused us (401/403).

    This is a configuration problem - a missing API key or a gated host -
    not a transient fault, so it is never retried and callers should abort
    the whole stage rather than repeat it for every wallet.
    """


def _get(path, params=None):
    """GET a Blockscout endpoint with exponential backoff.

    404 is returned as None rather than raised: an address Blockscout has
    never seen is a normal, expected outcome for a scraped candidate.
    """
    url = f"{config.BLOCKSCOUT_BASE.rstrip('/')}/{path.lstrip('/')}"
    delay = 1.0
    last_error = None

    for attempt in range(config.BLOCKSCOUT_RETRIES):
        try:
            response = _get_session().get(
                url, params=params, timeout=config.BLOCKSCOUT_TIMEOUT
            )
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                # Explicit rate limit - honour Retry-After when provided.
                wait = float(response.headers.get("Retry-After", delay))
                time.sleep(min(wait, 30))
                delay *= 2
                continue
            if response.status_code in (401, 403):
                # Permanent: retrying 60 wallets x 3 attempts against a gated
                # host burns minutes and changes nothing.
                raise ChainAuthError(
                    f"{response.status_code} {response.reason} for {url}. "
                    "Robinhood Chain is served through the Blockscout Pro API: set "
                    "BLOCKSCOUT_API_KEY and BLOCKSCOUT_BASE="
                    "https://api.blockscout.com/4663/api/v2"
                )
            if 400 <= response.status_code < 500:
                raise ChainError(f"{response.status_code} {response.reason} for {url}")
            response.raise_for_status()
            time.sleep(config.BLOCKSCOUT_DELAY)
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < config.BLOCKSCOUT_RETRIES - 1:
                time.sleep(delay)
                delay *= 2

    raise ChainError(f"{url} failed after {config.BLOCKSCOUT_RETRIES} attempts: {last_error}")


def address_field(value):
    """Blockscout v2 returns from/to as objects, older shapes as bare strings."""
    if isinstance(value, dict):
        return (value.get("hash") or "").lower()
    return (value or "").lower()


def is_contract(value):
    return bool(value.get("is_contract")) if isinstance(value, dict) else False


def get_address(address):
    """Address summary: balance, contract flag, tx count."""
    return _get(f"addresses/{address}")


def get_transactions(address, limit=None):
    """Recent transactions for an address, newest first."""
    payload = _get(f"addresses/{address}/transactions", params={"filter": "to|from"})
    items = (payload or {}).get("items", [])
    return items[:limit] if limit else items


def get_token_transfers(address, limit=None):
    payload = _get(f"addresses/{address}/token-transfers")
    items = (payload or {}).get("items", [])
    return items[:limit] if limit else items


def get_stats():
    return _get("stats") or {}


def get_main_page_transactions():
    """The chain's most recent transactions - the seed for direct discovery."""
    payload = _get("main-page/transactions")
    if isinstance(payload, list):
        return payload
    return (payload or {}).get("items", [])


def get_blocks():
    payload = _get("blocks", params={"type": "block"})
    return (payload or {}).get("items", [])


def get_block_transactions(block_number):
    payload = _get(f"blocks/{block_number}/transactions")
    return (payload or {}).get("items", [])


def to_native(raw_value):
    """Convert a raw wei-style integer string to a float of native units."""
    try:
        return int(raw_value) / (10 ** config.NATIVE_DECIMALS)
    except (TypeError, ValueError):
        return 0.0

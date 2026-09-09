"""Centralized configuration, sourced entirely from environment variables.

In the cloud there is no .env file: GitHub Actions injects secrets as env
vars. python-dotenv is still loaded when present so the pipeline can be run
locally against a .env for debugging, but nothing requires it.
"""
import os
from pathlib import Path

try:  # optional - only used for local debugging runs
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - CI installs no dotenv
    pass


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name, default):
    try:
        return int(_env(name, default))
    except (TypeError, ValueError):
        return int(default)


def _bool(name, default=False):
    return str(_env(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


def require(name):
    value = _env(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set it as a GitHub Actions secret (or in .env for local runs)."
        )
    return value


# --- Paths -----------------------------------------------------------------
# ROOT is the repository root; the JSON store and published site both live
# inside it so a workflow can commit them back.
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(_env("DATA_DIR", ROOT / "data"))
SITE_DATA_DIR = Path(_env("SITE_DATA_DIR", ROOT / "site" / "data"))

# --- Chain data ------------------------------------------------------------
# Robinhood Chain (chain id 4663) is served through Blockscout's multichain
# Pro API. The per-instance host robinhoodchain.blockscout.com/api/v2 returns
# 403 for programmatic access, so the Pro API is the default.
BLOCKSCOUT_CHAIN_ID = _env("BLOCKSCOUT_CHAIN_ID", "4663")
BLOCKSCOUT_BASE = _env(
    "BLOCKSCOUT_BASE", f"https://api.blockscout.com/{BLOCKSCOUT_CHAIN_ID}/api/v2"
)
BLOCKSCOUT_API_KEY = _env("BLOCKSCOUT_API_KEY")
BLOCKSCOUT_TIMEOUT = _int("BLOCKSCOUT_TIMEOUT", 20)
BLOCKSCOUT_RETRIES = _int("BLOCKSCOUT_RETRIES", 3)
# Politeness delay between Blockscout calls, in seconds.
# Free tier allows 5 req/s; 0.3s leaves margin for retry bursts.
BLOCKSCOUT_DELAY = float(_env("BLOCKSCOUT_DELAY", "0.3"))
CHAIN_EXPLORER_URL = _env(
    "CHAIN_EXPLORER_URL", "https://robinhoodchain.blockscout.com"
)
NATIVE_SYMBOL = _env("NATIVE_SYMBOL", "ETH")
NATIVE_DECIMALS = _int("NATIVE_DECIMALS", 18)

# --- hood.vantis.sh source -------------------------------------------------
HOOD_BASE_URL = _env("HOOD_BASE_URL", "https://hood.vantis.sh")
# When true the scraper skips the plain-HTTP attempt and goes straight to a
# headless browser. Leave false: the HTTP path is ~20x faster in CI.
HOOD_FORCE_BROWSER = _bool("HOOD_FORCE_BROWSER", False)

# --- bizyugoscan.com source ------------------------------------------------
BIZYUGOSCAN_BASE_URL = _env("BIZYUGOSCAN_BASE_URL", "https://bizyugoscan.com")
BIZYUGOSCAN_FORCE_BROWSER = _bool("BIZYUGOSCAN_FORCE_BROWSER", False)

# --- Telegram --------------------------------------------------------------
TG_API_ID = _env("TG_API_ID")
TG_API_HASH = _env("TG_API_HASH")
# StringSession minted once locally by scripts/mint_telegram_session.py and
# stored as a repo secret. There is no interactive login in CI.
TG_SESSION_STRING = _env("TG_SESSION_STRING")
TG_SOURCE = _env("TG_SOURCE")
TG_POLL_LIMIT = _int("TG_POLL_LIMIT", 200)
TG_BACKFILL_LIMIT = _int("TG_BACKFILL_LIMIT", 1000)

TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _env("TELEGRAM_CHAT_ID") or _env("YOUR_TELEGRAM_USER_ID")

# --- Scoring ---------------------------------------------------------------
ELITE_SCORE = _int("ELITE_SCORE", 80)
WATCH_SCORE = _int("WATCH_SCORE", 65)
CANDIDATE_SCORE = _int("CANDIDATE_SCORE", 45)

# Budget guards so a scheduled run always terminates well inside the job limit.
MAX_ENRICH_PER_RUN = _int("MAX_ENRICH_PER_RUN", 60)
MAX_EVENTS_PER_WALLET = _int("MAX_EVENTS_PER_WALLET", 100)
MAX_DISCOVERY_BLOCKS = _int("MAX_DISCOVERY_BLOCKS", 20)

# Flow tracing fans out multiplicatively, so it gets its own hard ceilings:
# depth is how many hops from a seed, addresses is the total examined per
# run whatever the depth, and pages caps how much history is read per
# address (each page is one API call).
MAX_FLOW_DEPTH = _int("MAX_FLOW_DEPTH", 2)
MAX_FLOW_ADDRESSES = _int("MAX_FLOW_ADDRESSES", 120)
MAX_FLOW_PAGES = _int("MAX_FLOW_PAGES", 3)

# --- Publishing ------------------------------------------------------------
SITE_TITLE = _env("SITE_TITLE", "Hoodwall Intelligence")
# Keep raw Telegram text out of published artifacts even on a public site.
PUBLISH_RAW_TEXT = _bool("PUBLISH_RAW_TEXT", False)

"""Centralized environment configuration for the wallet monitor services.

Loads .env once and exposes typed settings so individual scripts don't each
duplicate os.environ / load_dotenv boilerplate.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def _env(name, default=None):
    return os.environ.get(name, default)


def require(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Copy .env.example to .env and fill it in."
        )
    return value


# Google Sheets / Drive
GOOGLE_SHEET_ID = _env("GOOGLE_SHEET_ID")
GOOGLE_DRIVE_FOLDER_ID = _env("GOOGLE_DRIVE_FOLDER_ID")
GOOGLE_SERVICE_ACCOUNT_KEY = _env("GOOGLE_SERVICE_ACCOUNT_KEY", "service-account.json")

# Telegram userbot (Telethon) - reads the source channel
TG_API_ID = _env("TG_API_ID")
TG_API_HASH = _env("TG_API_HASH")
TG_PHONE = _env("TG_PHONE")
TG_SOURCE = _env("TG_SOURCE")

# Telegram alert bot - sends digests to you
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN")
YOUR_TELEGRAM_USER_ID = _env("YOUR_TELEGRAM_USER_ID")

# Ollama (local LLM server)
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5:7b")
OLLAMA_EMBED_MODEL = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Blockscout on-chain data API
BLOCKSCOUT_BASE = _env("BLOCKSCOUT_BASE", "https://robinhoodchain.blockscout.com/api/v2")

# hood.vantis.sh scraper target
HOOD_BASE_URL = _env("HOOD_BASE_URL", "https://hood.vantis.sh")

# Screen OCR (optional fallback ingestion source)
OCR_ENABLED = _env("OCR_ENABLED", "false").lower() == "true"
OCR_CAPTURE_INTERVAL = int(_env("OCR_CAPTURE_INTERVAL", "300"))
OCR_MONITOR_REGION = {
    "left": int(_env("OCR_MONITOR_LEFT", "100")),
    "top": int(_env("OCR_MONITOR_TOP", "100")),
    "width": int(_env("OCR_MONITOR_WIDTH", "800")),
    "height": int(_env("OCR_MONITOR_HEIGHT", "600")),
}
TESSERACT_PATH = _env("TESSERACT_PATH")

# Smart Score tier thresholds
ELITE_SCORE = int(_env("ELITE_SCORE", "80"))
WATCH_SCORE = int(_env("WATCH_SCORE", "65"))
CANDIDATE_SCORE = int(_env("CANDIDATE_SCORE", "45"))

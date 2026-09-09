#!/usr/bin/env python3
"""One-time helper: mint a Telethon StringSession for use in CI.

GitHub Actions cannot do an interactive Telegram login, so you log in once
here on your own machine and store the resulting string as the
TG_SESSION_STRING repository secret.

    pip install telethon
    TG_API_ID=... TG_API_HASH=... python scripts/mint_telegram_session.py

The printed string grants full read access to your Telegram account. Treat
it exactly like a password: paste it straight into the GitHub secret, never
into a file, a commit, or a chat window.
"""
import os
import sys

try:
    from telethon import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    sys.exit("telethon is not installed. Run: pip install telethon")

api_id = os.environ.get("TG_API_ID")
api_hash = os.environ.get("TG_API_HASH")

if not (api_id and api_hash):
    sys.exit(
        "Set TG_API_ID and TG_API_HASH first (get them from https://my.telegram.org).\n"
        "  TG_API_ID=12345 TG_API_HASH=abc... python scripts/mint_telegram_session.py"
    )

with TelegramClient(StringSession(), int(api_id), api_hash) as client:
    print("\n" + "=" * 70)
    print("Session string (store as the TG_SESSION_STRING secret, then clear")
    print("your terminal scrollback - anyone holding this can read your account):")
    print("=" * 70)
    print(client.session.save())
    print("=" * 70)

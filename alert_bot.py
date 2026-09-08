"""Sends a daily Telegram digest of elite / watch-list wallets."""
import asyncio

from telegram import Bot

import config
from google_sheets import get_all_rows

TOP_N = 5


def _latest_by_address(rows):
    """wallets_updated is append-only (one row per score run); keep only the
    most recent row per address."""
    latest = {}
    for row in rows:
        if row:
            latest[row[0]] = row
    return list(latest.values())


async def send_daily_digest():
    print("Sending daily digest...")

    rows = get_all_rows(config.GOOGLE_SHEET_ID, "wallets_updated")
    wallets = _latest_by_address(rows[1:] if rows else [])

    elite = sorted((w for w in wallets if w[2] == "elite"), key=lambda w: float(w[1]), reverse=True)
    watch = sorted((w for w in wallets if w[2] == "watch"), key=lambda w: float(w[1]), reverse=True)

    lines = ["Robinhood Chain Wallet Monitor - Daily Digest", "", f"Elite Wallets ({len(elite)})"]
    for w in elite[:TOP_N]:
        lines.append(f"- `{w[0]}` - Score: {w[1]}")
    if len(elite) > TOP_N:
        lines.append(f"... and {len(elite) - TOP_N} more")

    lines += ["", f"Watch List ({len(watch)})"]
    for w in watch[:TOP_N]:
        lines.append(f"- `{w[0]}` - Score: {w[1]}")
    if len(watch) > TOP_N:
        lines.append(f"... and {len(watch) - TOP_N} more")

    lines += ["", "Full dashboard: http://localhost:8501"]

    bot = Bot(token=config.require("TELEGRAM_BOT_TOKEN"))
    await bot.send_message(
        chat_id=int(config.require("YOUR_TELEGRAM_USER_ID")),
        text="\n".join(lines),
        parse_mode="Markdown",
    )
    print("Digest sent!")


if __name__ == "__main__":
    asyncio.run(send_daily_digest())

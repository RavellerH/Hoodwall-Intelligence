"""Reads a Telegram channel via Telethon (MTProto userbot, read-only, no
admin needed) and writes extracted messages / addresses straight into
Google Sheets. A small local state file tracks the last processed message
id per source so restarts don't re-backfill or duplicate rows.
"""
import json
import os
from datetime import datetime, timezone

from telethon import TelegramClient, events

import config
from google_sheets import append_row, append_rows, get_existing_addresses
from regex_utils import ADDRESS_RE, TX_HASH_RE

STATE_PATH = os.path.join(os.path.dirname(__file__), "data", "telethon_state.json")
BACKFILL_LIMIT = 3000

client = TelegramClient(
    "wallet_monitor_session", int(config.require("TG_API_ID")), config.require("TG_API_HASH")
)


def _load_state():
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f)


def normalize_message(message, source_id):
    text = message.message or ""
    return {
        "message_id": message.id,
        "source_chat_id": str(source_id),
        "timestamp": message.date.astimezone(timezone.utc).isoformat(),
        "text": text,
        "addresses": sorted(set(ADDRESS_RE.findall(text))),
        "tx_hashes": sorted(set(TX_HASH_RE.findall(text))),
        "forwarded": bool(message.fwd_from),
        "reply_to_id": getattr(message.reply_to, "reply_to_msg_id", None),
    }


def record_message(row):
    append_row(
        config.GOOGLE_SHEET_ID,
        "telegram_messages",
        [
            row["message_id"], row["source_chat_id"], row["timestamp"], row["text"],
            json.dumps(row["addresses"]), json.dumps(row["tx_hashes"]),
            row["forwarded"], row["reply_to_id"],
        ],
    )

    if row["addresses"]:
        existing = get_existing_addresses(config.GOOGLE_SHEET_ID, "candidates")
        new_addresses = [a for a in row["addresses"] if a not in existing]
        if new_addresses:
            now = datetime.now(timezone.utc).isoformat()
            append_rows(
                config.GOOGLE_SHEET_ID,
                "candidates",
                [[a, now, "telegram", row["timestamp"], "candidate", now] for a in new_addresses],
            )


async def main():
    source_name = config.require("TG_SOURCE")
    source = await client.get_entity(source_name)
    state = _load_state()
    source_key = str(source.id)
    last_message_id = state.get(source_key, 0)

    if last_message_id == 0:
        print(f"Backfilling last {BACKFILL_LIMIT} messages from {source_name}...")
        messages = [m async for m in client.iter_messages(source, limit=BACKFILL_LIMIT)]
        for message in reversed(messages):  # oldest first
            record_message(normalize_message(message, source.id))
            last_message_id = max(last_message_id, message.id)
        state[source_key] = last_message_id
        _save_state(state)
        print(f"Backfill complete: {len(messages)} messages processed")

    @client.on(events.NewMessage(chats=source))
    async def handler(event):
        record_message(normalize_message(event.message, source.id))
        state[source_key] = max(state.get(source_key, 0), event.message.id)
        _save_state(state)

    print(f"Listening for new messages on {source_name}...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())

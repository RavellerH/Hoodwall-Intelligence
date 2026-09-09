"""Telegram channel ingestion via Telethon.

Cloud difference from the local version: there is no long-lived listener and
no interactive login. Authentication uses a StringSession minted once by
scripts/mint_telegram_session.py and stored as a repo secret, and each
scheduled run polls forward from the last message id it recorded.
"""
import asyncio

from .. import config, masks
from ..regexes import find_addresses, find_tx_hashes
from ..store import get_state, set_state, upsert, utcnow

SOURCE_NAME = "telegram"
CURSOR_KEY = "telegram_last_message_id"


def _configured():
    return all([config.TG_API_ID, config.TG_API_HASH,
                config.TG_SESSION_STRING, config.TG_SOURCE])


async def _collect():
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(
        StringSession(config.TG_SESSION_STRING),
        int(config.TG_API_ID),
        config.TG_API_HASH,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError(
                "TG_SESSION_STRING is not authorized. Re-mint it with "
                "scripts/mint_telegram_session.py and update the secret."
            )

        source = await client.get_entity(config.TG_SOURCE)
        last_id = int(get_state(CURSOR_KEY, 0) or 0)

        # First ever run backfills history; later runs only read what is new.
        if last_id:
            limit, min_id = config.TG_POLL_LIMIT, last_id
            print(f"[telegram] polling {config.TG_SOURCE} after message {last_id}")
        else:
            limit, min_id = config.TG_BACKFILL_LIMIT, 0
            print(f"[telegram] first run: backfilling up to {limit} messages")

        messages = []
        async for message in client.iter_messages(source, limit=limit, min_id=min_id):
            messages.append(message)
        return list(reversed(messages)), last_id  # oldest first
    finally:
        await client.disconnect()


def run():
    if not _configured():
        print("[telegram] not configured (TG_* secrets missing); skipping")
        return 0

    try:
        messages, last_id = asyncio.run(_collect())
    except Exception as exc:
        print(f"[telegram] FAILED: {exc}")
        return 0

    if not messages:
        print("[telegram] no new messages")
        return 0

    now = utcnow()
    message_rows, candidate_rows = [], {}
    seen_masks = {}
    highest = last_id

    for message in messages:
        text = message.message or ""
        addresses = find_addresses(text)
        # Alert feeds usually print addresses truncated ("0x3475…3a12"), which
        # no 40-hex regex can see. Capture those separately for resolution.
        found_masks = masks.find_masks(text)
        row = {
            "message_id": message.id,
            "timestamp": message.date.isoformat() if message.date else now,
            "addresses": addresses,
            "tx_hashes": find_tx_hashes(text),
            "masks": [m[0] for m in found_masks],
            "forwarded": bool(message.fwd_from),
        }
        # Raw text is retained locally for debugging but is never published;
        # publish.py drops it unless PUBLISH_RAW_TEXT is explicitly enabled.
        row["text"] = text
        message_rows.append(row)
        highest = max(highest, message.id)

        for key, prefix, suffix in found_masks:
            seen_masks[key] = (key, prefix, suffix)

        for address in addresses:
            candidate_rows.setdefault(address, {
                "address": address,
                "first_seen_at": row["timestamp"],
                "last_seen_at": now,
                "source": SOURCE_NAME,
                "source_url": f"{config.TG_SOURCE}#{message.id}",
                "status": "candidate",
            })

    upsert("telegram_messages", message_rows)
    new = upsert("candidates", list(candidate_rows.values()))
    new_masks = masks.record(
        list(seen_masks.values()), SOURCE_NAME, source_url=str(config.TG_SOURCE))
    set_state(CURSOR_KEY, highest)

    print(f"[telegram] {len(message_rows)} message(s), "
          f"{len(candidate_rows)} full address(es) ({new} new), "
          f"{len(seen_masks)} masked ({new_masks} new)")
    return new


if __name__ == "__main__":
    run()

"""Calculates the Smart Score and behavioral labels for every enriched
wallet using the local LLM (Ollama)."""
import json
from datetime import datetime, timezone

import config
from google_sheets import append_row, get_all_rows
from llm_utils import categorize_wallet

MAX_EVENTS_FOR_SCORING = 20


def _tier_for(score: float) -> str:
    if score >= config.ELITE_SCORE:
        return "elite"
    if score >= config.WATCH_SCORE:
        return "watch"
    if score >= config.CANDIDATE_SCORE:
        return "candidate"
    return "archive"


def calculate_scores():
    print("Calculating scores...")

    wallets = get_all_rows(config.GOOGLE_SHEET_ID, "wallets")
    events = get_all_rows(config.GOOGLE_SHEET_ID, "events")

    for wallet in wallets[1:]:
        address = wallet[0]
        wallet_events = [e for e in events[1:] if len(e) > 2 and e[2] == address]

        wallet_data = {"address": address, "address_type": wallet[1] if len(wallet) > 1 else "unknown"}
        event_summaries = [
            {
                "tx_hash": (e[0][:10] + "...") if e[0] else "",
                "event_type": e[4] if len(e) > 4 else "",
                "event_time": e[3] if len(e) > 3 else "",
                "amount_usd": e[8] if len(e) > 8 else "",
                "counterparty": e[9] if len(e) > 9 else "",
            }
            for e in wallet_events[:MAX_EVENTS_FOR_SCORING]
        ]

        result = categorize_wallet(wallet_data, event_summaries)
        smart_score = result.get("smart_score", 0)
        tier = _tier_for(smart_score)
        now = datetime.now(timezone.utc).isoformat()

        append_row(
            config.GOOGLE_SHEET_ID,
            "wallets_updated",
            [
                address, smart_score, tier,
                json.dumps(result.get("labels", [])),
                json.dumps(result.get("evidence", [])),
                now,
            ],
        )

        for label in result.get("labels", []):
            append_row(
                config.GOOGLE_SHEET_ID,
                "labels",
                [address, label["name"], label["confidence"], json.dumps(result.get("evidence", [])), now, "llm_v1"],
            )

        print(f"Scored {address}: {smart_score} ({tier})")


if __name__ == "__main__":
    calculate_scores()

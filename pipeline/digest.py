"""Daily Telegram digest.

Uses the plain Bot HTTP API rather than a library: one POST, no async, no
extra dependency, and nothing that needs a session file.
"""
import requests

from . import config
from .store import load

TOP_N = 8
API_TIMEOUT = 20


def _format(rows, site_url):
    scored = sorted(rows.values(), key=lambda r: r["smart_score"], reverse=True)
    elite = [r for r in scored if r["tier"] == "elite"]
    watch = [r for r in scored if r["tier"] == "watch"]

    def block(title, items):
        lines = [f"*{title}* ({len(items)})"]
        if not items:
            lines.append("_none_")
            return lines
        for row in items[:TOP_N]:
            labels = ", ".join(l["name"] for l in row["labels"][:2]) or "unlabeled"
            lines.append(f"`{row['address'][:10]}…{row['address'][-6:]}` "
                         f"*{row['smart_score']:.0f}* — {labels}")
        if len(items) > TOP_N:
            lines.append(f"_…and {len(items) - TOP_N} more_")
        return lines

    lines = ["*Hoodwall Intelligence — Daily Digest*", ""]
    lines += block("Elite", elite) + [""]
    lines += block("Watch", watch) + [""]
    lines.append(f"Tracking {len(scored)} scored wallet(s).")
    if site_url:
        lines.append(f"[Open dashboard]({site_url})")
    return "\n".join(lines)


def run():
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        print("[digest] not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); skipping")
        return False

    scores = load("scores")
    if not scores:
        print("[digest] nothing scored yet; skipping")
        return False

    import os
    site_url = os.environ.get("SITE_URL", "")
    text = _format(scores, site_url)

    response = requests.post(
        f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
        json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=API_TIMEOUT,
    )
    if response.status_code != 200:
        print(f"[digest] FAILED {response.status_code}: {response.text[:300]}")
        return False

    print("[digest] sent")
    return True


if __name__ == "__main__":
    run()

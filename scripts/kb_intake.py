#!/usr/bin/env python3
"""Turn raw pasted feed text into draft knowledge-base records.

Drop a Telegram card, a vendor post or any raw text into knowledge/inbox/
and run this. It extracts addresses (full and masked), tokens and handles,
and writes a DRAFT yml file for you to review and move into wallets/.

Drafts are never written straight into wallets/ - the whole point of the
knowledge base is that a human asserts what is in it.

    python scripts/kb_intake.py knowledge/inbox/*.txt
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.kb import addresses as addr  # noqa: E402
from pipeline.kb.loader import KNOWLEDGE_DIR  # noqa: E402

MASKED_RE = re.compile(r"\b(0x[a-fA-F0-9]{3,8})\s*(?:\.{2,3}|[…⋯])\s*([a-fA-F0-9]{3,8})\b")
HANDLE_RE = re.compile(r"@[A-Za-z0-9_]{2,32}")
# $TICKER, or a bare uppercase ticker in a "bought TOKEN" style line.
TOKEN_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,14})\b")


def parse(text, chain):
    family = {"solana": "svm", "bitcoin": "utxo"}.get(chain, "evm")
    full = addr.scan(text, family)
    masked = sorted({f"{p.lower()}…{s.lower()}" for p, s in MASKED_RE.findall(text)})
    # A full address also matches the masked pattern's prefix; keep them apart.
    handles = sorted(set(HANDLE_RE.findall(text)))
    tokens = sorted(set(TOKEN_RE.findall(text)))
    return full, masked, handles, tokens


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--chain", default="robinhood", help="chain these sightings belong to")
    ap.add_argument("--source", default="inbox", help="source key to record")
    args = ap.parse_args()

    drafts, summary = [], []
    for name in args.files:
        path = Path(name)
        if not path.exists():
            print(f"! {path}: not found")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        full, masked, handles, tokens = parse(text, args.chain)
        summary.append((path.name, len(full), len(masked), len(handles), len(tokens)))

        for address in full:
            drafts.append({"chain": args.chain, "address": address,
                           "source": args.source, "confidence": "reported",
                           "labels": [], "added": date.today().isoformat(),
                           "notes": f"Extracted from {path.name}. REVIEW BEFORE USE."})
        for mask in masked:
            drafts.append({"chain": args.chain, "masked": mask,
                           "source": args.source, "confidence": "reported",
                           "labels": [], "added": date.today().isoformat(),
                           "notes": f"Masked sighting from {path.name}. REVIEW BEFORE USE."})

    print(f"{'file':30} {'full':>5} {'masked':>7} {'handles':>8} {'tokens':>7}")
    for row in summary:
        print(f"{row[0][:30]:30} {row[1]:>5} {row[2]:>7} {row[3]:>8} {row[4]:>7}")

    if not drafts:
        print("\nNothing extracted.")
        return 0

    out = KNOWLEDGE_DIR / "inbox" / f"draft-{date.today().isoformat()}.yml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(drafts, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"\n{len(drafts)} draft record(s) -> {out}")
    print("Review, then move the entries you trust into knowledge/wallets/<chain>.yml")
    return 0


if __name__ == "__main__":
    sys.exit(main())

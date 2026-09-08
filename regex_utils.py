"""Shared address / tx-hash extraction patterns used by every ingestion source."""
import re

ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TX_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")

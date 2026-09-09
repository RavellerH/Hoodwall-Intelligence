"""Shared address / tx-hash extraction patterns used by every ingestion source."""
import re

ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
TX_HASH_RE = re.compile(r"\b0x[a-fA-F0-9]{64}\b")

# The zero address and common burn sinks are never interesting as candidates.
IGNORED_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


def find_addresses(text):
    """Extract unique, lowercased, non-ignored wallet addresses from text.

    Addresses are normalized to lowercase so the same wallet written in
    EIP-55 checksum form and in lowercase never becomes two rows.
    """
    if not text:
        return []
    found = {match.lower() for match in ADDRESS_RE.findall(text)}
    return sorted(found - IGNORED_ADDRESSES)


def find_tx_hashes(text):
    if not text:
        return []
    return sorted({match.lower() for match in TX_HASH_RE.findall(text)})

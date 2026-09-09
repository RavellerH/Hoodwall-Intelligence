"""Multi-chain address parsing and validation.

The original system assumed every address was EVM (0x + 40 hex). Solana
uses base58, Bitcoin uses base58 or bech32, and none of them can be
validated by the same rule - so address handling is centralized here and
keyed on the chain's `family`.
"""
import re

EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
EVM_SCAN_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")

# Base58 excludes 0, O, I and l to avoid visual ambiguity.
BASE58 = r"[1-9A-HJ-NP-Za-km-z]"
SVM_RE = re.compile(rf"^{BASE58}{{32,44}}$")
SVM_SCAN_RE = re.compile(rf"\b{BASE58}{{32,44}}\b")

# Bitcoin: P2PKH (1...), P2SH (3...), bech32/bech32m (bc1...).
BTC_LEGACY_RE = re.compile(rf"^[13]{BASE58}{{25,34}}$")
BTC_BECH32_RE = re.compile(r"^bc1[02-9ac-hj-np-z]{11,71}$")

ZERO_EVM = "0x" + "0" * 40
BURN_ADDRESSES = {ZERO_EVM, "0x000000000000000000000000000000000000dead"}


class AddressError(ValueError):
    """An address that does not match its declared chain family."""


def normalize(address, family):
    """Canonicalize an address for use as a key.

    EVM addresses are lowercased, because the same wallet written in EIP-55
    checksum form and in lowercase must never become two records. Base58 and
    bech32 are case-sensitive in ways that matter, so only bech32's defined
    lowercase form is applied and base58 is left untouched.
    """
    address = (address or "").strip()
    if not address:
        raise AddressError("empty address")

    if family == "evm":
        return address.lower()
    if family == "utxo" and address.lower().startswith("bc1"):
        return address.lower()
    return address


def is_valid(address, family):
    if not address:
        return False
    address = address.strip()
    if family == "evm":
        return bool(EVM_RE.match(address))
    if family == "svm":
        return bool(SVM_RE.match(address))
    if family == "utxo":
        return bool(BTC_LEGACY_RE.match(address) or BTC_BECH32_RE.match(address.lower()))
    return False


def validate(address, family):
    """Return the normalized address, or raise AddressError."""
    if not is_valid(address, family):
        raise AddressError(f"{address!r} is not a valid {family} address")
    return normalize(address, family)


def is_burn(address):
    return (address or "").lower() in BURN_ADDRESSES


def scan(text, family):
    """Extract candidate addresses of a family from free text.

    Base58 scanning is intentionally noisy - many random tokens match the
    character class - so SVM results should be treated as candidates needing
    confirmation, never as facts.
    """
    if not text:
        return []
    if family == "evm":
        found = {m.lower() for m in EVM_SCAN_RE.findall(text)}
        return sorted(found - BURN_ADDRESSES)
    if family == "svm":
        return sorted({m for m in SVM_SCAN_RE.findall(text) if SVM_RE.match(m)})
    if family == "utxo":
        legacy = re.findall(rf"\b[13]{BASE58}{{25,34}}\b", text)
        bech = re.findall(r"\bbc1[02-9ac-hj-np-z]{11,71}\b", text, re.IGNORECASE)
        return sorted({*legacy, *(b.lower() for b in bech)})
    return []


def shorten(address, lead=6, tail=4):
    """Display form: 0x3475aa…3a12."""
    if not address or len(address) <= lead + tail + 1:
        return address or ""
    return f"{address[:lead]}…{address[-tail:]}"

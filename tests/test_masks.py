"""Tests for masked-address parsing and resolution.

The feed this system ingests prints addresses truncated ("0x3475…3a12"), so
these paths are the difference between the Telegram source contributing
candidates and contributing nothing at all.
"""
import pytest

from pipeline import masks


def test_parses_ellipsis_character():
    found = masks.find_masks("smart money: 0x3475…3a12 bought")
    assert found == [("0x3475…3a12", "0x3475", "3a12")]


def test_parses_three_dots():
    assert masks.find_masks("0xcb4d...35c9")[0][1:] == ("0xcb4d", "35c9")


def test_parses_two_dots():
    assert masks.find_masks("0xcb4d..35c9")[0][1:] == ("0xcb4d", "35c9")


def test_normalizes_case_and_dedupes():
    found = masks.find_masks("0xAB12…CD34 and again 0xab12…cd34")
    assert len(found) == 1
    assert found[0][0] == "0xab12…cd34"


def test_rejects_weak_masks():
    """Too few revealed digits cannot identify an address."""
    assert masks.find_masks("0x12…ab") == []


def test_ignores_full_addresses():
    """A full address is handled by the normal regex, not as a mask."""
    assert masks.find_masks(f"0x{'a' * 40}") == []


def test_multiple_masks_in_one_message():
    text = "0x3475…3a12 (@Cupseyy), 0x41cc…42f5 and 0xcb4d...35c9 all bought"
    assert len(masks.find_masks(text)) == 3


@pytest.mark.parametrize("address,expected", [
    ("0x3475" + "a" * 32 + "3a12", True),
    ("0x3475" + "b" * 32 + "3a12", True),
    ("0x9999" + "a" * 32 + "3a12", False),   # wrong prefix
    ("0x3475" + "a" * 32 + "9999", False),   # wrong suffix
    ("0x3475" + "a" * 20 + "3a12", False),   # wrong length
])
def test_matches(address, expected):
    assert masks.matches("0x3475", "3a12", address) is expected


def test_mask_entropy_is_sufficient():
    """8 revealed hex digits = 32 bits, so collisions must be rare.

    This documents why single-match resolution is safe rather than a guess.
    """
    revealed_bits = 8 * 4
    universe = 1_000_000
    collision_rate = universe / (2 ** revealed_bits)
    assert collision_rate < 0.001

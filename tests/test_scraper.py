"""Generic vendor-scraper tests.

Two vendor sites now share one scraper, so the behaviour that used to be
implicit in hood.py is pinned here: masks are collected as well as full
addresses (a scanner UI that truncates an address is still publishing a
32-bit identifier), a 404 endpoint is skipped rather than fatal, and the
browser fallback runs only when HTTP found nothing at all.
"""
import pytest

from pipeline import config, store
from pipeline.sources import bizyugoscan, hood, scraper

ADDRESS = "0x" + "ab" * 20
OTHER = "0x" + "cd" * 20


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def serve(pages, monkeypatch):
    """Wire _fetch_text to a dict of url -> body; anything else 404s."""
    calls = []

    def fake(url, timeout=None):
        calls.append(url)
        return pages.get(url)
    monkeypatch.setattr(scraper, "_fetch_text", fake)
    return calls


def test_full_addresses_become_candidates(monkeypatch):
    serve({"https://x.invalid": f"<td>{ADDRESS}</td>"}, monkeypatch)
    assert scraper.run("x.invalid", "https://x.invalid", api_paths=[]) == 1
    rows = store.load("candidates")
    assert list(rows) == [ADDRESS]
    assert rows[ADDRESS]["source"] == "x.invalid"
    assert rows[ADDRESS]["status"] == "candidate"


def test_masks_are_recorded_not_dropped(monkeypatch):
    """A truncated address is a sighting; discarding it loses it entirely."""
    serve({"https://x.invalid": "<td>0x3475…3a12</td>"}, monkeypatch)
    assert scraper.run("x.invalid", "https://x.invalid", api_paths=[]) == 1
    assert store.load("candidates") == {}
    masked = store.load("masked")
    assert list(masked) == ["0x3475…3a12"]
    assert masked["0x3475…3a12"]["status"] == "unresolved"
    assert masked["0x3475…3a12"]["source"] == "x.invalid"


def test_json_endpoints_are_probed_and_404s_skipped(monkeypatch):
    calls = serve({
        "https://x.invalid": "nothing here",
        "https://x.invalid/api/wallets": f'{{"w":"{OTHER}"}}',
    }, monkeypatch)
    assert scraper.run("x.invalid", "https://x.invalid",
                       api_paths=["/api/wallets", "/api/missing"]) == 1
    assert "https://x.invalid/api/missing" in calls, "a 404 must be tried, not assumed"
    assert store.load("candidates")[OTHER]["source_url"].endswith("/api/wallets")


def test_browser_fallback_only_when_http_found_nothing(monkeypatch):
    tried = []
    monkeypatch.setattr(scraper, "_scrape_browser",
                        lambda base: (tried.append(base), ({}, []))[1])

    serve({"https://x.invalid": f"<td>{ADDRESS}</td>"}, monkeypatch)
    scraper.run("x.invalid", "https://x.invalid", api_paths=[])
    assert tried == [], "HTTP succeeded; the browser must not be launched"

    serve({"https://x.invalid": "no addresses at all"}, monkeypatch)
    scraper.run("x.invalid", "https://x.invalid", api_paths=[])
    assert tried == ["https://x.invalid"]


def test_missing_base_url_is_skipped_not_crashed(monkeypatch):
    serve({}, monkeypatch)
    assert scraper.run("x.invalid", "", api_paths=[]) == 0
    assert scraper.run("x.invalid", None, api_paths=[]) == 0


def test_both_vendor_sources_are_thin_configuration(monkeypatch):
    """Each site should be a config entry, not another copy of the scraper."""
    seen = {}

    def fake_run(source_name, base_url, api_paths=None, force_browser=False, tag=None):
        seen[tag] = (source_name, base_url)
        return 0
    monkeypatch.setattr(scraper, "run", fake_run)

    hood.run()
    bizyugoscan.run()
    assert seen["hood"] == ("hood.vantis.sh", config.HOOD_BASE_URL)
    assert seen["bizyugoscan"] == ("bizyugoscan.com", config.BIZYUGOSCAN_BASE_URL)

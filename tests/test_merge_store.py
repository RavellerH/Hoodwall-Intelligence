"""Tests for the store reconciliation used when a pipeline push is rejected.

This exists because of a real CI failure: the commit step used
`git pull --rebase` on generated JSON, which conflicts every time (both
sides rewrite the whole file), leaving the tree detached with unmerged
paths so every subsequent retry failed on "not currently on a branch".
The store is keyed JSON, so the correct reconciliation is a key-wise union.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "merge_store.py"


def write(directory, name, payload):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def run_merge(remote, local):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(remote), str(local)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def read(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


@pytest.fixture
def dirs(tmp_path):
    return tmp_path / "remote", tmp_path / "local"


def test_union_of_keys(dirs):
    remote, local = dirs
    write(remote, "candidates.json", {"a": {"address": "a"}, "b": {"address": "b"}})
    write(local, "candidates.json", {"a": {"address": "a"}, "c": {"address": "c"}})
    run_merge(remote, local)
    assert set(read(local, "candidates.json")) == {"a", "b", "c"}


def test_local_wins_on_shared_key(dirs):
    """The pushing run's record is the newer view."""
    remote, local = dirs
    write(remote, "wallets.json", {"a": {"address": "a", "score": 10}})
    write(local, "wallets.json", {"a": {"address": "a", "score": 99}})
    run_merge(remote, local)
    assert read(local, "wallets.json")["a"]["score"] == 99


def test_remote_only_table_is_preserved(dirs):
    """A table this run never touched must survive reconciliation."""
    remote, local = dirs
    write(remote, "hl_scores.json", {"x": {"address": "x"}})
    write(local, "candidates.json", {"a": {"address": "a"}})
    run_merge(remote, local)
    assert read(local, "hl_scores.json") == {"x": {"address": "x"}}
    assert read(local, "candidates.json") == {"a": {"address": "a"}}


def test_local_only_table_is_kept(dirs):
    remote, local = dirs
    write(remote, "candidates.json", {"a": {"address": "a"}})
    write(local, "candidates.json", {"a": {"address": "a"}})
    write(local, "scores.json", {"a": {"address": "a", "smart_score": 50}})
    run_merge(remote, local)
    assert read(local, "scores.json")["a"]["smart_score"] == 50


def test_corrupt_remote_file_does_not_lose_local_data(dirs):
    """A half-written remote file must not take this run's output with it."""
    remote, local = dirs
    remote.mkdir(parents=True)
    (remote / "candidates.json").write_text("{ this is not json", encoding="utf-8")
    write(local, "candidates.json", {"a": {"address": "a"}})
    output = run_merge(remote, local)
    assert "unreadable" in output
    assert read(local, "candidates.json") == {"a": {"address": "a"}}


def test_empty_dirs_are_harmless(dirs):
    remote, local = dirs
    remote.mkdir(parents=True)
    local.mkdir(parents=True)
    assert "nothing to merge" in run_merge(remote, local)


def test_output_is_stable_and_sorted(dirs):
    """Committed JSON must diff cleanly, so key order cannot wobble."""
    remote, local = dirs
    write(remote, "candidates.json", {"z": {"address": "z"}})
    write(local, "candidates.json", {"a": {"address": "a"}})
    run_merge(remote, local)
    first = (local / "candidates.json").read_text(encoding="utf-8")
    run_merge(remote, local)
    assert (local / "candidates.json").read_text(encoding="utf-8") == first
    assert first.index('"a"') < first.index('"z"')

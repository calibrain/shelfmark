"""The ledger of torrents this instance added."""

from __future__ import annotations

import pytest

from shelfmark.download import torrent_ledger


@pytest.fixture(autouse=True)
def _ledger_file(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent_ledger, "_path", lambda: tmp_path / "torrent_ledger.db")


def test_a_recorded_hash_is_kept_lower_case_with_its_task():
    torrent_ledger.record("ABCDEF", "task-1")

    assert torrent_ledger.entries() == {"abcdef": "task-1"}


def test_recording_a_hash_again_moves_it_to_the_new_task():
    torrent_ledger.record("abc", "task-1")
    torrent_ledger.record("ABC", "task-2")

    assert torrent_ledger.entries() == {"abc": "task-2"}


def test_claiming_moves_a_known_hash_and_never_adds_an_unknown_one():
    torrent_ledger.record("abc", "task-1")

    torrent_ledger.claim("abc", "task-2")
    torrent_ledger.claim("unknown", "task-2")

    assert torrent_ledger.entries() == {"abc": "task-2"}


def test_forget_drops_only_the_named_hashes():
    torrent_ledger.record("a", "t1")
    torrent_ledger.record("b", "t2")

    torrent_ledger.forget("A")
    torrent_ledger.forget()
    torrent_ledger.forget("")

    assert torrent_ledger.entries() == {"b": "t2"}


def test_an_unusable_ledger_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(torrent_ledger, "_path", lambda: tmp_path / "missing" / "ledger.db")

    torrent_ledger.record("a", "t1")
    torrent_ledger.claim("a", "t2")
    torrent_ledger.forget("a")

    assert torrent_ledger.entries() == {}

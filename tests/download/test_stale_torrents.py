"""Dead torrents nothing is watching are removed; everything else is left alone."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shelfmark.download import stale_torrents as mod

NOW = 1_000_000.0
HOUR = 3600.0


def _torrent(name="Book", *, state="stalledDL", progress=0.3, idle=10.0, h="h1", **kw):
    return SimpleNamespace(
        name=name,
        hash=h,
        state=state,
        progress=progress,
        last_activity=NOW - idle * HOUR,
        added_on=NOW - 100 * HOUR,
        **kw,
    )


def _find(torrents, *, owned=(), hours=6.0):
    return mod.find_stale(list(torrents), owned_titles=set(owned), idle_hours=hours, now=NOW)


def test_a_stalled_torrent_idle_for_hours_with_no_owner_is_stale():
    assert [t.name for t in _find([_torrent()])] == ["Book"]


@pytest.mark.parametrize("state", ["stalledDL", "metaDL"])
def test_a_torrent_stuck_fetching_data_or_metadata_counts(state):
    assert _find([_torrent(state=state)])


@pytest.mark.parametrize(
    "state", ["downloading", "queuedDL", "pausedDL", "uploading", "stalledUP", "forcedDL"]
)
def test_any_other_state_is_left_alone(state):
    assert _find([_torrent(state=state)]) == []


def test_a_finished_torrent_is_left_alone():
    assert _find([_torrent(progress=1.0)]) == []


def test_a_torrent_active_within_the_window_is_left_alone():
    assert _find([_torrent(idle=2.0)]) == []


def test_a_torrent_a_running_download_owns_is_left_alone():
    assert (
        _find([_torrent(name="Cross Down: An Alex Cross")], owned={"cross down an alex cross"})
        == []
    )


def test_ownership_ignores_case_and_punctuation():
    assert _find([_torrent(name="The Vampire Lestat!")], owned={"the vampire lestat"}) == []


def test_without_activity_the_time_it_was_added_decides():
    torrent = _torrent()
    torrent.last_activity = 0

    assert _find([torrent])  # added 100 hours ago


def test_a_torrent_with_no_times_at_all_is_not_judged():
    torrent = _torrent()
    torrent.last_activity = 0
    torrent.added_on = 0

    assert _find([torrent]) == []


def test_switched_off_finds_nothing():
    assert _find([_torrent()], hours=0) == []


def test_run_removes_stale_torrents_with_their_data_in_each_category(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    client = MagicMock()
    client.list_torrents.side_effect = lambda cat: {
        "AudioBook": [_torrent("Dead", h="d1"), _torrent("Live", h="l1", state="downloading")],
        "books": [_torrent("Also dead", h="d2")],
    }[cat]
    client.remove.return_value = True

    report = mod.run(
        client,
        categories=["books", "AudioBook", "AudioBook", ""],
        owned_titles=lambda: set(),
        now=NOW,
    )

    assert sorted(report.removed) == ["Also dead", "Dead"]
    removed = sorted(call.args[0] for call in client.remove.call_args_list)
    assert removed == ["d1", "d2"]
    assert all(call.kwargs == {"delete_files": True} for call in client.remove.call_args_list)
    assert client.list_torrents.call_count == 2  # a repeated or empty category is read once


def test_run_does_nothing_when_switched_off(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 0.0)
    client = MagicMock()

    report = mod.run(client, categories=["AudioBook"], owned_titles=lambda: set(), now=NOW)

    assert report.removed == []
    client.list_torrents.assert_not_called()


def test_a_failed_removal_is_not_reported(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    client = MagicMock()
    client.list_torrents.return_value = [_torrent()]
    client.remove.return_value = False

    assert mod.run(client, categories=["x"], owned_titles=lambda: set(), now=NOW).removed == []


def test_the_setting_reads_as_hours_and_bad_values_mean_off(monkeypatch):
    for raw, expected in (("6", 6.0), (0, 0.0), (None, 0.0), ("junk", 0.0), (-3, 0.0)):
        monkeypatch.setattr(mod.config, "get", lambda _k, _d=None, raw=raw: raw)
        assert mod.stale_hours() == expected


def test_the_qbittorrent_client_lists_a_category_and_returns_nothing_on_failure():
    from shelfmark.download.clients.qbittorrent import QBittorrentClient

    client = object.__new__(QBittorrentClient)
    seen = []

    def listing(category):
        seen.append(category)
        return ([_torrent()], None) if category == "ok" else ([], "Cannot connect")

    client._list_torrents_by_category = listing  # type: ignore[method-assign]

    assert [t.name for t in client.list_torrents("ok")] == ["Book"]
    assert client.list_torrents("down") == []
    assert seen == ["ok", "down"]

"""Dead torrents nothing is watching are removed; everything else is left alone."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shelfmark.download import stale_torrents as mod
from shelfmark.download import torrent_ledger

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


@pytest.fixture(autouse=True)
def _ledger_file(tmp_path, monkeypatch):
    monkeypatch.setattr(torrent_ledger, "_path", lambda: tmp_path / "torrent_ledger.db")


def _find(torrents, *, ledger=None, running=(), hours=6.0):
    ledger = {"h1": "task-1"} if ledger is None else ledger
    return mod.find_stale(
        list(torrents), ledger=ledger, running_task_ids=set(running), idle_hours=hours, now=NOW
    )


def test_a_stalled_torrent_in_the_ledger_idle_for_hours_with_no_owner_is_stale():
    assert [t.name for _known, t in _find([_torrent()])] == ["Book"]


def test_a_torrent_not_in_the_ledger_is_never_stale_whatever_its_category():
    torrent = _torrent(h="theirs", category="books")

    assert _find([torrent]) == []


def test_a_torrent_is_matched_on_any_hash_the_client_exposes():
    hybrid = _torrent(h="trunc-v2", infohash_v1="h1")

    assert [known for known, _t in _find([hybrid])] == ["h1"]


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


def test_a_torrent_owned_by_a_running_task_is_left_alone():
    assert _find([_torrent()], running={"task-1"}) == []


def test_a_task_that_is_not_running_does_not_protect_its_torrent():
    assert _find([_torrent()], running={"task-2"})


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


def _client(*torrents, removed=True):
    client = MagicMock()
    client.list_torrents.return_value = list(torrents)
    client.remove.return_value = removed
    return client


def test_run_removes_a_ledger_torrent_idle_past_the_limit_with_its_data(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    torrent_ledger.record("h1", "task-1")
    client = _client(_torrent("Dead"))

    report = mod.run(client, running_task_ids=lambda: set(), now=NOW)

    assert report.removed == ["Dead"]
    client.remove.assert_called_once_with("h1", delete_files=True)
    assert torrent_ledger.entries() == {}  # the row goes with the torrent


def test_run_never_touches_a_torrent_in_the_same_category_that_is_not_in_the_ledger(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    torrent_ledger.record("h1", "task-1")
    client = _client(
        _torrent("Ours", h="h1"), _torrent("Somebody else's", h="other", category="books")
    )

    report = mod.run(client, running_task_ids=lambda: set(), now=NOW)

    assert report.removed == ["Ours"]
    assert [call.args[0] for call in client.remove.call_args_list] == ["h1"]


def test_run_skips_a_torrent_a_running_task_owns(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    torrent_ledger.record("h1", "task-1")
    client = _client(_torrent())

    report = mod.run(client, running_task_ids=lambda: {"task-1"}, now=NOW)

    assert report.removed == []
    client.remove.assert_not_called()
    assert torrent_ledger.entries() == {"h1": "task-1"}


def test_a_task_that_joined_the_torrent_owns_it_now(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    torrent_ledger.record("h1", "task-1")
    torrent_ledger.claim("h1", "task-2")
    client = _client(_torrent())

    assert mod.run(client, running_task_ids=lambda: {"task-2"}, now=NOW).removed == []
    assert mod.run(client, running_task_ids=lambda: {"task-1"}, now=NOW).removed == ["Book"]


def test_run_does_nothing_when_switched_off(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 0.0)
    torrent_ledger.record("h1", "task-1")
    client = _client(_torrent())

    report = mod.run(client, running_task_ids=lambda: set(), now=NOW)

    assert report.removed == []
    client.list_torrents.assert_not_called()


def test_a_failed_removal_is_not_reported_and_keeps_the_row(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    torrent_ledger.record("h1", "task-1")
    client = _client(_torrent(), removed=False)

    assert mod.run(client, running_task_ids=lambda: set(), now=NOW).removed == []
    assert torrent_ledger.entries() == {"h1": "task-1"}


def test_a_row_whose_torrent_is_gone_is_dropped_but_an_empty_listing_drops_nothing(monkeypatch):
    monkeypatch.setattr(mod, "stale_hours", lambda: 6.0)
    torrent_ledger.record("gone", "task-9")
    torrent_ledger.record("h1", "task-1")

    mod.run(_client(), running_task_ids=lambda: set(), now=NOW)  # qBittorrent failed to answer
    assert set(torrent_ledger.entries()) == {"gone", "h1"}

    mod.run(_client(_torrent(idle=1.0)), running_task_ids=lambda: set(), now=NOW)
    assert set(torrent_ledger.entries()) == {"h1"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("6", 6.0),
        (24, 24.0),
        (1, 3.0),
        (2.5, 3.0),
        (500, 72.0),
        (72, 72.0),
        (0, 0.0),
        (-3, 0.0),
        (None, 24.0),
        ("junk", 24.0),
    ],
)
def test_the_limit_is_held_to_3_through_72_hours_and_zero_means_off(monkeypatch, raw, expected):
    monkeypatch.setattr(mod.config, "get", lambda _k, _d=None: raw)

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

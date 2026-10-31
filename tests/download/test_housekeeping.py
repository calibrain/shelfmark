"""The housekeeping thread runs both sweeps, isolated from each other."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.download import housekeeping as mod


def _leftovers(**lists):
    base = {"adopted": [], "removed": [], "reattached": [], "cancelled": [], "kept": []}
    base.update(lists)
    return SimpleNamespace(**base)


@pytest.fixture
def sweeps():
    with (
        patch("shelfmark.download.stale_torrents.run_for_shelfmark") as torrents,
        patch("shelfmark.release_sources.slskd.leftovers.run_with_backend") as slskd,
    ):
        torrents.return_value = SimpleNamespace(removed=[])
        slskd.return_value = _leftovers()
        yield torrents, slskd


def test_run_once_runs_both_sweeps(sweeps):
    torrents, slskd = sweeps
    torrents.return_value = SimpleNamespace(removed=["Dead Book"])
    slskd.return_value = _leftovers(adopted=["slskd_ab12"], kept=["x"])
    user_db, history = MagicMock(), MagicMock()

    summary = mod.run_once(user_db, history)

    torrents.assert_called_once_with()
    slskd.assert_called_once_with(history_service=history, user_db=user_db)
    assert summary == {
        "torrents_removed": ["Dead Book"],
        "slskd": {"adopted": ["slskd_ab12"]},
    }


def test_nothing_done_reports_nothing(sweeps):
    assert mod.run_once(MagicMock(), MagicMock()) == {}


def test_torrent_sweep_failure_does_not_skip_slskd(sweeps):
    torrents, slskd = sweeps
    torrents.side_effect = RuntimeError("qbittorrent down")
    slskd.return_value = _leftovers(removed=["slskd_old"])

    summary = mod.run_once(MagicMock(), MagicMock())

    slskd.assert_called_once()
    assert summary == {"slskd": {"removed": ["slskd_old"]}, "errors": ["stale_torrents"]}


def test_slskd_failure_does_not_skip_torrents(sweeps):
    torrents, slskd = sweeps
    torrents.return_value = SimpleNamespace(removed=["Dead Book"])
    slskd.side_effect = OSError("disk")

    summary = mod.run_once(MagicMock(), MagicMock())

    assert summary == {"torrents_removed": ["Dead Book"], "errors": ["slskd_leftovers"]}


def test_slskd_sweep_skipped_without_context(sweeps):
    torrents, slskd = sweeps
    mod.run_once()
    torrents.assert_called_once()
    slskd.assert_not_called()


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setattr(mod, "_thread", None)
    monkeypatch.setattr(mod, "_loop", lambda: __import__("time").sleep(5))
    mod.start(MagicMock(), MagicMock())
    first = mod._thread
    assert first is not None
    assert first.name == "DownloadHousekeeping"
    assert first.daemon
    mod.start(MagicMock(), MagicMock())
    assert mod._thread is first


def test_loop_survives_a_failing_cycle(monkeypatch):
    calls = []

    def fake_run_once(*_args):
        calls.append(1)
        raise RuntimeError("boom")

    class Stop(BaseException):
        pass

    def fake_sleep(seconds):
        if seconds == mod.INTERVAL_SECONDS and len(calls) >= 2:
            raise Stop

    monkeypatch.setattr(mod, "run_once", fake_run_once)
    monkeypatch.setattr(mod.time, "sleep", fake_sleep)
    with pytest.raises(Stop):
        mod._loop()
    assert len(calls) == 2

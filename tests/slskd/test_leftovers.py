"""Finished and abandoned folders under Shelfmark's slskd downloads folder."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import shelfmark.release_sources.slskd.handler as handler_mod
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB
from shelfmark.release_sources.slskd import leftovers

NOW = datetime(2026, 10, 16, 18, 0, tzinfo=UTC)
FILES = [{"filename": "@@s\\Book\\a.mp3", "size": 5}, {"filename": "@@s\\Book\\b.mp3", "size": 7}]


@pytest.fixture
def env(tmp_path):
    db_path = str(tmp_path / "users.db")
    user_db = UserDB(db_path)
    user_db.initialize()
    base = tmp_path / "complete" / "shelfmark"
    base.mkdir(parents=True)
    return user_db, DownloadHistoryService(db_path), db_path, base


def _folder(base, task_id, *, sizes=(5, 7), age_days=0.0):
    folder = base / task_id.replace(":", "_")
    folder.mkdir()
    for n, size in enumerate(sizes):
        (folder / f"f{n}.mp3").write_bytes(b"x" * size)
    return folder


def _row(env, task_id, *, status, request_id=None, age_days=0.0, files=FILES):
    _user_db, history, db_path, _base = env
    history.record_download(
        task_id=task_id,
        user_id=None,
        username=None,
        request_id=request_id,
        source="slskd",
        source_display_name=None,
        title="Book",
        author=None,
        file_format=None,
        size=None,
        preview=None,
        content_type="audiobook",
        downloads=None,
        origin="requested" if request_id else "direct",
        retry_payload={"retry_source_context": {"username": "peer", "files": files}},
    )
    if status != "active":
        history.finalize_download(task_id=task_id, final_status=status, status_message="x")
    stamp = (NOW - timedelta(days=age_days)).isoformat(timespec="seconds")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE download_history SET queued_at = ?, terminal_at = ? WHERE task_id = ?",
        (stamp, stamp, task_id),
    )
    conn.commit()
    conn.close()


def _request(env, *, status="fulfilled", delivery="none"):
    user_db = env[0]
    user = user_db.create_user(username="reader", role="user") if not user_db.list_users() else None
    user_id = user["id"] if user else user_db.list_users()[0]["id"]
    row = user_db.create_request(
        user_id=user_id,
        content_type="audiobook",
        request_level="book",
        policy_mode="request_book",
        book_data={"title": "T", "author": "A", "provider": "hardcover", "provider_id": "1"},
    )
    return user_db.update_request(row["id"], status=status, delivery_state=delivery)


def _plan(env, folders, *, keep_completed=False, clean_days=7):
    user_db, history, _db, _base = env
    return leftovers.plan(
        folders,
        history_service=history,
        user_db=user_db,
        folder_complete=handler_mod.folder_complete,
        keep_completed=keep_completed,
        clean_after=timedelta(days=clean_days) if clean_days else None,
        now=NOW,
    )


def _names(paths):
    return sorted(p.name if hasattr(p, "name") else p[0].name for p in paths)


def test_a_whole_folder_whose_download_never_completed_is_finished(env):
    base = env[3]
    folder = _folder(base, "slskd:aa")
    _row(env, "slskd:aa", status="error")

    finish, remove, keep = _plan(env, [folder])

    assert _names(finish) == ["slskd_aa"]
    assert not remove
    assert not keep


def test_a_whole_folder_of_a_request_that_still_waits_for_it_is_finished(env):
    request = _request(env, delivery="cancelled")
    folder = _folder(env[3], "slskd:bb")
    _row(env, "slskd:bb", status="error", request_id=request["id"])

    finish, _remove, _keep = _plan(env, [folder])

    assert _names(finish) == ["slskd_bb"]


def test_a_whole_folder_of_a_request_that_already_has_its_book_is_not_imported_twice(env):
    request = _request(env, delivery="complete")
    folder = _folder(env[3], "slskd:cc")
    _row(env, "slskd:cc", status="error", request_id=request["id"], age_days=2)

    finish, remove, _keep = _plan(env, [folder])

    assert not finish
    assert _names(remove) == ["slskd_cc"]


def test_a_whole_folder_of_a_pending_request_is_kept(env):
    request = _request(env, status="pending")
    folder = _folder(env[3], "slskd:dd")
    _row(env, "slskd:dd", status="error", request_id=request["id"], age_days=30)

    finish, remove, keep = _plan(env, [folder])

    assert not finish
    assert not remove
    assert _names(keep) == ["slskd_dd"]


def test_a_partial_folder_goes_once_it_is_old_enough(env):
    old = _folder(env[3], "slskd:old", sizes=(5,))
    young = _folder(env[3], "slskd:new", sizes=(5,))
    _row(env, "slskd:old", status="error", age_days=10)
    _row(env, "slskd:new", status="error", age_days=3)

    _finish, remove, keep = _plan(env, [old, young])

    assert _names(remove) == ["slskd_old"]
    assert _names(keep) == ["slskd_new"]


def test_a_partial_folder_stays_when_cleanup_is_off(env):
    folder = _folder(env[3], "slskd:old", sizes=(5,))
    _row(env, "slskd:old", status="error", age_days=400)

    _finish, remove, keep = _plan(env, [folder], clean_days=0)

    assert not remove
    assert _names(keep) == ["slskd_old"]


def test_a_partial_folder_of_a_delivered_request_goes_after_a_day_not_a_week(env):
    request = _request(env, delivery="complete")
    folder = _folder(env[3], "slskd:ee", sizes=(5,))
    _row(env, "slskd:ee", status="error", request_id=request["id"], age_days=2)

    _finish, remove, _keep = _plan(env, [folder])

    assert _names(remove) == ["slskd_ee"]


def test_the_folder_of_a_running_download_is_never_touched(env):
    folder = _folder(env[3], "slskd:run", sizes=(5,))
    _row(env, "slskd:run", status="active", age_days=100)

    finish, remove, keep = _plan(env, [folder])

    assert not finish
    assert not remove
    assert _names(keep) == ["slskd_run"]


@pytest.mark.parametrize(("keep_completed", "removed"), [(False, True), (True, False)])
def test_an_imported_folder_is_removed_unless_finished_files_are_kept_shared(
    env, keep_completed, removed
):
    folder = _folder(env[3], "slskd:done")
    _row(env, "slskd:done", status="complete", age_days=3)

    _finish, remove, _keep = _plan(env, [folder], keep_completed=keep_completed)

    assert bool(remove) is removed


def test_a_folder_nothing_records_is_removed_only_when_old(env):
    folder = _folder(env[3], "slskd:mystery")

    _finish, remove, keep = _plan(env, [folder])

    assert _names(keep) == ["slskd_mystery"]  # its mtime is now
    assert not remove


def test_a_folder_that_is_not_named_after_a_task_is_left_alone(env):
    other = env[3] / "something-else"
    other.mkdir()

    finish, remove, keep = _plan(env, [other])

    assert not finish
    assert not remove
    assert _names(keep) == ["something-else"]


class _Client:
    pass


@pytest.fixture
def wired(env, monkeypatch):
    base = env[3]
    values = {"SLSKD_CLEAN_LEFTOVERS_DAYS": 7, "SLSKD_KEEP_COMPLETED": False}
    monkeypatch.setattr(leftovers.config, "get", lambda k, d=None: values.get(k, d))
    monkeypatch.setattr(handler_mod.config, "get", lambda k, d=None: values.get(k, d))
    monkeypatch.setattr(handler_mod, "build_client_from_config", lambda: _Client())
    monkeypatch.setattr(handler_mod, "resolve_local_download_root", lambda _c: (base.parent, None))
    return values


def _run(env, requeue):
    return leftovers.run(history_service=env[1], user_db=env[0], requeue=requeue, now=NOW)


def test_run_queues_a_whole_folder_and_removes_an_old_partial_one(env, wired):
    whole = _folder(env[3], "slskd:aa")
    partial = _folder(env[3], "slskd:bb", sizes=(5,))
    _row(env, "slskd:aa", status="error")
    _row(env, "slskd:bb", status="error", age_days=10)
    queued = []

    def requeue(payload, final_status):
        queued.append((payload, final_status))
        return True, None

    report = _run(env, requeue)

    assert report.adopted == ["slskd_aa"]
    assert report.removed == ["slskd_bb"]
    assert len(queued) == 1
    assert queued[0][1] == "error"
    assert whole.exists()  # left for the import to read, and removed after it
    assert not partial.exists()


def test_run_queues_at_most_a_few_folders_at_a_time(env, wired):
    for n in range(leftovers.MAX_ADOPTIONS_PER_RUN + 2):
        _folder(env[3], f"slskd:t{n}")
        _row(env, f"slskd:t{n}", status="error")

    report = _run(env, lambda payload, final_status: (True, None))

    assert len(report.adopted) == leftovers.MAX_ADOPTIONS_PER_RUN


def test_run_does_not_import_when_finishing_is_switched_off(env, wired):
    _folder(env[3], "slskd:aa")
    _row(env, "slskd:aa", status="error")
    wired["SLSKD_FINISH_LEFTOVERS"] = False

    report = _run(env, lambda payload, final_status: (_ for _ in ()).throw(AssertionError()))

    assert report.adopted == []


def test_run_keeps_a_folder_whose_import_could_not_be_queued(env, wired):
    folder = _folder(env[3], "slskd:aa")
    _row(env, "slskd:aa", status="error")

    report = _run(env, lambda payload, final_status: (False, "Download cannot be retried"))

    assert report.adopted == []
    assert folder.exists()


def test_run_does_nothing_without_a_downloads_folder(env, wired, monkeypatch):
    monkeypatch.setattr(handler_mod, "resolve_local_download_root", lambda _c: (None, "no path"))
    _folder(env[3], "slskd:aa")
    _row(env, "slskd:aa", status="error")

    report = _run(env, lambda payload, final_status: (True, None))

    assert (report.adopted, report.removed) == ([], [])


def test_the_task_id_comes_back_from_a_folder_name():
    assert leftovers._task_id_for_folder("slskd_ab12") == "slskd:ab12"
    assert leftovers._task_id_for_folder("slskd_") is None
    assert leftovers._task_id_for_folder("other") is None


def test_the_payload_files_are_read_whether_stored_as_text_or_dict():
    payload = {"retry_source_context": {"files": FILES}}

    assert leftovers._expected_files({"retry_payload": payload}) == FILES
    assert leftovers._expected_files({"retry_payload": json.dumps(payload)}) == FILES
    assert leftovers._expected_files({"retry_payload": "not json"}) == []


# --- transfers nothing is watching -------------------------------------------------------


def _transfer(name="@@s\\Book\\a.mp3", state="Queued, Remotely", peer="peer", tid="x1"):
    return {"username": peer, "filename": name, "state": state, "id": tid, "size": 5}


def _plan_transfers(env, transfers, *, live=None):
    live = live or {}
    return leftovers.plan_transfers(
        transfers,
        rows=env[1].list_recent(user_id=None, limit=1000),
        live_task_ids=set(live),
        live_request_ids={r for r in live.values() if r},
        user_db=env[0],
    )


def test_a_finished_transfer_is_only_a_record_and_is_left_alone(env):
    _row(env, "slskd:aa", status="error")

    reattach, cancel = _plan_transfers(env, [_transfer(state="Completed, Errored")])

    assert (reattach, cancel) == ([], [])


def test_a_transfer_a_running_task_owns_is_left_alone(env):
    _row(env, "slskd:aa", status="active")

    reattach, cancel = _plan_transfers(env, [_transfer()], live={"slskd:aa": None})

    assert (reattach, cancel) == ([], [])


def test_an_interrupted_manual_download_is_picked_up_again(env):
    _row(env, "slskd:aa", status="active")

    reattach, cancel = _plan_transfers(env, [_transfer()])

    assert [r["task_id"] for r in reattach] == ["slskd:aa"]
    assert cancel == []


def test_a_manual_download_that_already_failed_is_not_brought_back(env):
    _row(env, "slskd:aa", status="error")

    reattach, cancel = _plan_transfers(env, [_transfer()])

    assert reattach == []
    assert len(cancel) == 1


def test_a_transfer_of_a_request_still_waiting_for_it_is_picked_up_again(env):
    request = _request(env, delivery="queued")
    _row(env, "slskd:aa", status="error", request_id=request["id"])

    reattach, cancel = _plan_transfers(env, [_transfer()])

    assert [r["task_id"] for r in reattach] == ["slskd:aa"]
    assert cancel == []


def test_an_older_attempt_is_dropped_when_a_newer_one_is_running(env):
    request = _request(env, delivery="queued")
    _row(env, "slskd:old", status="error", request_id=request["id"])

    reattach, cancel = _plan_transfers(env, [_transfer()], live={"slskd:new": request["id"]})

    assert reattach == []
    assert len(cancel) == 1


def test_a_pending_request_may_still_choose_the_same_release_so_its_transfer_waits(env):
    request = _request(env, status="pending")
    _row(env, "slskd:aa", status="error", request_id=request["id"])

    assert _plan_transfers(env, [_transfer()]) == ([], [])


@pytest.mark.parametrize(
    ("status", "delivery"),
    [("fulfilled", "complete"), ("rejected", "none"), ("cancelled", "none")],
)
def test_a_transfer_of_a_request_that_is_done_or_dropped_is_cancelled(env, status, delivery):
    request = _request(env, status=status, delivery=delivery)
    _row(env, "slskd:aa", status="error", request_id=request["id"])

    reattach, cancel = _plan_transfers(env, [_transfer()])

    assert reattach == []
    assert len(cancel) == 1


def test_a_transfer_no_download_ever_asked_for_is_cancelled(env):
    reattach, cancel = _plan_transfers(env, [_transfer(name="@@s\\Other\\z.mp3")])

    assert reattach == []
    assert len(cancel) == 1


class _FakeSlskd:
    def __init__(self, transfers):
        self.transfers = transfers
        self.cancelled = []

    def list_all_downloads(self):
        return self.transfers

    def cancel_transfer(self, username, transfer_id, *, remove):
        self.cancelled.append((username, transfer_id, remove))
        return True


def test_run_cancels_an_unwatched_transfer_and_picks_up_a_waiting_one(env, wired, monkeypatch):
    monkeypatch.setattr(leftovers.time, "sleep", lambda _s: None)
    waiting = _request(env, delivery="queued")
    _row(env, "slskd:aa", status="error", request_id=waiting["id"])
    fake = _FakeSlskd(
        [
            _transfer(tid="keep"),
            _transfer(name="@@s\\Stranger\\q.mp3", tid="drop"),
        ]
    )
    monkeypatch.setattr(handler_mod, "build_client_from_config", lambda: fake)
    queued = []

    def requeue(payload, final_status):
        queued.append(final_status)
        return True, None

    report = leftovers.run(
        history_service=env[1],
        user_db=env[0],
        requeue=requeue,
        live_tasks=lambda: {},
        now=NOW,
    )

    assert report.reattached == ["slskd:aa"]
    assert queued == ["error"]
    assert [c[1] for c in fake.cancelled] == ["drop", "drop"]  # cancel, then the second pass
    assert all(c[2] is True for c in fake.cancelled)
    assert len(report.cancelled) == 1


def test_run_leaves_transfers_alone_when_it_is_not_given_the_running_tasks(env, wired, monkeypatch):
    fake = _FakeSlskd([_transfer(name="@@s\\Stranger\\q.mp3", tid="drop")])
    monkeypatch.setattr(handler_mod, "build_client_from_config", lambda: fake)

    leftovers.run(
        history_service=env[1],
        user_db=env[0],
        requeue=lambda *a, **k: (True, None),
        now=NOW,
    )

    assert fake.cancelled == []


def test_run_with_backend_reads_the_running_tasks_and_requeues_through_the_retry(
    env, wired, monkeypatch
):
    from shelfmark.download import orchestrator as backend

    request = _request(env, delivery="queued")
    _row(env, "slskd:aa", status="error", request_id=request["id"])
    fake = _FakeSlskd([_transfer(tid="t1")])
    monkeypatch.setattr(handler_mod, "build_client_from_config", lambda: fake)
    monkeypatch.setattr(
        backend,
        "queue_status",
        lambda: {"downloading": {"slskd:other": {"request_id": None}}, "error": {}},
    )
    retried = []

    def retry(payload, *, final_status, allow_request_linked):
        retried.append((final_status, allow_request_linked))
        return True, None

    monkeypatch.setattr(backend, "retry_persisted_download", retry)

    report = leftovers.run_with_backend(history_service=env[1], user_db=env[0], now=NOW)

    assert report.reattached == ["slskd:aa"]
    assert retried == [("error", True)]

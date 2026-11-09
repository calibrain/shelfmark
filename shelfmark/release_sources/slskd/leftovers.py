"""Folders slskd left under Shelfmark's downloads folder: finish the whole ones, drop the dead ones.

Each download gets a folder named after its task id. A restart can land between "the last file
arrived" and "the files were copied to the library", leaving a finished folder nothing will ever
import, and a failed or abandoned download leaves a partial one. Both sit in slskd's folder
(and are shared with other Soulseek users) until someone looks.

A whole folder whose download never completed is queued again through the ordinary retry; the
handler sees the files are already there and goes straight to the import. It is queued once:
if that import fails too, the folder stays, since the files may not be had again, until a
person retries the download or its request is done with (delivered another way, or dropped).
A partial folder, or the folder of a cancelled download, is removed once its download is old
enough that nothing is going to resume it. Running downloads keep their folders, and nothing
is removed while clean-up is off.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int
from shelfmark.download.fs import run_blocking_io

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

FINISH_SETTING = "SLSKD_FINISH_LEFTOVERS"
CLEAN_DAYS_SETTING = "SLSKD_CLEAN_LEFTOVERS_DAYS"

# Folders of a request that already has its book, or that was given up on, go after this long.
DELIVERED_GRACE = timedelta(days=1)
# At most this many downloads are queued per run by each loop (finished folders, transfers picked
# up again), counting only the ones that were queued, so a backlog does not flood the queue.
MAX_ADOPTIONS_PER_RUN = 3
# Left in a folder once it has been tried for import, queued or not. Hidden, so it never counts as
# a file of the download. A folder is not tried again every run: it waits for a person to retry
# the download, or for its request to be done with.
ADOPTED_MARKER = ".shelfmark-adopted"
_TASK_PREFIX = "slskd_"


@dataclass
class LeftoverReport:
    adopted: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    reattached: list[str] = field(default_factory=list)  # task ids picked up again
    cancelled: list[str] = field(default_factory=list)  # "<peer>: <file>" transfers dropped


def _task_id_for_folder(name: str) -> str | None:
    """The task id a folder is named after ('slskd_ab12' is 'slskd:ab12'), or None.

    One-to-one because the handler only gives a task its own folder when the name maps back
    (``handler.folder_maps_back``); any other folder here was not made for a task.
    """
    if not name.startswith(_TASK_PREFIX) or len(name) == len(_TASK_PREFIX):
        return None
    return "slskd:" + name[len(_TASK_PREFIX) :]


def _parse_stamp(value: object) -> datetime | None:
    """Parse a stored ISO timestamp, reading one without a zone as UTC.

    None when it is missing or unreadable.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _expected_files(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The files a Soulseek download asked for, from its retry payload.

    Empty when the payload is missing or malformed.
    """
    payload = row.get("retry_payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    context = payload.get("retry_source_context") if isinstance(payload, dict) else None
    files = context.get("files") if isinstance(context, dict) else None
    return [f for f in files if isinstance(f, dict)] if isinstance(files, list) else []


def _folder_age(folder: Path, row: dict[str, Any] | None, now: datetime) -> timedelta:
    """How old a download folder is.

    Counted from the download's end, else its start, else the folder's own mtime.
    """
    stamp = _parse_stamp((row or {}).get("terminal_at")) or _parse_stamp(
        (row or {}).get("queued_at")
    )
    if stamp is None:
        try:
            stamp = datetime.fromtimestamp(run_blocking_io(folder.stat).st_mtime, tz=UTC)
        except OSError:
            return timedelta(0)
    return now - stamp


def _request_state(user_db: UserDB, row: dict[str, Any]) -> tuple[bool, bool]:
    """(needs this download, already has its book or was given up on) for the row's request."""
    request_id = row.get("request_id")
    if not request_id:
        return True, False
    request = user_db.get_request(int(request_id))
    if not request:
        return False, True
    status = request.get("status")
    delivered = request.get("delivery_state") == "complete"
    still_waiting = status == "fulfilled" and not delivered
    return still_waiting, delivered or status in {"rejected", "cancelled"}


def plan(
    folders: list[Path],
    *,
    history_service: DownloadHistoryService,
    user_db: UserDB,
    folder_complete: Any,
    keep_completed: bool,
    clean_after: timedelta | None,
    now: datetime | None = None,
    live_request_ids: set[int] | None = None,
) -> tuple[list[tuple[Path, dict[str, Any]]], list[Path], list[Path]]:
    """Sort folders into (to finish, to remove, to keep). Reads only, changes nothing.

    What goes where, with ``clean_after`` the clean-up age (None while clean-up is off):

    - not named after a task, or a running download: kept.
    - imported: removed a day on, unless ``keep_completed``. Clean-up has no say here.
    - every file asked for is there and its request is still open (or it was a direct
      download): queued for import once, when the request waits for it; kept otherwise.
    - the request has its book from elsewhere, or was dropped: removed a day on.
    - cancelled, partial, or nothing records it: removed at ``clean_after``.

    ``live_request_ids`` are the requests a running task is downloading for now; a folder of an
    older attempt for one of them is kept, not imported next to the new attempt.
    """
    now = now or datetime.now(UTC)
    to_finish: list[tuple[Path, dict[str, Any]]] = []
    to_remove: list[Path] = []
    to_keep: list[Path] = []
    for folder in folders:
        task_id = _task_id_for_folder(folder.name)
        row = history_service.get_by_task_id(task_id) if task_id else None
        if task_id is None or (row is not None and row.get("task_id") != task_id):
            to_keep.append(folder)
            continue
        age = _folder_age(folder, row, now)
        status = str((row or {}).get("final_status") or "")

        if row is None:
            # Nothing records this download. Old enough, and it is dead weight.
            (to_remove if clean_after and age >= clean_after else to_keep).append(folder)
            continue
        if status == "active":
            to_keep.append(folder)  # a download is running or about to be reconciled
            continue
        if status == "complete":
            # Imported. The folder is only still here if cleanup failed, or it is kept shared.
            removable = not keep_completed and age >= DELIVERED_GRACE
            (to_remove if removable else to_keep).append(folder)
            continue

        if status == "cancelled":
            # Someone cancelled it: never imported automatically. The folder goes with clean-up.
            (to_remove if clean_after is not None and age >= clean_after else to_keep).append(
                folder
            )
            continue

        needs_it, done_with = _request_state(user_db, row)
        request_id = coerce_int(row.get("request_id"), 0)
        if needs_it and request_id and request_id in (live_request_ids or set()):
            to_keep.append(folder)  # a newer attempt for this request is running
            continue
        sizes = [int(f.get("size") or 0) for f in _expected_files(row)]
        whole = folder_complete(folder, sizes)
        if whole and not done_with:
            # Every file asked for is here, and someone may still want it: a download a person
            # started, or a request that is still open. Never removed, whatever its age: the
            # files may not be had again, and a later attempt at the same release (the task id
            # is the release) lands in this folder and imports it without downloading. Queued
            # for import once; after that it waits for a person to retry the download.
            if needs_it and not run_blocking_io((folder / ADOPTED_MARKER).exists):
                to_finish.append((folder, row))
            else:
                to_keep.append(folder)
        elif done_with and clean_after is not None and age >= DELIVERED_GRACE:
            # The book is in the library, or the request was dropped: no need to wait the full
            # clean-up period, but only while clean-up is on (0 keeps every folder).
            to_remove.append(folder)
        elif not whole and clean_after is not None and age >= clean_after:
            to_remove.append(folder)  # partial and old: nothing will resume it
        else:
            to_keep.append(folder)
    return to_finish, to_remove, to_keep


_LIVE_TASK_STATES = frozenset({"queued", "resolving", "locating", "downloading"})


def _is_unfinished(transfer: dict[str, Any]) -> bool:
    """slskd transfer states are 'Queued, Remotely', 'InProgress', 'Completed, Errored'..."""
    return not str(transfer.get("state") or "").lower().startswith("completed")


def _rows_by_file(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """The newest Soulseek history row that asked for each (peer, remote filename)."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:  # newest first
        if row.get("source") != "slskd":
            continue
        payload = row.get("retry_payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        context = payload.get("retry_source_context") if isinstance(payload, dict) else None
        if not isinstance(context, dict):
            continue
        peer = str(context.get("username") or "")
        for entry in context.get("files") or []:
            name = str(entry.get("filename") or "") if isinstance(entry, dict) else ""
            if peer and name:
                index.setdefault((peer, name), row)
    return index


def _queued_after_it_ended(transfer: dict[str, Any], row: dict[str, Any]) -> bool:
    """Whether slskd queued this transfer after the Shelfmark download it matches had ended.

    slskd will not queue a second transfer of a file from a peer while one is unfinished, and
    stamps each with ``requestedAt`` when it is queued. So a transfer asked for after the
    download ended is not one Shelfmark queued: another app or a person queued that file
    again. A running download has not ended, and a transfer with no readable stamp cannot be
    told apart, so both answer False.
    """
    if row.get("final_status") == "active":
        return False
    ended = _parse_stamp(row.get("terminal_at"))
    requested = _parse_stamp(transfer.get("requestedAt"))
    return ended is not None and requested is not None and requested > ended


def plan_transfers(
    transfers: list[dict[str, Any]],
    *,
    rows: list[dict[str, Any]],
    live_task_ids: set[str],
    live_request_ids: set[int],
    user_db: UserDB,
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Split the unfinished transfers nothing is watching into (reattach, cancel).

    ``reattach`` holds one (history row, its transfers) pair per download to pick up again, so
    a download that cannot be picked up can have its transfers dropped instead.

    Reads only. Only transfers a Shelfmark download asked for are considered, and only those
    slskd queued before that download ended; anything else on the slskd instance belongs to
    someone else and is left alone. A transfer is watched when a
    running task owns it. slskd keeps working on a
    transfer after the task that queued it is gone (a restart, a timeout), so these go on
    queueing at peers and writing into a folder nobody imports. One whose request still waits
    for that download is picked up again; the rest are dropped. A request that is pending is
    deciding what to fetch next, and may well choose this very release, so its transfers wait.
    """
    index = _rows_by_file(rows)
    reattach: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    cancel: list[dict[str, Any]] = []

    def pick_up(row: dict[str, Any], transfer: dict[str, Any]) -> None:
        """Note the transfer under its download, which is picked up once however many it has."""
        reattach.setdefault(str(row["task_id"]), (row, []))[1].append(transfer)

    for transfer in transfers:
        if not _is_unfinished(transfer):
            continue
        row = index.get((str(transfer.get("username") or ""), str(transfer.get("filename") or "")))
        if row is not None and row.get("task_id") in live_task_ids:
            continue  # a running task owns it
        if row is None:
            # Not one Shelfmark asked for: slskd may be shared with other apps or used by hand.
            continue
        if _queued_after_it_ended(transfer, row):
            continue  # the same file queued again, by another app or by hand
        if row.get("final_status") == "complete":
            # That download finished; an unfinished transfer of the same file is someone else's.
            continue
        if row.get("final_status") == "cancelled":
            cancel.append(transfer)  # someone cancelled this download; slskd should stop too
            continue
        request_id = row.get("request_id")
        if not request_id:
            if row.get("final_status") == "active":
                pick_up(row, transfer)  # interrupted, not abandoned
            else:
                cancel.append(transfer)
            continue
        request = user_db.get_request(int(request_id))
        if not request:
            cancel.append(transfer)
        elif request.get("status") == "pending":
            continue
        elif int(request_id) in live_request_ids:
            cancel.append(transfer)  # a newer attempt is running; this is an old one
        elif request.get("status") == "fulfilled" and request.get("delivery_state") != "complete":
            pick_up(row, transfer)
        else:
            cancel.append(transfer)
    return list(reattach.values()), cancel


def _subfolders(base: Path) -> list[Path]:
    """The folders directly inside base, sorted; run off the event loop by the caller."""
    return sorted(p for p in base.iterdir() if p.is_dir())


def _is_inside(folder: Path, root: Path) -> bool:
    """Whether folder is strictly inside root.

    Both are resolved first, so a link cannot lead the sweep elsewhere.
    """
    try:
        run_blocking_io(folder.resolve).relative_to(run_blocking_io(root.resolve))
    except OSError, ValueError:
        return False
    return run_blocking_io(folder.resolve) != run_blocking_io(root.resolve)


def run(
    *,
    history_service: DownloadHistoryService,
    user_db: UserDB,
    requeue: Any,
    live_tasks: Any = None,
    now: datetime | None = None,
) -> LeftoverReport:
    """Finish and clean up the folders under Shelfmark's slskd downloads folder.

    Does nothing while the Soulseek source is switched off, or without a client, isolated
    downloads or a readable downloads folder.
    """
    from shelfmark.release_sources.slskd import handler as slskd_handler

    report = LeftoverReport()
    if not config.get("SLSKD_ENABLED", False):
        # Soulseek is switched off: an import queued now would be refused and still mark the
        # folder as tried, so it would not be imported when Soulseek is switched back on.
        return report
    client = slskd_handler.build_client_from_config()
    if client is None or not slskd_handler.isolation_enabled():
        return report
    root, error = slskd_handler.resolve_local_download_root(client)
    if root is None:
        logger.debug("slskd leftovers: %s", error)
        return report
    prefix = slskd_handler.destination_prefix()
    base = root.joinpath(*prefix.split("/"))
    try:
        folders = run_blocking_io(_subfolders, base)
    except OSError:
        return report

    finish = slskd_handler.bool_setting(FINISH_SETTING, True)
    clean_days = _clean_days()
    clean_after = timedelta(days=clean_days) if clean_days > 0 else None
    keep_completed = slskd_handler.bool_setting("SLSKD_KEEP_COMPLETED", True)

    live_now = live_tasks() if live_tasks is not None else {}
    to_finish, to_remove, to_keep = plan(
        folders,
        history_service=history_service,
        user_db=user_db,
        folder_complete=slskd_handler.folder_complete,
        keep_completed=keep_completed,
        clean_after=clean_after,
        now=now,
        live_request_ids={coerce_int(r, 0) for r in live_now.values() if r},
    )
    report.kept = [f.name for f in to_keep]

    if finish:
        adopted_now = 0
        for folder, row in to_finish:
            if adopted_now >= MAX_ADOPTIONS_PER_RUN:
                report.kept.append(folder.name)  # the next run takes it
                continue
            ok, message = requeue(row.get("retry_payload"), final_status=row.get("final_status"))
            if ok:
                adopted_now += 1
                report.adopted.append(folder.name)
                logger.info("slskd leftovers: importing the finished folder %s", folder.name)
            else:
                logger.warning("slskd leftovers: could not import %s: %s", folder.name, message)
                report.kept.append(folder.name)
            # Tried once either way: a folder that cannot be queued is not retried every run.
            try:
                run_blocking_io((folder / ADOPTED_MARKER).touch)
            except OSError as exc:
                logger.warning("slskd leftovers: could not mark %s: %s", folder.name, exc)
    else:
        report.kept.extend(f.name for f, _ in to_finish)

    if finish and live_tasks is not None:
        _settle_transfers(
            client,
            history_service=history_service,
            user_db=user_db,
            requeue=requeue,
            live_tasks=live_tasks,
            report=report,
        )

    # A folder whose download this run picked up again, or that a task is using now, is in use.
    busy = set(report.reattached)
    if live_tasks is not None:
        busy |= set(live_tasks())
    for folder in to_remove:
        if not _is_inside(folder, base):
            continue
        if _task_id_for_folder(folder.name) in busy:
            report.kept.append(folder.name)
            continue
        try:
            run_blocking_io(shutil.rmtree, folder)
        except OSError as exc:
            logger.warning("slskd leftovers: could not remove %s: %s", folder, exc)
            report.kept.append(folder.name)
            continue
        report.removed.append(folder.name)
        logger.info("slskd leftovers: removed %s", folder.name)
    return report


def _settle_transfers(
    client: Any,
    *,
    history_service: DownloadHistoryService,
    user_db: UserDB,
    requeue: Any,
    live_tasks: Any,
    report: LeftoverReport,
) -> None:
    """Pick up or drop the unfinished slskd transfers no running task owns.

    As with folders, only downloads that were queued count towards the per-run cap; the rest
    wait for the next run, their transfers untouched. One that cannot be queued has its
    transfers dropped, since nothing is ever going to watch them.
    """
    try:
        transfers = client.list_all_downloads()
    except Exception as exc:  # noqa: BLE001 - slskd being down is not this sweep's problem
        logger.debug("slskd leftovers: could not list transfers: %s", exc)
        return
    live = live_tasks()  # {task id: request id or None} of the tasks running now
    reattach, cancel = plan_transfers(
        transfers,
        rows=history_service.list_recent(user_id=None, limit=1000),
        live_task_ids=set(live),
        live_request_ids={int(r) for r in live.values() if r},
        user_db=user_db,
    )
    picked_up = 0
    for row, owned in reattach:
        if picked_up >= MAX_ADOPTIONS_PER_RUN:
            break  # the next run takes the rest
        ok, message = requeue(row.get("retry_payload"), final_status=row.get("final_status"))
        if ok:
            picked_up += 1
            report.reattached.append(str(row["task_id"]))
            logger.info("slskd leftovers: picked %s up again", row["task_id"])
        else:
            logger.warning(
                "slskd leftovers: could not pick up %s, dropping its transfers: %s",
                row["task_id"],
                message,
            )
            cancel.extend(owned)
    _drop_transfers(client, cancel, report)


def _cancel_once(client: Any, transfer: dict[str, Any]) -> bool:
    """Ask slskd to cancel one transfer and remove its record. False when slskd refused."""
    try:
        client.cancel_transfer(str(transfer.get("username")), str(transfer.get("id")), remove=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("slskd leftovers: could not cancel %s: %s", _label(transfer), exc)
        return False
    return True


def _drop_transfers(client: Any, transfers: list[dict[str, Any]], report: LeftoverReport) -> None:
    """Cancel the transfers and remove their records, noting each in the report when both land.

    A live transfer needs a second pass once the cancel lands, so every transfer is cancelled
    first, then one wait covers them all before the second pass. One slskd refuses is left for
    the next run.
    """
    pending = [t for t in transfers if _cancel_once(client, t)]
    if pending:
        time.sleep(2.0)
    for transfer in pending:
        if _cancel_once(client, transfer):
            report.cancelled.append(_label(transfer))
            logger.info("slskd leftovers: dropped an unwatched transfer, %s", _label(transfer))


def _label(transfer: dict[str, Any]) -> str:
    """'<peer>: <file>', as the report and the log name a transfer."""
    return f"{transfer.get('username')}: {transfer.get('filename')}"


def _clean_days() -> int:
    return max(coerce_int(config.get(CLEAN_DAYS_SETTING, 0), 0), 0)


def run_with_backend(
    *, history_service: DownloadHistoryService, user_db: UserDB, now: datetime | None = None
) -> LeftoverReport:
    """``run`` wired to the live download queue: requeue through the retry, read what is running."""
    from shelfmark.download import orchestrator as backend

    def requeue(payload: object, final_status: object) -> tuple[bool, str | None]:
        return backend.retry_persisted_download(
            payload, final_status=final_status, allow_request_linked=True
        )

    def live_tasks() -> dict[str, Any]:
        """Task id to request id for every task the queue is still working on."""
        return {
            str(task_id): payload.get("request_id")
            for bucket, tasks in backend.queue_status().items()
            if bucket in _LIVE_TASK_STATES
            for task_id, payload in tasks.items()
            if isinstance(payload, dict)
        }

    return run(
        history_service=history_service,
        user_db=user_db,
        requeue=requeue,
        live_tasks=live_tasks,
        now=now,
    )

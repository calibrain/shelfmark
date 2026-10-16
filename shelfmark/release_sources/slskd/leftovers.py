"""Folders slskd left under Shelfmark's downloads folder: finish the whole ones, drop the dead ones.

Each download gets a folder named after its task id. A restart can land between "the last file
arrived" and "the files were copied to the library", leaving a finished folder nothing will ever
import, and a failed or abandoned download leaves a partial one. Both sit in slskd's folder
(and are shared with other Soulseek users) until someone looks.

A whole folder whose download never completed is queued again through the ordinary retry; the
handler sees the files are already there and goes straight to the import. A partial folder is
removed once its download is old enough that nothing is going to resume it.
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

if TYPE_CHECKING:
    from pathlib import Path

    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

FINISH_SETTING = "SLSKD_FINISH_LEFTOVERS"
CLEAN_DAYS_SETTING = "SLSKD_CLEAN_LEFTOVERS_DAYS"

# Folders of a request that already has its book, or that was given up on, go after this long.
DELIVERED_GRACE = timedelta(days=1)
# At most this many finished folders are queued per run, so a backlog does not flood the queue.
MAX_ADOPTIONS_PER_RUN = 3
_TASK_PREFIX = "slskd_"


@dataclass
class LeftoverReport:
    adopted: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    reattached: list[str] = field(default_factory=list)  # task ids picked up again
    cancelled: list[str] = field(default_factory=list)  # "<peer>: <file>" transfers dropped


def _task_id_for_folder(name: str) -> str | None:
    """The task id a folder is named after ('slskd_ab12' is 'slskd:ab12'), or None."""
    if not name.startswith(_TASK_PREFIX) or len(name) == len(_TASK_PREFIX):
        return None
    return "slskd:" + name[len(_TASK_PREFIX) :]


def _parse_stamp(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _expected_files(row: dict[str, Any]) -> list[dict[str, Any]]:
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
    stamp = _parse_stamp((row or {}).get("terminal_at")) or _parse_stamp(
        (row or {}).get("queued_at")
    )
    if stamp is None:
        try:
            stamp = datetime.fromtimestamp(folder.stat().st_mtime, tz=UTC)
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
) -> tuple[list[tuple[Path, dict[str, Any]]], list[Path], list[Path]]:
    """Sort folders into (to finish, to remove, to keep). Reads only, changes nothing."""
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

        needs_it, done_with = _request_state(user_db, row)
        sizes = [int(f.get("size") or 0) for f in _expected_files(row)]
        whole = folder_complete(folder, sizes)
        if whole and needs_it:
            to_finish.append((folder, row))
        elif done_with and age >= DELIVERED_GRACE:
            to_remove.append(folder)  # the book is in the library, or the request was dropped
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


def plan_transfers(
    transfers: list[dict[str, Any]],
    *,
    rows: list[dict[str, Any]],
    live_task_ids: set[str],
    live_request_ids: set[int],
    user_db: UserDB,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the unfinished transfers nothing is watching into (reattach, cancel).

    Reads only. A transfer is watched when a running task owns it. slskd keeps working on a
    transfer after the task that queued it is gone (a restart, a timeout), so these go on
    queueing at peers and writing into a folder nobody imports. One whose request still waits
    for that download is picked up again; the rest are dropped. A request that is pending is
    deciding what to fetch next, and may well choose this very release, so its transfers wait.
    """
    index = _rows_by_file(rows)
    reattach: dict[str, dict[str, Any]] = {}
    cancel: list[dict[str, Any]] = []
    for transfer in transfers:
        if not _is_unfinished(transfer):
            continue
        row = index.get((str(transfer.get("username") or ""), str(transfer.get("filename") or "")))
        if row is not None and row.get("task_id") in live_task_ids:
            continue  # a running task owns it
        if row is None:
            cancel.append(transfer)
            continue
        request_id = row.get("request_id")
        if not request_id:
            if row.get("final_status") == "active":
                reattach.setdefault(str(row["task_id"]), row)  # interrupted, not abandoned
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
            reattach.setdefault(str(row["task_id"]), row)
        else:
            cancel.append(transfer)
    return list(reattach.values()), cancel


def _is_inside(folder: Path, root: Path) -> bool:
    try:
        folder.resolve().relative_to(root.resolve())
    except OSError, ValueError:
        return False
    return folder.resolve() != root.resolve()


def run(
    *,
    history_service: DownloadHistoryService,
    user_db: UserDB,
    requeue: Any,
    live_tasks: Any = None,
    now: datetime | None = None,
) -> LeftoverReport:
    """Finish and clean up the folders under Shelfmark's slskd downloads folder."""
    from shelfmark.release_sources.slskd import handler as slskd_handler

    report = LeftoverReport()
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
        folders = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return report

    finish = slskd_handler.bool_setting(FINISH_SETTING, True)
    clean_days = _clean_days()
    clean_after = timedelta(days=clean_days) if clean_days > 0 else None
    keep_completed = slskd_handler.bool_setting("SLSKD_KEEP_COMPLETED", True)

    to_finish, to_remove, to_keep = plan(
        folders,
        history_service=history_service,
        user_db=user_db,
        folder_complete=slskd_handler.folder_complete,
        keep_completed=keep_completed,
        clean_after=clean_after,
        now=now,
    )
    report.kept = [f.name for f in to_keep]

    if finish:
        for folder, row in to_finish[:MAX_ADOPTIONS_PER_RUN]:
            ok, message = requeue(row.get("retry_payload"), final_status=row.get("final_status"))
            if ok:
                report.adopted.append(folder.name)
                logger.info("slskd leftovers: importing the finished folder %s", folder.name)
            else:
                logger.warning("slskd leftovers: could not import %s: %s", folder.name, message)
                report.kept.append(folder.name)
        report.kept.extend(f.name for f, _ in to_finish[MAX_ADOPTIONS_PER_RUN:])
    else:
        report.kept.extend(f.name for f, _ in to_finish)

    if live_tasks is not None:
        _settle_transfers(
            client,
            history_service=history_service,
            user_db=user_db,
            requeue=requeue,
            live_tasks=live_tasks,
            finish=finish,
            report=report,
        )

    for folder in to_remove:
        if not _is_inside(folder, base):
            continue
        try:
            shutil.rmtree(folder)
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
    finish: bool,
    report: LeftoverReport,
) -> None:
    """Pick up or drop the unfinished slskd transfers no running task owns."""
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
    if finish:
        for row in reattach[:MAX_ADOPTIONS_PER_RUN]:
            ok, message = requeue(row.get("retry_payload"), final_status=row.get("final_status"))
            if ok:
                report.reattached.append(str(row["task_id"]))
                logger.info("slskd leftovers: picked %s up again", row["task_id"])
            else:
                logger.warning("slskd leftovers: could not pick up %s: %s", row["task_id"], message)
    for transfer in cancel:
        label = f"{transfer.get('username')}: {transfer.get('filename')}"
        for attempt in range(2):  # a live transfer needs a second pass once the cancel lands
            try:
                client.cancel_transfer(
                    str(transfer.get("username")), str(transfer.get("id")), remove=True
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("slskd leftovers: could not cancel %s: %s", label, exc)
                break
            if attempt == 0:
                time.sleep(2.0)
        else:
            report.cancelled.append(label)
            logger.info("slskd leftovers: dropped an unwatched transfer, %s", label)


def _clean_days() -> int:
    try:
        return max(int(config.get(CLEAN_DAYS_SETTING, 0) or 0), 0)
    except TypeError, ValueError:
        return 0


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

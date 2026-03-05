"""Activity API routes (snapshot, dismiss, history)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, NamedTuple

from flask import Flask, jsonify, request, session

from shelfmark.core.download_history_service import ACTIVE_DOWNLOAD_STATUS, DownloadHistoryService, VALID_TERMINAL_STATUSES
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import ACTIVE_QUEUE_STATUSES, QueueStatus, TERMINAL_QUEUE_STATUSES
from shelfmark.core.request_validation import RequestStatus
from shelfmark.core.request_helpers import (
    emit_ws_event,
    extract_release_source_id,
    normalize_optional_text,
    normalize_positive_int,
    now_utc_iso,
    populate_request_usernames,
)
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _parse_timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _require_authenticated(resolve_auth_mode: Callable[[], str]):
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return None
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _resolve_db_user_id(
    require_in_auth_mode: bool = True,
    *,
    user_db: UserDB | None = None,
):
    raw_db_user_id = session.get("db_user_id")
    if raw_db_user_id is None:
        if not require_in_auth_mode:
            return None, None
        return None, (
            jsonify(
                {
                    "error": "User identity unavailable for activity workflow",
                    "code": "user_identity_unavailable",
                }
            ),
            403,
        )
    try:
        parsed_db_user_id = int(raw_db_user_id)
    except (TypeError, ValueError):
        if not require_in_auth_mode:
            return None, None
        return None, (
            jsonify(
                {
                    "error": "User identity unavailable for activity workflow",
                    "code": "user_identity_unavailable",
                }
            ),
            403,
        )

    if parsed_db_user_id < 1:
        if not require_in_auth_mode:
            return None, None
        return None, (
            jsonify(
                {
                    "error": "User identity unavailable for activity workflow",
                    "code": "user_identity_unavailable",
                }
            ),
            403,
        )

    if user_db is not None:
        try:
            db_user = user_db.get_user(user_id=parsed_db_user_id)
        except Exception as exc:
            logger.warning("Failed to validate activity db identity %s: %s", parsed_db_user_id, exc)
            db_user = None
        if db_user is None:
            if not require_in_auth_mode:
                return None, None
            return None, (
                jsonify(
                    {
                        "error": "User identity unavailable for activity workflow",
                        "code": "user_identity_unavailable",
                    }
                ),
                403,
            )

    return parsed_db_user_id, None


class _ActorContext(NamedTuple):
    db_user_id: int | None
    is_no_auth: bool
    is_admin: bool
    owner_scope: int | None


def _resolve_activity_actor(
    *,
    user_db: UserDB,
    resolve_auth_mode: Callable[[], str],
) -> tuple[_ActorContext | None, Any | None]:
    """Resolve acting user identity for activity mutations.

    Returns (actor, error_response). On success actor is non-None.
    """
    if resolve_auth_mode() == "none":
        return _ActorContext(db_user_id=None, is_no_auth=True, is_admin=True, owner_scope=None), None

    db_user_id, db_gate = _resolve_db_user_id(user_db=user_db)
    if db_user_id is None:
        return None, db_gate

    is_admin = bool(session.get("is_admin"))
    return _ActorContext(
        db_user_id=db_user_id,
        is_no_auth=False,
        is_admin=is_admin,
        owner_scope=None if is_admin else db_user_id,
    ), None


def _activity_ws_room(*, is_no_auth: bool, actor_db_user_id: int | None) -> str:
    """Resolve the WebSocket room for activity events."""
    if is_no_auth:
        return "admins"
    if actor_db_user_id is not None:
        return f"user_{actor_db_user_id}"
    return "admins"


def _check_item_ownership(actor: _ActorContext, row: dict[str, Any]) -> Any | None:
    """Return a 403 response if the actor doesn't own the item, else None."""
    if actor.is_admin:
        return None
    owner_user_id = normalize_positive_int(row.get("user_id"))
    if owner_user_id != actor.db_user_id:
        return jsonify({"error": "Forbidden"}), 403
    return None


def _list_visible_requests(user_db: UserDB, *, is_admin: bool, db_user_id: int | None) -> list[dict[str, Any]]:
    if is_admin:
        request_rows = user_db.list_requests()
        populate_request_usernames(request_rows, user_db)
        return request_rows

    if db_user_id is None:
        return []
    return user_db.list_requests(user_id=db_user_id)


def _parse_item_key(item_key: Any, prefix: str) -> str | None:
    """Extract the value after 'prefix:' from an item_key string."""
    if not isinstance(item_key, str) or not item_key.startswith(f"{prefix}:"):
        return None
    value = item_key.split(":", 1)[1].strip()
    return value or None


_ALL_BUCKET_KEYS = (*ACTIVE_QUEUE_STATUSES, *TERMINAL_QUEUE_STATUSES)


def _build_download_status_from_db(
    *,
    db_rows: list[dict[str, Any]],
    queue_status: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Build the download status dict from DB rows, overlaying live queue data.

    Active DB rows are matched against the queue for live progress.
    Terminal DB rows go directly into their final bucket.
    Stale active rows (no queue entry) are treated as interrupted errors.
    """
    status: dict[str, dict[str, Any]] = {key: {} for key in _ALL_BUCKET_KEYS}

    # Index queue items by task_id for fast lookup: task_id -> (bucket_key, payload)
    queue_index: dict[str, tuple[str, dict[str, Any]]] = {}
    for bucket_key in _ALL_BUCKET_KEYS:
        bucket = queue_status.get(bucket_key)
        if not isinstance(bucket, dict):
            continue
        for task_id, payload in bucket.items():
            queue_index[str(task_id)] = (bucket_key, payload)

    for row in db_rows:
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            continue

        final_status = row.get("final_status")

        if final_status == ACTIVE_DOWNLOAD_STATUS:
            queue_entry = queue_index.pop(task_id, None)
            if queue_entry is not None:
                bucket_key, queue_payload = queue_entry
                status[bucket_key][task_id] = queue_payload
            else:
                # Stale active row — no queue entry means it was interrupted
                download_payload = DownloadHistoryService.to_download_payload(row)
                download_payload["status_message"] = "Interrupted"
                status[QueueStatus.ERROR][task_id] = download_payload
        elif final_status in VALID_TERMINAL_STATUSES:
            download_payload = DownloadHistoryService.to_download_payload(row)
            status[final_status][task_id] = download_payload

    # Include any queue items that don't have a DB row yet (race condition safety)
    for task_id, (bucket_key, queue_payload) in queue_index.items():
        if task_id not in status.get(bucket_key, {}):
            status[bucket_key][task_id] = queue_payload

    return status


def _collect_active_download_task_ids(status: dict[str, dict[str, Any]]) -> set[str]:
    active_task_ids: set[str] = set()
    for bucket_key in ACTIVE_QUEUE_STATUSES:
        bucket = status.get(bucket_key)
        if not isinstance(bucket, dict):
            continue
        for task_id in bucket.keys():
            normalized_task_id = str(task_id).strip()
            if normalized_task_id:
                active_task_ids.add(normalized_task_id)
    return active_task_ids


def _request_terminal_status(row: dict[str, Any]) -> str | None:
    request_status = row.get("status")
    if request_status == RequestStatus.PENDING:
        return None
    if request_status == RequestStatus.REJECTED:
        return RequestStatus.REJECTED
    if request_status == RequestStatus.CANCELLED:
        return RequestStatus.CANCELLED
    if request_status != RequestStatus.FULFILLED:
        return None

    delivery_state = str(row.get("delivery_state") or "").strip().lower()
    if delivery_state in {QueueStatus.ERROR, QueueStatus.CANCELLED}:
        return delivery_state
    return QueueStatus.COMPLETE


def _minimal_request_snapshot(request_row: dict[str, Any], request_id: int) -> dict[str, Any]:
    book_data = request_row.get("book_data")
    release_data = request_row.get("release_data")
    if not isinstance(book_data, dict):
        book_data = {}
    if not isinstance(release_data, dict):
        release_data = {}

    minimal_request = {
        "id": request_id,
        "user_id": request_row.get("user_id"),
        "status": request_row.get("status"),
        "request_level": request_row.get("request_level"),
        "delivery_state": request_row.get("delivery_state"),
        "book_data": book_data,
        "release_data": release_data,
        "note": request_row.get("note"),
        "admin_note": request_row.get("admin_note"),
        "created_at": request_row.get("created_at"),
        "updated_at": request_row.get("reviewed_at") or request_row.get("created_at"),
    }
    username = request_row.get("username")
    if isinstance(username, str):
        minimal_request["username"] = username
    return {"kind": "request", "request": minimal_request}


def _request_history_entry(request_row: dict[str, Any]) -> dict[str, Any] | None:
    request_id = normalize_positive_int(request_row.get("id"))
    if request_id is None:
        return None
    final_status = _request_terminal_status(request_row)
    item_key = f"request:{request_id}"
    return {
        "id": item_key,
        "user_id": request_row.get("user_id"),
        "item_type": "request",
        "item_key": item_key,
        "dismissed_at": request_row.get("dismissed_at"),
        "snapshot": _minimal_request_snapshot(request_row, request_id),
        "origin": "request",
        "final_status": final_status,
        "terminal_at": request_row.get("reviewed_at") or request_row.get("created_at"),
        "request_id": request_id,
        "source_id": extract_release_source_id(request_row.get("release_data")),
    }


def _dedupe_dismissed_entries(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, str]] = []
    for entry in entries:
        item_type = str(entry.get("item_type") or "").strip().lower()
        item_key = str(entry.get("item_key") or "").strip()
        if item_type not in {"download", "request"} or not item_key:
            continue
        marker = (item_type, item_key)
        if marker in seen:
            continue
        seen.add(marker)
        result.append({"item_type": item_type, "item_key": item_key})
    return result


def register_activity_routes(
    app: Flask,
    user_db: UserDB,
    *,
    download_history_service: DownloadHistoryService,
    resolve_auth_mode: Callable[[], str],
    resolve_status_scope: Callable[[], tuple[bool, int | None, bool]],
    queue_status: Callable[..., dict[str, dict[str, Any]]],
    sync_request_delivery_states: Callable[..., list[dict[str, Any]]],
    emit_request_updates: Callable[[list[dict[str, Any]]], None],
    ws_manager: Any | None = None,
) -> None:
    """Register activity routes."""

    @app.route("/api/activity/snapshot", methods=["GET"])
    def api_activity_snapshot():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        is_admin, db_user_id, can_access_status = resolve_status_scope()
        if not can_access_status:
            return (
                jsonify(
                    {
                        "error": "User identity unavailable for activity workflow",
                        "code": "user_identity_unavailable",
                    }
                ),
                403,
            )

        owner_user_scope = None if is_admin else db_user_id

        live_queue = queue_status(user_id=owner_user_scope)

        try:
            db_rows = download_history_service.get_undismissed(
                user_id=owner_user_scope,
                limit=200,
            )
        except Exception as exc:
            logger.warning("Failed to load undismissed download rows: %s", exc)
            db_rows = []

        status = _build_download_status_from_db(
            db_rows=db_rows,
            queue_status=live_queue,
        )

        updated_requests = sync_request_delivery_states(
            user_db,
            queue_status=status,
            user_id=owner_user_scope,
        )
        emit_request_updates(updated_requests)
        request_rows = _list_visible_requests(user_db, is_admin=is_admin, db_user_id=db_user_id)

        dismissed: list[dict[str, str]] = []
        dismissed_task_ids: list[str] = []
        try:
            dismissed_task_ids = download_history_service.get_dismissed_keys(
                user_id=owner_user_scope,
            )
        except Exception as exc:
            logger.warning("Failed to load dismissed download keys: %s", exc)

        # Only clear stale dismissals when active downloads overlap dismissed keys.
        active_task_ids = _collect_active_download_task_ids(status)
        stale_dismissed = active_task_ids & set(dismissed_task_ids) if active_task_ids else set()
        if stale_dismissed:
            try:
                download_history_service.clear_dismissals_for_active(
                    task_ids=stale_dismissed,
                    user_id=owner_user_scope,
                )
                dismissed_task_ids = [tid for tid in dismissed_task_ids if tid not in stale_dismissed]
            except Exception as exc:
                logger.warning("Failed to clear stale download dismissals for active tasks: %s", exc)

        dismissed.extend(
            {"item_type": "download", "item_key": f"download:{task_id}"}
            for task_id in dismissed_task_ids
        )

        # Keep request dismissal state on the request rows directly.
        try:
            dismissed_request_rows = user_db.list_dismissed_requests(user_id=owner_user_scope)
            for request_row in dismissed_request_rows:
                request_id = normalize_positive_int(request_row.get("id"))
                if request_id is None:
                    continue
                dismissed.append({"item_type": "request", "item_key": f"request:{request_id}"})
        except Exception as exc:
            logger.warning("Failed to load dismissed request keys: %s", exc)

        if not is_admin and db_user_id is None:
            # In auth mode, if we can't identify a non-admin viewer, don't show dismissals.
            dismissed = []
        else:
            dismissed = _dedupe_dismissed_entries(dismissed)

        return jsonify(
            {
                "status": status,
                "requests": request_rows,
                "dismissed": dismissed,
            }
        )

    @app.route("/api/activity/dismiss", methods=["POST"])
    def api_activity_dismiss():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if actor_error is not None:
            return actor_error

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        item_type = str(data.get("item_type") or "").strip().lower()
        item_key = data.get("item_key")

        dismissal_item: dict[str, str] | None = None

        if item_type == "download":
            task_id = _parse_item_key(item_key, "download")
            if task_id is None:
                return jsonify({"error": "item_key must be in the format download:<task_id>"}), 400

            existing = download_history_service.get_by_task_id(task_id)
            if existing is None:
                return jsonify({"error": "Activity item not found"}), 404

            ownership_gate = _check_item_ownership(actor, existing)
            if ownership_gate is not None:
                return ownership_gate

            dismissed_count = download_history_service.dismiss(
                task_id=task_id,
                user_id=actor.owner_scope,
            )
            if dismissed_count < 1:
                return jsonify({"error": "Activity item not found"}), 404

            dismissal_item = {"item_type": "download", "item_key": f"download:{task_id}"}

        elif item_type == "request":
            request_id = normalize_positive_int(_parse_item_key(item_key, "request"))
            if request_id is None:
                return jsonify({"error": "item_key must be in the format request:<id>"}), 400

            request_row = user_db.get_request(request_id)
            if request_row is None:
                return jsonify({"error": "Request not found"}), 404

            ownership_gate = _check_item_ownership(actor, request_row)
            if ownership_gate is not None:
                return ownership_gate

            user_db.update_request(request_id, dismissed_at=now_utc_iso())
            dismissal_item = {"item_type": "request", "item_key": f"request:{request_id}"}
        else:
            return jsonify({"error": "item_type must be one of: download, request"}), 400

        room = _activity_ws_room(is_no_auth=actor.is_no_auth, actor_db_user_id=actor.db_user_id)
        emit_ws_event(
            ws_manager,
            event_name="activity_update",
            room=room,
            payload={
                "kind": "dismiss",
                "item_type": dismissal_item["item_type"],
                "item_key": dismissal_item["item_key"],
            },
        )

        return jsonify({"status": "dismissed", "item": dismissal_item})

    @app.route("/api/activity/dismiss-many", methods=["POST"])
    def api_activity_dismiss_many():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if actor_error is not None:
            return actor_error

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400
        items = data.get("items")
        if not isinstance(items, list):
            return jsonify({"error": "items must be an array"}), 400

        download_task_ids: list[str] = []
        request_ids: list[int] = []

        for item in items:
            if not isinstance(item, dict):
                return jsonify({"error": "items must contain objects"}), 400

            item_type = str(item.get("item_type") or "").strip().lower()
            item_key = item.get("item_key")

            if item_type == "download":
                task_id = _parse_item_key(item_key, "download")
                if task_id is None:
                    return jsonify({"error": "download item_key must be in the format download:<task_id>"}), 400
                existing = download_history_service.get_by_task_id(task_id)
                if existing is None:
                    continue
                ownership_gate = _check_item_ownership(actor, existing)
                if ownership_gate is not None:
                    return ownership_gate
                download_task_ids.append(task_id)
                continue

            if item_type == "request":
                request_id = normalize_positive_int(_parse_item_key(item_key, "request"))
                if request_id is None:
                    return jsonify({"error": "request item_key must be in the format request:<id>"}), 400
                request_row = user_db.get_request(request_id)
                if request_row is None:
                    continue
                ownership_gate = _check_item_ownership(actor, request_row)
                if ownership_gate is not None:
                    return ownership_gate
                request_ids.append(request_id)
                continue

            return jsonify({"error": "item_type must be one of: download, request"}), 400

        dismissed_download_count = download_history_service.dismiss_many(
            task_ids=download_task_ids,
            user_id=actor.owner_scope,
        )

        dismissed_request_count = user_db.dismiss_requests_batch(
            request_ids=request_ids,
            dismissed_at=now_utc_iso(),
        )

        dismissed_count = dismissed_download_count + dismissed_request_count

        room = _activity_ws_room(is_no_auth=actor.is_no_auth, actor_db_user_id=actor.db_user_id)
        emit_ws_event(
            ws_manager,
            event_name="activity_update",
            room=room,
            payload={
                "kind": "dismiss_many",
                "count": dismissed_count,
            },
        )

        return jsonify({"status": "dismissed", "count": dismissed_count})

    @app.route("/api/activity/history", methods=["GET"])
    def api_activity_history():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if actor_error is not None:
            return actor_error

        limit = request.args.get("limit", type=int, default=50)
        offset = request.args.get("offset", type=int, default=0)
        if limit is None:
            limit = 50
        if offset is None:
            offset = 0
        if limit < 1:
            return jsonify({"error": "limit must be a positive integer"}), 400
        if offset < 0:
            return jsonify({"error": "offset must be a non-negative integer"}), 400

        # Fetch enough from each source to fill the requested page after merging.
        merge_limit = offset + limit
        download_history_rows = download_history_service.get_history(
            user_id=actor.owner_scope,
            limit=merge_limit,
            offset=0,
        )
        dismissed_request_rows = user_db.list_dismissed_requests(user_id=actor.owner_scope, limit=merge_limit)
        request_history_rows = [
            entry
            for entry in (_request_history_entry(row) for row in dismissed_request_rows)
            if entry is not None
        ]

        combined = [*download_history_rows, *request_history_rows]
        combined.sort(
            key=lambda row: (
                _parse_timestamp(row.get("dismissed_at")),
                str(row.get("id") or ""),
            ),
            reverse=True,
        )
        paged = combined[offset:offset + limit]
        return jsonify(paged)

    @app.route("/api/activity/history", methods=["DELETE"])
    def api_activity_history_clear():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if actor_error is not None:
            return actor_error

        deleted_downloads = download_history_service.clear_dismissed(user_id=actor.owner_scope)
        deleted_requests = user_db.delete_dismissed_requests(user_id=actor.owner_scope)
        deleted_count = deleted_downloads + deleted_requests

        room = _activity_ws_room(is_no_auth=actor.is_no_auth, actor_db_user_id=actor.db_user_id)
        emit_ws_event(
            ws_manager,
            event_name="activity_update",
            room=room,
            payload={
                "kind": "history_cleared",
                "count": deleted_count,
            },
        )
        return jsonify({"status": "cleared", "deleted_count": deleted_count})

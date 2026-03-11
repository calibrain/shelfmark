"""Activity API routes (snapshot, dismiss, history)."""

from __future__ import annotations

from typing import Any, Callable, NamedTuple

from flask import Flask, jsonify, request, session

from shelfmark.core.activity_view_state_service import (
    ADMIN_VIEWER_SCOPE,
    NOAUTH_VIEWER_SCOPE,
    ActivityViewStateService,
    user_viewer_scope,
)
from shelfmark.core.download_history_service import ACTIVE_DOWNLOAD_STATUS, DownloadHistoryService, VALID_TERMINAL_STATUSES
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import ACTIVE_QUEUE_STATUSES, QueueStatus, TERMINAL_QUEUE_STATUSES
from shelfmark.core.request_validation import RequestStatus
from shelfmark.core.request_helpers import (
    emit_ws_event,
    extract_release_source_id,
    normalize_positive_int,
    populate_request_usernames,
)
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _normalize_log_field(value: Any) -> str:
    if value is None:
        return "-"
    text = str(value).strip()
    return text or "-"


def _log_activity_rejection(
    action: str,
    *,
    status_code: int,
    reason: str,
    auth_mode: Any = None,
    viewer_scope: Any = None,
    item_type: Any = None,
    item_key: Any = None,
    item_count: int | None = None,
    missing_item_keys: list[str] | None = None,
    owner_user_id: Any = None,
    final_status: Any = None,
    request_id: Any = None,
) -> None:
    parts = [
        f"Activity {action} rejected",
        f"status={status_code}",
        f"reason={_normalize_log_field(reason)}",
        f"method={request.method}",
        f"path={request.path}",
        f"user={_normalize_log_field(session.get('user_id'))}",
        f"db_user_id={_normalize_log_field(session.get('db_user_id'))}",
        f"is_admin={bool(session.get('is_admin', False))}",
    ]
    if auth_mode is not None:
        parts.append(f"auth_mode={_normalize_log_field(auth_mode)}")
    if viewer_scope is not None:
        parts.append(f"viewer_scope={_normalize_log_field(viewer_scope)}")
    if item_type is not None:
        parts.append(f"item_type={_normalize_log_field(item_type)}")
    if item_key is not None:
        parts.append(f"item_key={_normalize_log_field(item_key)}")
    if item_count is not None:
        parts.append(f"item_count={item_count}")
    if missing_item_keys:
        parts.append(f"missing_item_keys={','.join(missing_item_keys)}")
    if owner_user_id is not None:
        parts.append(f"owner_user_id={_normalize_log_field(owner_user_id)}")
    if final_status is not None:
        parts.append(f"final_status={_normalize_log_field(final_status)}")
    if request_id is not None:
        parts.append(f"request_id={_normalize_log_field(request_id)}")
    logger.warning(" ".join(parts))


def _activity_error_response(
    action: str,
    *,
    status_code: int,
    error: str,
    code: str | None = None,
    auth_mode: Any = None,
    viewer_scope: Any = None,
    item_type: Any = None,
    item_key: Any = None,
    item_count: int | None = None,
    missing_item_keys: list[str] | None = None,
    owner_user_id: Any = None,
    final_status: Any = None,
    request_id: Any = None,
):
    _log_activity_rejection(
        action,
        status_code=status_code,
        reason=error,
        auth_mode=auth_mode,
        viewer_scope=viewer_scope,
        item_type=item_type,
        item_key=item_key,
        item_count=item_count,
        missing_item_keys=missing_item_keys,
        owner_user_id=owner_user_id,
        final_status=final_status,
        request_id=request_id,
    )

    payload: dict[str, Any] = {"error": error}
    if code:
        payload["code"] = code
    if missing_item_keys:
        payload["missing_item_keys"] = missing_item_keys
    return jsonify(payload), status_code


def _require_authenticated(resolve_auth_mode: Callable[[], str], *, action: str):
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return None
    if "user_id" not in session:
        return _activity_error_response(
            action,
            status_code=401,
            error="Unauthorized",
            auth_mode=auth_mode,
        )
    return None


def _resolve_db_user_id(
    require_in_auth_mode: bool = True,
    *,
    user_db: UserDB | None = None,
    action: str | None = None,
    auth_mode: str | None = None,
):
    raw_db_user_id = session.get("db_user_id")
    if raw_db_user_id is None:
        if not require_in_auth_mode:
            return None, None
        return None, _activity_error_response(
            action or "request",
            status_code=403,
            error="User identity unavailable for activity workflow",
            code="user_identity_unavailable",
            auth_mode=auth_mode,
        )
    try:
        parsed_db_user_id = int(raw_db_user_id)
    except (TypeError, ValueError):
        if not require_in_auth_mode:
            return None, None
        return None, _activity_error_response(
            action or "request",
            status_code=403,
            error="User identity unavailable for activity workflow",
            code="user_identity_unavailable",
            auth_mode=auth_mode,
        )

    if parsed_db_user_id < 1:
        if not require_in_auth_mode:
            return None, None
        return None, _activity_error_response(
            action or "request",
            status_code=403,
            error="User identity unavailable for activity workflow",
            code="user_identity_unavailable",
            auth_mode=auth_mode,
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
            return None, _activity_error_response(
                action or "request",
                status_code=403,
                error="User identity unavailable for activity workflow",
                code="user_identity_unavailable",
                auth_mode=auth_mode,
            )

    return parsed_db_user_id, None


class _ActorContext(NamedTuple):
    db_user_id: int | None
    is_no_auth: bool
    is_admin: bool
    owner_scope: int | None
    viewer_scope: str


def _resolve_activity_actor(
    *,
    user_db: UserDB,
    resolve_auth_mode: Callable[[], str],
    action: str,
) -> tuple[_ActorContext | None, Any | None]:
    """Resolve acting user identity for activity mutations.

    Returns (actor, error_response). On success actor is non-None.
    """
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return _ActorContext(
            db_user_id=None,
            is_no_auth=True,
            is_admin=True,
            owner_scope=None,
            viewer_scope=NOAUTH_VIEWER_SCOPE,
        ), None

    db_user_id, db_gate = _resolve_db_user_id(
        user_db=user_db,
        action=action,
        auth_mode=auth_mode,
    )
    if db_user_id is None:
        return None, db_gate

    is_admin = bool(session.get("is_admin"))
    viewer_scope = ADMIN_VIEWER_SCOPE if is_admin else user_viewer_scope(db_user_id)
    return _ActorContext(
        db_user_id=db_user_id,
        is_no_auth=False,
        is_admin=is_admin,
        owner_scope=None if is_admin else db_user_id,
        viewer_scope=viewer_scope,
    ), None


def _activity_ws_room(actor: _ActorContext) -> str:
    """Resolve the WebSocket room for activity events."""
    if actor.is_no_auth or actor.is_admin:
        return "admins"
    if actor.db_user_id is not None:
        return f"user_{actor.db_user_id}"
    return "admins"


def _check_item_ownership(actor: _ActorContext, row: dict[str, Any]) -> Any | None:
    """Return an error string if the actor doesn't own the item, else None."""
    if actor.is_admin:
        return None
    owner_user_id = normalize_positive_int(row.get("user_id"))
    if owner_user_id != actor.db_user_id:
        return "Forbidden"
    return None


def _check_terminal_download(row: dict[str, Any]) -> Any | None:
    final_status = str(row.get("final_status") or "").strip().lower()
    if final_status not in VALID_TERMINAL_STATUSES:
        return "Only terminal downloads can be dismissed"
    return None


def _check_terminal_request(row: dict[str, Any]) -> Any | None:
    if _request_terminal_status(row) is None:
        return "Only terminal requests can be dismissed"
    return None


def _download_row_log_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "owner_user_id": normalize_positive_int(row.get("user_id")),
        "final_status": row.get("final_status"),
        "request_id": normalize_positive_int(row.get("request_id")),
    }


def _request_row_log_context(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "owner_user_id": normalize_positive_int(row.get("user_id")),
        "request_id": normalize_positive_int(row.get("id")),
    }


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
            # For complete/cancelled the saved status_message is a stale
            # progress string (e.g. "Fetching download sources") — clear it
            # so the frontend only shows its own status label.  Error rows
            # keep theirs since the message describes the failure.
            if final_status in ("complete", "cancelled"):
                download_payload["status_message"] = None
            status[final_status][task_id] = download_payload

    return status


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


def _request_history_entry(
    request_row: dict[str, Any],
    *,
    dismissed_at: str | None,
) -> dict[str, Any] | None:
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
        "dismissed_at": dismissed_at,
        "snapshot": _minimal_request_snapshot(request_row, request_id),
        "origin": "request",
        "final_status": final_status,
        "terminal_at": request_row.get("reviewed_at") or request_row.get("created_at"),
        "request_id": request_id,
        "source_id": extract_release_source_id(request_row.get("release_data")),
    }


def register_activity_routes(
    app: Flask,
    user_db: UserDB,
    *,
    activity_view_state_service: ActivityViewStateService,
    download_history_service: DownloadHistoryService,
    resolve_auth_mode: Callable[[], str],
    queue_status: Callable[..., dict[str, dict[str, Any]]],
    sync_request_delivery_states: Callable[..., list[dict[str, Any]]],
    emit_request_updates: Callable[[list[dict[str, Any]]], None],
    ws_manager: Any | None = None,
) -> None:
    """Register activity routes."""

    @app.route("/api/activity/snapshot", methods=["GET"])
    def api_activity_snapshot():
        auth_gate = _require_authenticated(resolve_auth_mode, action="snapshot")
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
            action="snapshot",
        )
        if actor_error is not None:
            return actor_error

        hidden_rows = activity_view_state_service.list_hidden(viewer_scope=actor.viewer_scope)
        hidden_item_keys = {str(row.get("item_key") or "").strip() for row in hidden_rows}
        dismissed_entries = [
            {
                "item_type": str(row.get("item_type") or "").strip().lower(),
                "item_key": str(row.get("item_key") or "").strip(),
            }
            for row in hidden_rows
            if str(row.get("item_type") or "").strip().lower() in {"download", "request"}
            and str(row.get("item_key") or "").strip()
        ]
        live_queue = queue_status(user_id=actor.owner_scope)
        db_rows = download_history_service.list_recent(
            user_id=actor.owner_scope,
            limit=200,
        )
        visible_db_rows = [
            row
            for row in db_rows
            if f"download:{str(row.get('task_id') or '').strip()}" not in hidden_item_keys
        ]

        status = _build_download_status_from_db(
            db_rows=visible_db_rows,
            queue_status=live_queue,
        )

        updated_requests = sync_request_delivery_states(
            user_db,
            queue_status=status,
            user_id=actor.owner_scope,
        )
        emit_request_updates(updated_requests)
        request_rows = _list_visible_requests(
            user_db,
            is_admin=actor.is_admin,
            db_user_id=actor.db_user_id,
        )
        visible_request_rows: list[dict[str, Any]] = []
        for row in request_rows:
            request_id = normalize_positive_int(row.get("id"))
            if request_id is None:
                continue
            if f"request:{request_id}" in hidden_item_keys:
                continue
            visible_request_rows.append(row)

        return jsonify(
            {
                "status": status,
                "requests": visible_request_rows,
                "dismissed": dismissed_entries,
            }
        )

    @app.route("/api/activity/dismiss", methods=["POST"])
    def api_activity_dismiss():
        auth_gate = _require_authenticated(resolve_auth_mode, action="dismiss")
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
            action="dismiss",
        )
        if actor_error is not None:
            return actor_error

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _activity_error_response("dismiss", status_code=400, error="Invalid payload")

        item_type = str(data.get("item_type") or "").strip().lower()
        item_key = data.get("item_key")

        dismissal_item: dict[str, str] | None = None

        if item_type == "download":
            task_id = _parse_item_key(item_key, "download")
            if task_id is None:
                return _activity_error_response(
                    "dismiss",
                    status_code=400,
                    error="item_key must be in the format download:<task_id>",
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="download",
                    item_key=item_key,
                )

            existing = download_history_service.get_by_task_id(task_id)
            if existing is None:
                return _activity_error_response(
                    "dismiss",
                    status_code=404,
                    error="Download not found",
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="download",
                    item_key=f"download:{task_id}",
                )

            ownership_error = _check_item_ownership(actor, existing)
            if ownership_error is not None:
                return _activity_error_response(
                    "dismiss",
                    status_code=403,
                    error=ownership_error,
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="download",
                    item_key=f"download:{task_id}",
                    **_download_row_log_context(existing),
                )
            terminal_error = _check_terminal_download(existing)
            if terminal_error is not None:
                return _activity_error_response(
                    "dismiss",
                    status_code=409,
                    error=terminal_error,
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="download",
                    item_key=f"download:{task_id}",
                    **_download_row_log_context(existing),
                )

            activity_view_state_service.dismiss(
                viewer_scope=actor.viewer_scope,
                item_type="download",
                item_key=f"download:{task_id}",
            )
            dismissal_item = {"item_type": "download", "item_key": f"download:{task_id}"}

        elif item_type == "request":
            request_id = normalize_positive_int(_parse_item_key(item_key, "request"))
            if request_id is None:
                return _activity_error_response(
                    "dismiss",
                    status_code=400,
                    error="item_key must be in the format request:<id>",
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="request",
                    item_key=item_key,
                )

            request_row = user_db.get_request(request_id)
            if request_row is None:
                return _activity_error_response(
                    "dismiss",
                    status_code=404,
                    error="Request not found",
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="request",
                    item_key=f"request:{request_id}",
                    request_id=request_id,
                )

            ownership_error = _check_item_ownership(actor, request_row)
            if ownership_error is not None:
                return _activity_error_response(
                    "dismiss",
                    status_code=403,
                    error=ownership_error,
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="request",
                    item_key=f"request:{request_id}",
                    **_request_row_log_context(request_row),
                )
            terminal_error = _check_terminal_request(request_row)
            if terminal_error is not None:
                return _activity_error_response(
                    "dismiss",
                    status_code=409,
                    error=terminal_error,
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_type="request",
                    item_key=f"request:{request_id}",
                    **_request_row_log_context(request_row),
                )

            activity_view_state_service.dismiss(
                viewer_scope=actor.viewer_scope,
                item_type="request",
                item_key=f"request:{request_id}",
            )
            dismissal_item = {"item_type": "request", "item_key": f"request:{request_id}"}
        else:
            return _activity_error_response(
                "dismiss",
                status_code=400,
                error="item_type must be one of: download, request",
                auth_mode=resolve_auth_mode(),
                viewer_scope=actor.viewer_scope,
                item_type=item_type,
                item_key=item_key,
            )

        room = _activity_ws_room(actor)
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
        auth_gate = _require_authenticated(resolve_auth_mode, action="dismiss_many")
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
            action="dismiss_many",
        )
        if actor_error is not None:
            return actor_error

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _activity_error_response(
                "dismiss_many",
                status_code=400,
                error="Invalid payload",
                auth_mode=resolve_auth_mode(),
                viewer_scope=actor.viewer_scope,
            )
        items = data.get("items")
        if not isinstance(items, list):
            return _activity_error_response(
                "dismiss_many",
                status_code=400,
                error="items must be an array",
                auth_mode=resolve_auth_mode(),
                viewer_scope=actor.viewer_scope,
            )

        dismissal_items: list[dict[str, str]] = []
        missing_item_keys: list[str] = []

        for item in items:
            if not isinstance(item, dict):
                return _activity_error_response(
                    "dismiss_many",
                    status_code=400,
                    error="items must contain objects",
                    auth_mode=resolve_auth_mode(),
                    viewer_scope=actor.viewer_scope,
                    item_count=len(items),
                )

            item_type = str(item.get("item_type") or "").strip().lower()
            item_key = item.get("item_key")

            if item_type == "download":
                task_id = _parse_item_key(item_key, "download")
                if task_id is None:
                    return _activity_error_response(
                        "dismiss_many",
                        status_code=400,
                        error="download item_key must be in the format download:<task_id>",
                        auth_mode=resolve_auth_mode(),
                        viewer_scope=actor.viewer_scope,
                        item_type="download",
                        item_key=item_key,
                        item_count=len(items),
                    )
                existing = download_history_service.get_by_task_id(task_id)
                if existing is None:
                    missing_item_keys.append(f"download:{task_id}")
                    continue
                ownership_error = _check_item_ownership(actor, existing)
                if ownership_error is not None:
                    return _activity_error_response(
                        "dismiss_many",
                        status_code=403,
                        error=ownership_error,
                        auth_mode=resolve_auth_mode(),
                        viewer_scope=actor.viewer_scope,
                        item_type="download",
                        item_key=f"download:{task_id}",
                        item_count=len(items),
                        **_download_row_log_context(existing),
                    )
                terminal_error = _check_terminal_download(existing)
                if terminal_error is not None:
                    return _activity_error_response(
                        "dismiss_many",
                        status_code=409,
                        error=terminal_error,
                        auth_mode=resolve_auth_mode(),
                        viewer_scope=actor.viewer_scope,
                        item_type="download",
                        item_key=f"download:{task_id}",
                        item_count=len(items),
                        **_download_row_log_context(existing),
                    )
                dismissal_items.append({"item_type": "download", "item_key": f"download:{task_id}"})
                continue

            if item_type == "request":
                request_id = normalize_positive_int(_parse_item_key(item_key, "request"))
                if request_id is None:
                    return _activity_error_response(
                        "dismiss_many",
                        status_code=400,
                        error="request item_key must be in the format request:<id>",
                        auth_mode=resolve_auth_mode(),
                        viewer_scope=actor.viewer_scope,
                        item_type="request",
                        item_key=item_key,
                        item_count=len(items),
                    )
                request_row = user_db.get_request(request_id)
                if request_row is None:
                    missing_item_keys.append(f"request:{request_id}")
                    continue
                ownership_error = _check_item_ownership(actor, request_row)
                if ownership_error is not None:
                    return _activity_error_response(
                        "dismiss_many",
                        status_code=403,
                        error=ownership_error,
                        auth_mode=resolve_auth_mode(),
                        viewer_scope=actor.viewer_scope,
                        item_type="request",
                        item_key=f"request:{request_id}",
                        item_count=len(items),
                        **_request_row_log_context(request_row),
                    )
                terminal_error = _check_terminal_request(request_row)
                if terminal_error is not None:
                    return _activity_error_response(
                        "dismiss_many",
                        status_code=409,
                        error=terminal_error,
                        auth_mode=resolve_auth_mode(),
                        viewer_scope=actor.viewer_scope,
                        item_type="request",
                        item_key=f"request:{request_id}",
                        item_count=len(items),
                        **_request_row_log_context(request_row),
                    )
                dismissal_items.append({"item_type": "request", "item_key": f"request:{request_id}"})
                continue

            return _activity_error_response(
                "dismiss_many",
                status_code=400,
                error="item_type must be one of: download, request",
                auth_mode=resolve_auth_mode(),
                viewer_scope=actor.viewer_scope,
                item_type=item_type,
                item_key=item_key,
                item_count=len(items),
            )

        if missing_item_keys:
            return _activity_error_response(
                "dismiss_many",
                status_code=404,
                error="One or more activity items were not found",
                auth_mode=resolve_auth_mode(),
                viewer_scope=actor.viewer_scope,
                item_count=len(items),
                missing_item_keys=missing_item_keys,
            )

        dismissed_count = activity_view_state_service.dismiss_many(
            viewer_scope=actor.viewer_scope,
            items=dismissal_items,
        )

        room = _activity_ws_room(actor)
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
        auth_gate = _require_authenticated(resolve_auth_mode, action="history")
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
            action="history",
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
            return _activity_error_response("history", status_code=400, error="limit must be a positive integer")
        if offset < 0:
            return _activity_error_response("history", status_code=400, error="offset must be a non-negative integer")

        history_rows = activity_view_state_service.list_history(
            viewer_scope=actor.viewer_scope,
            limit=limit,
            offset=offset,
        )
        payload: list[dict[str, Any]] = []

        for history_row in history_rows:
            item_type = str(history_row.get("item_type") or "").strip().lower()
            item_key = str(history_row.get("item_key") or "").strip()
            dismissed_at = history_row.get("dismissed_at")

            if not isinstance(dismissed_at, str) or not dismissed_at.strip():
                raise RuntimeError(f"Activity history state missing dismissed_at for {item_key}")

            if item_type == "download":
                task_id = _parse_item_key(item_key, "download")
                if task_id is None:
                    raise RuntimeError(f"Invalid activity history item_key: {item_key}")

                download_row = download_history_service.get_by_task_id(task_id)
                if download_row is None:
                    raise RuntimeError(f"Download history row not found for {item_key}")

                if not actor.is_admin:
                    owner_user_id = normalize_positive_int(download_row.get("user_id"))
                    if owner_user_id != actor.db_user_id:
                        raise RuntimeError(f"Viewer state out of scope for {item_key}")

                payload.append(
                    DownloadHistoryService.to_history_row(
                        download_row,
                        dismissed_at=dismissed_at,
                    )
                )
                continue

            if item_type == "request":
                request_id = normalize_positive_int(_parse_item_key(item_key, "request"))
                if request_id is None:
                    raise RuntimeError(f"Invalid activity history item_key: {item_key}")

                request_row = user_db.get_request(request_id)
                if request_row is None:
                    raise RuntimeError(f"Request row not found for {item_key}")

                if not actor.is_admin:
                    owner_user_id = normalize_positive_int(request_row.get("user_id"))
                    if owner_user_id != actor.db_user_id:
                        raise RuntimeError(f"Viewer state out of scope for {item_key}")

                populate_request_usernames([request_row], user_db)
                entry = _request_history_entry(
                    request_row,
                    dismissed_at=dismissed_at,
                )
                if entry is None:
                    raise RuntimeError(f"Failed to build request history entry for {item_key}")
                payload.append(entry)
                continue

            raise RuntimeError(f"Unknown activity history item_type: {item_type}")

        return jsonify(payload)

    @app.route("/api/activity/history", methods=["DELETE"])
    def api_activity_history_clear():
        auth_gate = _require_authenticated(resolve_auth_mode, action="history_clear")
        if auth_gate is not None:
            return auth_gate

        actor, actor_error = _resolve_activity_actor(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
            action="history_clear",
        )
        if actor_error is not None:
            return actor_error

        cleared_count = activity_view_state_service.clear_history(
            viewer_scope=actor.viewer_scope,
        )

        room = _activity_ws_room(actor)
        emit_ws_event(
            ws_manager,
            event_name="activity_update",
            room=room,
            payload={
                "kind": "history_cleared",
                "count": cleared_count,
            },
        )
        return jsonify({"status": "cleared", "cleared_count": cleared_count})

"""Activity API routes (snapshot, dismiss, history)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from flask import Flask, jsonify, request, session

from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import extract_release_source_id
from shelfmark.core.user_db import NO_AUTH_ACTIVITY_USERNAME, UserDB

logger = setup_logger(__name__)


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: Any) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _normalize_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


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


def _ensure_no_auth_activity_user_id(user_db: UserDB) -> int | None:
    """Resolve a stable users.db identity for no-auth activity state."""
    try:
        user = user_db.get_user(username=NO_AUTH_ACTIVITY_USERNAME)
        if user is None:
            try:
                user_db.create_user(
                    username=NO_AUTH_ACTIVITY_USERNAME,
                    display_name="No-auth Activity",
                    role="admin",
                )
            except ValueError:
                # Another request may have created it between lookup and insert.
                pass
            user = user_db.get_user(username=NO_AUTH_ACTIVITY_USERNAME)

        if user is None:
            return None
        user_id = int(user.get("id"))
        return user_id if user_id > 0 else None
    except Exception as exc:
        logger.warning("Failed to resolve no-auth activity identity: %s", exc)
        return None


def _resolve_activity_actor_user_id(
    *,
    user_db: UserDB,
    resolve_auth_mode: Callable[[], str],
) -> tuple[int | None, Any | None]:
    """Resolve acting user identity for activity mutations."""
    if resolve_auth_mode() == "none":
        no_auth_user_id = _ensure_no_auth_activity_user_id(user_db)
        if no_auth_user_id is not None:
            return no_auth_user_id, None
        return None, (
            jsonify(
                {
                    "error": "User identity unavailable for activity workflow",
                    "code": "user_identity_unavailable",
                }
            ),
            403,
        )

    db_user_id, db_gate = _resolve_db_user_id(user_db=user_db)
    if db_user_id is not None:
        return db_user_id, None

    return None, db_gate


def _emit_activity_event(ws_manager: Any | None, *, room: str, payload: dict[str, Any]) -> None:
    if ws_manager is None:
        return
    try:
        socketio = getattr(ws_manager, "socketio", None)
        is_enabled = getattr(ws_manager, "is_enabled", None)
        if socketio is None or not callable(is_enabled) or not is_enabled():
            return
        socketio.emit("activity_update", payload, to=room)
    except Exception as exc:
        logger.warning("Failed to emit activity_update event: %s", exc)


def _list_visible_requests(user_db: UserDB, *, is_admin: bool, db_user_id: int | None) -> list[dict[str, Any]]:
    if is_admin:
        request_rows = user_db.list_requests()
        user_cache: dict[int, str] = {}
        for row in request_rows:
            requester_id = row["user_id"]
            if requester_id not in user_cache:
                requester = user_db.get_user(user_id=requester_id)
                user_cache[requester_id] = requester.get("username", "") if requester else ""
            row["username"] = user_cache[requester_id]
        return request_rows

    if db_user_id is None:
        return []
    return user_db.list_requests(user_id=db_user_id)


def _parse_download_item_key(item_key: Any) -> str | None:
    if not isinstance(item_key, str) or not item_key.startswith("download:"):
        return None
    task_id = item_key.split(":", 1)[1].strip()
    return task_id or None


def _parse_request_item_key(item_key: Any) -> int | None:
    if not isinstance(item_key, str) or not item_key.startswith("request:"):
        return None
    raw_id = item_key.split(":", 1)[1].strip()
    return _normalize_positive_int(raw_id)


def _download_history_row_to_download_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("task_id"),
        "title": row.get("title"),
        "author": row.get("author"),
        "format": row.get("format"),
        "size": row.get("size"),
        "preview": row.get("preview"),
        "content_type": row.get("content_type"),
        "source": row.get("source"),
        "source_display_name": row.get("source_display_name"),
        "status_message": row.get("status_message"),
        "download_path": row.get("download_path"),
        "user_id": row.get("user_id"),
        "username": row.get("username"),
        "request_id": row.get("request_id"),
    }


def _merge_terminal_snapshot_backfill(
    *,
    status: dict[str, dict[str, Any]],
    terminal_rows: list[dict[str, Any]],
) -> None:
    existing_task_ids: set[str] = set()
    for bucket_key in ("queued", "resolving", "locating", "downloading", "complete", "error", "cancelled"):
        bucket = status.get(bucket_key)
        if not isinstance(bucket, dict):
            continue
        existing_task_ids.update(str(task_id) for task_id in bucket.keys())

    for row in terminal_rows:
        task_id = str(row.get("task_id") or "").strip()
        if not task_id or task_id in existing_task_ids:
            continue

        final_status = row.get("final_status")
        if final_status not in {"complete", "error", "cancelled"}:
            continue

        download_payload = _download_history_row_to_download_payload(row)
        if final_status not in status or not isinstance(status.get(final_status), dict):
            status[final_status] = {}
        status[final_status][task_id] = download_payload
        existing_task_ids.add(task_id)


def _collect_active_download_task_ids(status: dict[str, dict[str, Any]]) -> set[str]:
    active_task_ids: set[str] = set()
    for bucket_key in ("queued", "resolving", "locating", "downloading"):
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
    if request_status == "pending":
        return None
    if request_status == "rejected":
        return "rejected"
    if request_status == "cancelled":
        return "cancelled"
    if request_status != "fulfilled":
        return None

    delivery_state = str(row.get("delivery_state") or "").strip().lower()
    if delivery_state in {"error", "cancelled"}:
        return delivery_state
    return "complete"


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
    request_id = _normalize_positive_int(request_row.get("id"))
    if request_id is None:
        return None
    final_status = _request_terminal_status(request_row)
    return {
        "id": 1000000000 + request_id,
        "user_id": request_row.get("user_id"),
        "item_type": "request",
        "item_key": f"request:{request_id}",
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

        if resolve_auth_mode() == "none":
            viewer_db_user_id = _ensure_no_auth_activity_user_id(user_db)
        else:
            viewer_db_user_id, _ = _resolve_db_user_id(
                require_in_auth_mode=False,
                user_db=user_db,
            )

        owner_user_scope = None if is_admin else db_user_id
        if resolve_auth_mode() == "none":
            owner_user_scope = None

        status = queue_status(user_id=owner_user_scope)
        updated_requests = sync_request_delivery_states(
            user_db,
            queue_status=status,
            user_id=owner_user_scope,
        )
        emit_request_updates(updated_requests)
        request_rows = _list_visible_requests(user_db, is_admin=is_admin, db_user_id=db_user_id)

        try:
            terminal_rows = download_history_service.get_undismissed_terminal(
                user_id=owner_user_scope,
                limit=200,
            )
            _merge_terminal_snapshot_backfill(status=status, terminal_rows=terminal_rows)
        except Exception as exc:
            logger.warning("Failed to merge terminal download history rows: %s", exc)

        active_task_ids = _collect_active_download_task_ids(status)
        if active_task_ids:
            try:
                download_history_service.clear_dismissals_for_active(
                    task_ids=active_task_ids,
                    user_id=owner_user_scope,
                )
            except Exception as exc:
                logger.warning("Failed to clear stale download dismissals for active tasks: %s", exc)

        dismissed: list[dict[str, str]] = []
        try:
            dismissed_task_ids = download_history_service.get_dismissed_keys(
                user_id=owner_user_scope,
            )
            dismissed.extend(
                {"item_type": "download", "item_key": f"download:{task_id}"}
                for task_id in dismissed_task_ids
            )
        except Exception as exc:
            logger.warning("Failed to load dismissed download keys: %s", exc)

        # Keep request dismissal state on the request rows directly.
        try:
            dismissed_request_rows = user_db.list_dismissed_requests(user_id=owner_user_scope)
            for request_row in dismissed_request_rows:
                request_id = _normalize_positive_int(request_row.get("id"))
                if request_id is None:
                    continue
                dismissed.append({"item_type": "request", "item_key": f"request:{request_id}"})
        except Exception as exc:
            logger.warning("Failed to load dismissed request keys: %s", exc)

        if viewer_db_user_id is None and resolve_auth_mode() != "none":
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

        actor_db_user_id, db_gate = _resolve_activity_actor_user_id(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if db_gate is not None or actor_db_user_id is None:
            return db_gate

        actor_is_admin = bool(session.get("is_admin")) or resolve_auth_mode() == "none"
        owner_scope_user_id = None if actor_is_admin else actor_db_user_id

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        item_type = str(data.get("item_type") or "").strip().lower()
        item_key = data.get("item_key")

        target_user_ids: set[int] = {actor_db_user_id}
        dismissal_item: dict[str, str] | None = None

        if item_type == "download":
            task_id = _parse_download_item_key(item_key)
            if task_id is None:
                return jsonify({"error": "item_key must be in the format download:<task_id>"}), 400

            existing = download_history_service.get_by_task_id(task_id)
            if existing is None:
                return jsonify({"error": "Activity item not found"}), 404

            owner_user_id = _normalize_positive_int(existing.get("user_id"))
            if not actor_is_admin and owner_user_id != actor_db_user_id:
                return jsonify({"error": "Forbidden"}), 403

            dismissed_count = download_history_service.dismiss(
                task_id=task_id,
                user_id=owner_scope_user_id,
            )
            if dismissed_count < 1:
                return jsonify({"error": "Activity item not found"}), 404

            dismissal_item = {"item_type": "download", "item_key": f"download:{task_id}"}

        elif item_type == "request":
            request_id = _parse_request_item_key(item_key)
            if request_id is None:
                return jsonify({"error": "item_key must be in the format request:<id>"}), 400

            request_row = user_db.get_request(request_id)
            if request_row is None:
                return jsonify({"error": "Request not found"}), 404

            owner_user_id = _normalize_positive_int(request_row.get("user_id"))
            if not actor_is_admin and owner_user_id != actor_db_user_id:
                return jsonify({"error": "Forbidden"}), 403

            user_db.update_request(request_id, dismissed_at=_now_timestamp())
            dismissal_item = {"item_type": "request", "item_key": f"request:{request_id}"}
        else:
            return jsonify({"error": "item_type must be one of: download, request"}), 400

        for target_user_id in sorted(target_user_ids):
            _emit_activity_event(
                ws_manager,
                room=f"user_{target_user_id}",
                payload={
                    "kind": "dismiss",
                    "user_id": target_user_id,
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

        actor_db_user_id, db_gate = _resolve_activity_actor_user_id(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if db_gate is not None or actor_db_user_id is None:
            return db_gate

        actor_is_admin = bool(session.get("is_admin")) or resolve_auth_mode() == "none"
        owner_scope_user_id = None if actor_is_admin else actor_db_user_id

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400
        items = data.get("items")
        if not isinstance(items, list):
            return jsonify({"error": "items must be an array"}), 400

        download_task_ids: list[str] = []
        request_ids: list[int] = []
        target_user_ids: set[int] = {actor_db_user_id}

        for item in items:
            if not isinstance(item, dict):
                return jsonify({"error": "items must contain objects"}), 400

            item_type = str(item.get("item_type") or "").strip().lower()
            item_key = item.get("item_key")

            if item_type == "download":
                task_id = _parse_download_item_key(item_key)
                if task_id is None:
                    return jsonify({"error": "download item_key must be in the format download:<task_id>"}), 400
                existing = download_history_service.get_by_task_id(task_id)
                if existing is None:
                    continue
                owner_user_id = _normalize_positive_int(existing.get("user_id"))
                if not actor_is_admin and owner_user_id != actor_db_user_id:
                    return jsonify({"error": "Forbidden"}), 403
                download_task_ids.append(task_id)
                continue

            if item_type == "request":
                request_id = _parse_request_item_key(item_key)
                if request_id is None:
                    return jsonify({"error": "request item_key must be in the format request:<id>"}), 400
                request_row = user_db.get_request(request_id)
                if request_row is None:
                    continue
                owner_user_id = _normalize_positive_int(request_row.get("user_id"))
                if not actor_is_admin and owner_user_id != actor_db_user_id:
                    return jsonify({"error": "Forbidden"}), 403
                request_ids.append(request_id)
                continue

            return jsonify({"error": "item_type must be one of: download, request"}), 400

        dismissed_download_count = download_history_service.dismiss_many(
            task_ids=download_task_ids,
            user_id=owner_scope_user_id,
        )

        dismissed_request_count = 0
        dismissed_at = _now_timestamp()
        for request_id in request_ids:
            try:
                user_db.update_request(request_id, dismissed_at=dismissed_at)
                dismissed_request_count += 1
            except ValueError:
                continue

        dismissed_count = dismissed_download_count + dismissed_request_count

        for target_user_id in sorted(target_user_ids):
            _emit_activity_event(
                ws_manager,
                room=f"user_{target_user_id}",
                payload={
                    "kind": "dismiss_many",
                    "user_id": target_user_id,
                    "count": dismissed_count,
                },
            )

        return jsonify({"status": "dismissed", "count": dismissed_count})

    @app.route("/api/activity/history", methods=["GET"])
    def api_activity_history():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        actor_db_user_id, db_gate = _resolve_activity_actor_user_id(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if db_gate is not None or actor_db_user_id is None:
            return db_gate

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

        actor_is_admin = bool(session.get("is_admin")) or resolve_auth_mode() == "none"
        owner_scope_user_id = None if actor_is_admin else actor_db_user_id

        # We combine download + request history and apply pagination over the merged list.
        max_rows = min(limit + offset + 500, 5000)
        download_history_rows = download_history_service.get_history(
            user_id=owner_scope_user_id,
            limit=max_rows,
            offset=0,
        )
        dismissed_request_rows = user_db.list_dismissed_requests(user_id=owner_scope_user_id, limit=max_rows)
        request_history_rows = [
            entry
            for entry in (_request_history_entry(row) for row in dismissed_request_rows)
            if entry is not None
        ]

        combined = [*download_history_rows, *request_history_rows]
        combined.sort(
            key=lambda row: (
                _parse_timestamp(row.get("dismissed_at")),
                int(row.get("id") or 0),
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

        actor_db_user_id, db_gate = _resolve_activity_actor_user_id(
            user_db=user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if db_gate is not None or actor_db_user_id is None:
            return db_gate

        actor_is_admin = bool(session.get("is_admin")) or resolve_auth_mode() == "none"
        owner_scope_user_id = None if actor_is_admin else actor_db_user_id

        deleted_downloads = download_history_service.clear_dismissed(user_id=owner_scope_user_id)
        cleared_requests = user_db.clear_request_dismissals(user_id=owner_scope_user_id)
        deleted_count = deleted_downloads + cleared_requests

        _emit_activity_event(
            ws_manager,
            room=f"user_{actor_db_user_id}",
            payload={
                "kind": "history_cleared",
                "user_id": actor_db_user_id,
                "count": deleted_count,
            },
        )
        return jsonify({"status": "cleared", "deleted_count": deleted_count})

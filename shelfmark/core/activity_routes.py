"""Activity API routes (snapshot, dismiss, history)."""

from __future__ import annotations

from typing import Any, Callable

from flask import Flask, jsonify, request, session

from shelfmark.core.activity_service import ActivityService
from shelfmark.core.logger import setup_logger
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _require_authenticated(resolve_auth_mode: Callable[[], str]):
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return None
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _resolve_db_user_id(require_in_auth_mode: bool = True):
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
        return int(raw_db_user_id), None
    except (TypeError, ValueError):
        return None, (
            jsonify(
                {
                    "error": "User identity unavailable for activity workflow",
                    "code": "user_identity_unavailable",
                }
            ),
            403,
        )


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


def _parse_download_item_key(item_key: str) -> str | None:
    if not isinstance(item_key, str) or not item_key.startswith("download:"):
        return None
    task_id = item_key.split(":", 1)[1].strip()
    return task_id or None


def _task_id_from_download_item_key(item_key: str) -> str | None:
    task_id = _parse_download_item_key(item_key)
    if task_id is None:
        return None
    return task_id


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
        item_key = row.get("item_key")
        if not isinstance(item_key, str):
            continue
        task_id = _task_id_from_download_item_key(item_key)
        if not task_id or task_id in existing_task_ids:
            continue

        final_status = row.get("final_status")
        if final_status not in {"complete", "error", "cancelled"}:
            continue

        snapshot = row.get("snapshot")
        if not isinstance(snapshot, dict):
            continue
        raw_download = snapshot.get("download")
        if not isinstance(raw_download, dict):
            continue

        download_payload = dict(raw_download)
        if not isinstance(download_payload.get("id"), str):
            download_payload["id"] = task_id

        if final_status not in status or not isinstance(status.get(final_status), dict):
            status[final_status] = {}
        status[final_status][task_id] = download_payload
        existing_task_ids.add(task_id)


def _get_existing_activity_log_id_for_item(
    *,
    activity_service: ActivityService,
    item_type: str,
    item_key: str,
) -> int | None:
    if item_type not in {"request", "download"}:
        return None
    if not isinstance(item_key, str) or not item_key.strip():
        return None

    return activity_service.get_latest_activity_log_id(
        item_type=item_type,
        item_key=item_key,
    )


def register_activity_routes(
    app: Flask,
    user_db: UserDB,
    *,
    activity_service: ActivityService,
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

        scoped_user_id = None if is_admin else db_user_id
        status = queue_status(user_id=scoped_user_id)
        updated_requests = sync_request_delivery_states(
            user_db,
            queue_status=status,
            user_id=scoped_user_id,
        )
        emit_request_updates(updated_requests)
        request_rows = _list_visible_requests(user_db, is_admin=is_admin, db_user_id=db_user_id)

        if not is_admin and db_user_id is not None:
            try:
                terminal_rows = activity_service.get_undismissed_terminal_downloads(db_user_id, limit=200)
                _merge_terminal_snapshot_backfill(status=status, terminal_rows=terminal_rows)
            except Exception as exc:
                logger.warning("Failed to merge terminal snapshot backfill rows: %s", exc)

        dismissed: list[dict[str, str]] = []
        if db_user_id is not None:
            dismissed = activity_service.get_dismissal_set(db_user_id)

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

        db_user_id, db_gate = _resolve_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        activity_log_id = data.get("activity_log_id")
        if activity_log_id is None:
            try:
                activity_log_id = _get_existing_activity_log_id_for_item(
                    activity_service=activity_service,
                    item_type=data.get("item_type"),
                    item_key=data.get("item_key"),
                )
            except Exception as exc:
                logger.warning("Failed to resolve activity snapshot id for dismiss payload: %s", exc)
                activity_log_id = None

        try:
            dismissal = activity_service.dismiss_item(
                user_id=db_user_id,
                item_type=data.get("item_type"),
                item_key=data.get("item_key"),
                activity_log_id=activity_log_id,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        _emit_activity_event(
            ws_manager,
            room=f"user_{db_user_id}",
            payload={
                "kind": "dismiss",
                "user_id": db_user_id,
                "item_type": dismissal["item_type"],
                "item_key": dismissal["item_key"],
            },
        )

        return jsonify({"status": "dismissed", "item": dismissal})

    @app.route("/api/activity/dismiss-many", methods=["POST"])
    def api_activity_dismiss_many():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _resolve_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400
        items = data.get("items")
        if not isinstance(items, list):
            return jsonify({"error": "items must be an array"}), 400

        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                return jsonify({"error": "items must contain objects"}), 400

            activity_log_id = item.get("activity_log_id")
            if activity_log_id is None:
                try:
                    activity_log_id = _get_existing_activity_log_id_for_item(
                        activity_service=activity_service,
                        item_type=item.get("item_type"),
                        item_key=item.get("item_key"),
                    )
                except Exception as exc:
                    logger.warning("Failed to resolve activity snapshot id for dismiss-many item: %s", exc)
                    activity_log_id = None

            normalized_payload = {
                "item_type": item.get("item_type"),
                "item_key": item.get("item_key"),
            }
            if activity_log_id is not None:
                normalized_payload["activity_log_id"] = activity_log_id
            normalized_items.append(normalized_payload)

        try:
            dismissed_count = activity_service.dismiss_many(user_id=db_user_id, items=normalized_items)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        _emit_activity_event(
            ws_manager,
            room=f"user_{db_user_id}",
            payload={
                "kind": "dismiss_many",
                "user_id": db_user_id,
                "count": dismissed_count,
            },
        )

        return jsonify({"status": "dismissed", "count": dismissed_count})

    @app.route("/api/activity/history", methods=["GET"])
    def api_activity_history():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _resolve_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate

        limit = request.args.get("limit", type=int, default=50) or 50
        offset = request.args.get("offset", type=int, default=0) or 0

        try:
            history = activity_service.get_history(db_user_id, limit=limit, offset=offset)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(history)

    @app.route("/api/activity/history", methods=["DELETE"])
    def api_activity_history_clear():
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _resolve_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate

        deleted_count = activity_service.clear_history(db_user_id)
        _emit_activity_event(
            ws_manager,
            room=f"user_{db_user_id}",
            payload={
                "kind": "history_cleared",
                "user_id": db_user_id,
                "count": deleted_count,
            },
        )
        return jsonify({"status": "cleared", "deleted_count": deleted_count})

"""Book-level Request API routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Flask, Response, jsonify, request, session

from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    notify_admin,
    notify_user,
)
from shelfmark.core.request_helpers import (
    emit_ws_event,
    normalize_positive_int,
    populate_request_usernames,
)
from shelfmark.core.request_validation import RequestStatus
from shelfmark.core.requests_service import RequestServiceError, normalize_note

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue

    from shelfmark.core.user_db import UserDB


def _error(message: str, status_code: int, *, code: str | None = None) -> tuple[Response, int]:
    payload: dict[str, Any] = {"error": message}
    if code is not None:
        payload["code"] = code
    return jsonify(payload), status_code


def _require_authenticated(resolve_auth_mode: Callable[[], str]) -> tuple[Response, int] | None:
    if resolve_auth_mode() == "none":
        return _error("Request workflow is unavailable in no-auth mode", 403)
    if "user_id" not in session:
        return _error("Unauthorized", 401)
    return None


def _current_user_id() -> int | None:
    return normalize_positive_int(session.get("db_user_id"))


def _emit_request_update(
    ws_manager: object | None, request_row: dict[str, Any], *, include_admins: bool = True
) -> None:
    payload = {
        "request_id": request_row["id"],
        "book_id": request_row["book_id"],
        "status": request_row["status"],
    }
    emit_ws_event(
        ws_manager,
        event_name="request_update",
        payload=payload,
        room=f"user_{request_row['user_id']}",
    )
    if include_admins:
        emit_ws_event(ws_manager, event_name="request_update", payload=payload, room="admins")


def _notify_request(user_db: UserDB, request_row: dict[str, Any], event: NotificationEvent) -> None:
    book = user_db.get_book_notification_context(request_row["book_id"])
    if book is None:
        return
    context = NotificationContext(
        event=event,
        title=book["title"],
        author=book["author"],
        admin_note=request_row.get("admin_note"),
        book_id=book["id"],
        book=book,
    )
    if event == NotificationEvent.REQUEST_CREATED:
        requester = user_db.get_user(user_id=request_row["user_id"])
        context.username = requester.get("username") if requester else None
        notify_admin(event, context)
    else:
        notify_user(user_db, request_row["user_id"], event, context)


def register_request_routes(
    app: Flask,
    user_db: UserDB,
    *,
    resolve_auth_mode: Callable[[], str],
    queue_release: Callable[..., tuple[bool, str | None]],
    ws_manager: object | None = None,
) -> None:
    """Register the canonical Book-level Request lifecycle."""

    @app.route("/api/requests", methods=["POST"])
    def api_create_request() -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        actor_id = _current_user_id()
        if actor_id is None:
            return _error("User identity is unavailable", 403, code="user_identity_unavailable")
        actor = user_db.get_user(user_id=actor_id)
        if actor is None or actor.get("library_capability") != "request-only":
            return _error("Request-only capability required", 403)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("No data provided", 400)
        book_id = normalize_positive_int(data.get("book_id"))
        if book_id is None:
            return _error("book_id must be a positive integer", 400)
        try:
            created = user_db.create_library_request(
                user_id=actor_id,
                book_id=book_id,
                note=normalize_note(data.get("note")),
            )
        except RequestServiceError as exc:
            return _error(str(exc), exc.status_code, code=exc.code)
        except ValueError as exc:
            return _error(str(exc), 409)
        _emit_request_update(ws_manager, created)
        _notify_request(user_db, created, NotificationEvent.REQUEST_CREATED)
        return jsonify(created), 201

    @app.route("/api/requests", methods=["GET"])
    def api_list_requests() -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        actor_id = _current_user_id()
        if actor_id is None:
            return _error("User identity is unavailable", 403, code="user_identity_unavailable")
        status = request.args.get("status")
        try:
            return jsonify(user_db.list_requests(user_id=actor_id, status=status))
        except ValueError as exc:
            return _error(str(exc), 400)

    @app.route("/api/requests/<int:request_id>", methods=["DELETE"])
    def api_cancel_request(request_id: int) -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        actor_id = _current_user_id()
        if actor_id is None:
            return _error("User identity is unavailable", 403, code="user_identity_unavailable")
        request_row = user_db.get_request(request_id)
        if request_row is None:
            return _error("Request not found", 404)
        if request_row["user_id"] != actor_id:
            return _error("Forbidden", 403)
        if request_row["status"] != RequestStatus.PENDING:
            return _error("Request is already in a terminal state", 409, code="stale_transition")
        try:
            updated = user_db.update_request(
                request_id,
                expected_current_status=RequestStatus.PENDING,
                status=RequestStatus.CANCELLED,
            )
        except ValueError:
            return _error("Request state changed before update", 409, code="stale_transition")
        _emit_request_update(ws_manager, updated)
        return jsonify(updated)

    @app.route("/api/admin/requests", methods=["GET"])
    def api_admin_list_requests() -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return _error("Admin access required", 403)
        try:
            rows = user_db.list_requests(status=request.args.get("status"))
        except ValueError as exc:
            return _error(str(exc), 400)
        populate_request_usernames(rows, user_db)
        return jsonify(rows)

    @app.route("/api/admin/requests/count", methods=["GET"])
    def api_admin_request_counts() -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return _error("Admin access required", 403)
        counts = {status: len(user_db.list_requests(status=status)) for status in RequestStatus}
        return jsonify({"pending": counts[RequestStatus.PENDING], "by_status": counts})

    @app.route("/api/admin/requests/<int:request_id>/reject", methods=["POST"])
    def api_admin_reject_request(request_id: int) -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        admin_id = _current_user_id()
        if not session.get("is_admin", False) or admin_id is None:
            return _error("Admin access required", 403)
        request_row = user_db.get_request(request_id)
        if request_row is None:
            return _error("Request not found", 404)
        if request_row["status"] != RequestStatus.PENDING:
            return _error("Request is already in a terminal state", 409, code="stale_transition")
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return _error("Invalid payload", 400)
        try:
            updated = user_db.update_request(
                request_id,
                expected_current_status=RequestStatus.PENDING,
                status=RequestStatus.REJECTED,
                admin_note=normalize_note(data.get("admin_note")),
                reviewed_by=admin_id,
            )
        except RequestServiceError, ValueError:
            return _error("Request state changed before update", 409, code="stale_transition")
        _emit_request_update(ws_manager, updated)
        _notify_request(user_db, updated, NotificationEvent.REQUEST_REJECTED)
        return jsonify(updated)

    @app.route("/api/admin/requests/books/<int:book_id>/fulfil", methods=["POST"])
    def api_admin_fulfil_book_requests(book_id: int) -> ResponseReturnValue:
        auth_gate = _require_authenticated(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        admin_id = _current_user_id()
        if not session.get("is_admin", False) or admin_id is None:
            return _error("Admin access required", 403)
        pending = user_db.list_pending_book_requests(book_id)
        if not pending:
            return _error("No pending Requests for this Book", 409)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return _error("Invalid payload", 400)
        release_data = data.get("release_data")
        if release_data is None:
            return _error("release_data is required", 400)
        if not isinstance(release_data, dict):
            return _error("release_data must be an object", 400)
        if not release_data:
            return _error("release_data must be a non-empty object", 400)
        admin = user_db.get_user(user_id=admin_id)
        if admin is None:
            return _error("Admin user identity unavailable", 403)
        queued_release = dict(release_data)
        queued_release["library_book_id"] = book_id
        success, error = queue_release(
            queued_release, 0, user_id=admin_id, username=admin.get("username")
        )
        if not success:
            return _error(error or "Failed to queue release", 409, code="queue_failed")
        return jsonify({"status": "queued", "book_id": book_id})

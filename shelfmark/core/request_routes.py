"""Request API routes and policy snapshot endpoint."""

from __future__ import annotations

from typing import Any, Callable

from flask import Flask, jsonify, request, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_policy import (
    PolicyMode,
    REQUEST_POLICY_DEFAULT_FALLBACK_MODE,
    get_source_content_type_capabilities,
    merge_request_policy_settings,
    normalize_content_type,
    normalize_source,
    parse_policy_mode,
    resolve_policy_mode,
)
from shelfmark.core.requests_service import (
    RequestServiceError,
    cancel_request,
    create_request,
    fulfil_request,
    reject_request,
)
from shelfmark.core.activity_service import ActivityService, build_request_item_key
from shelfmark.core.notifications import (
    NotificationContext,
    NotificationEvent,
    notify_admin,
    notify_user,
)
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def _load_users_request_policy_settings() -> dict[str, Any]:
    """Load global request-policy settings from users config."""
    return load_config_file("users")


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _error_response(
    message: str,
    status_code: int,
    *,
    code: str | None = None,
    required_mode: str | None = None,
):
    payload: dict[str, Any] = {"error": message}
    if code is not None:
        payload["code"] = code
    if required_mode is not None:
        payload["required_mode"] = required_mode
    return jsonify(payload), status_code


def _require_request_endpoints_available(resolve_auth_mode: Callable[[], str]):
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return _error_response(
            "Request workflow is unavailable in no-auth mode",
            403,
            code="requests_unavailable",
        )
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _require_db_user_id() -> tuple[int | None, Any | None]:
    raw_user_id = session.get("db_user_id")
    if raw_user_id is None:
        return None, _error_response(
            "User identity is unavailable for request workflow",
            403,
            code="user_identity_unavailable",
        )
    try:
        return int(raw_user_id), None
    except (TypeError, ValueError):
        return None, _error_response(
            "User identity is unavailable for request workflow",
            403,
            code="user_identity_unavailable",
        )


def _resolve_effective_policy(
    user_db: UserDB,
    *,
    db_user_id: int | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bool]:
    global_settings = _load_users_request_policy_settings()
    user_settings = user_db.get_user_settings(db_user_id) if db_user_id is not None else {}
    effective = merge_request_policy_settings(global_settings, user_settings)
    requests_enabled = _as_bool(effective.get("REQUESTS_ENABLED"), False)
    return global_settings, user_settings, effective, requests_enabled


def _emit_request_event(
    ws_manager: Any,
    *,
    event_name: str,
    payload: dict[str, Any],
    room: str,
) -> None:
    if ws_manager is None:
        return
    try:
        socketio = getattr(ws_manager, "socketio", None)
        is_enabled = getattr(ws_manager, "is_enabled", None)
        if socketio is None or not callable(is_enabled) or not is_enabled():
            return
        socketio.emit(event_name, payload, to=room)
    except Exception as exc:
        logger.warning(f"Failed to emit WebSocket event '{event_name}' to room '{room}': {exc}")


def _extract_release_source_id(release_data: Any) -> str | None:
    if not isinstance(release_data, dict):
        return None
    source_id = release_data.get("source_id")
    if not isinstance(source_id, str):
        return None
    normalized = source_id.strip()
    return normalized or None


def _record_terminal_request_snapshot(
    activity_service: ActivityService | None,
    *,
    request_row: dict[str, Any],
) -> None:
    if activity_service is None:
        return

    request_status = request_row.get("status")
    if request_status not in {"rejected", "cancelled"}:
        return

    raw_request_id = request_row.get("id")
    try:
        request_id = int(raw_request_id)
    except (TypeError, ValueError):
        return
    if request_id < 1:
        return

    raw_user_id = request_row.get("user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        user_id = None

    source_id = _extract_release_source_id(request_row.get("release_data"))

    try:
        activity_service.record_terminal_snapshot(
            user_id=user_id,
            item_type="request",
            item_key=build_request_item_key(request_id),
            origin="request",
            final_status=request_status,
            snapshot={"kind": "request", "request": request_row},
            request_id=request_id,
            source_id=source_id,
        )
    except Exception as exc:
        logger.warning("Failed to record terminal request snapshot for request %s: %s", request_id, exc)


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _resolve_title_from_book_data(book_data: Any) -> str:
    if isinstance(book_data, dict):
        title = _normalize_optional_text(book_data.get("title"))
        if title is not None:
            return title
    return "Unknown title"


def _resolve_request_title(request_row: dict[str, Any]) -> str:
    return _resolve_title_from_book_data(request_row.get("book_data"))


def _format_user_label(username: str | None, user_id: int | None = None) -> str:
    normalized_username = _normalize_optional_text(username)
    if normalized_username is not None:
        return normalized_username
    if user_id is not None and user_id > 0:
        return f"user#{user_id}"
    return "unknown user"


def _resolve_request_username(
    user_db: UserDB,
    *,
    request_row: dict[str, Any],
    fallback_username: str | None = None,
) -> str | None:
    normalized_fallback = _normalize_optional_text(fallback_username)
    raw_user_id = request_row.get("user_id")
    try:
        request_user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return normalized_fallback

    requester = user_db.get_user(user_id=request_user_id)
    if not isinstance(requester, dict):
        return normalized_fallback
    return _normalize_optional_text(requester.get("username")) or normalized_fallback


def _resolve_request_source_and_format(request_row: dict[str, Any]) -> tuple[str, str | None]:
    release_data = request_row.get("release_data")
    if isinstance(release_data, dict):
        source = normalize_source(release_data.get("source") or request_row.get("source_hint"))
        release_format = _normalize_optional_text(
            release_data.get("format")
            or release_data.get("filetype")
            or release_data.get("extension")
        )
        return source, release_format
    return normalize_source(request_row.get("source_hint")), None


def _resolve_request_user_id(request_row: dict[str, Any]) -> int | None:
    raw_user_id = request_row.get("user_id")
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def _notify_admin_for_request_event(
    user_db: UserDB,
    *,
    event: NotificationEvent,
    request_row: dict[str, Any],
    fallback_username: str | None = None,
) -> None:
    book_data = request_row.get("book_data")
    if not isinstance(book_data, dict):
        book_data = {}

    source, release_format = _resolve_request_source_and_format(request_row)
    context = NotificationContext(
        event=event,
        title=str(book_data.get("title") or "Unknown title"),
        author=str(book_data.get("author") or "Unknown author"),
        username=_resolve_request_username(
            user_db,
            request_row=request_row,
            fallback_username=fallback_username,
        ),
        content_type=normalize_content_type(
            request_row.get("content_type") or book_data.get("content_type")
        ),
        format=release_format,
        source=source,
        admin_note=_normalize_optional_text(request_row.get("admin_note")),
        error_message=None,
    )

    owner_user_id = _resolve_request_user_id(request_row)
    try:
        notify_admin(event, context)
    except Exception as exc:
        logger.warning(
            "Failed to trigger admin notification for request event '%s': %s",
            event.value,
            exc,
        )
    if owner_user_id is None:
        return
    try:
        notify_user(owner_user_id, event, context)
    except Exception as exc:
        logger.warning(
            "Failed to trigger user notification for request event '%s' (user_id=%s): %s",
            event.value,
            owner_user_id,
            exc,
        )


def register_request_routes(
    app: Flask,
    user_db: UserDB,
    *,
    resolve_auth_mode: Callable[[], str],
    queue_release: Callable[..., tuple[bool, str | None]],
    activity_service: ActivityService | None = None,
    ws_manager: Any | None = None,
) -> None:
    """Register request policy and request lifecycle routes."""

    @app.route("/api/request-policy", methods=["GET"])
    def api_request_policy():
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        is_admin = bool(session.get("is_admin", False))
        db_user_id: int | None = None
        if not is_admin:
            db_user_id, db_gate = _require_db_user_id()
            if db_gate is not None:
                return db_gate
        else:
            raw_id = session.get("db_user_id")
            if raw_id is not None:
                try:
                    db_user_id = int(raw_id)
                except (TypeError, ValueError):
                    db_user_id = None

        global_settings, user_settings, effective, requests_enabled = _resolve_effective_policy(
            user_db,
            db_user_id=db_user_id,
        )

        default_ebook_mode = parse_policy_mode(effective.get("REQUEST_POLICY_DEFAULT_EBOOK"))
        default_audio_mode = parse_policy_mode(effective.get("REQUEST_POLICY_DEFAULT_AUDIOBOOK"))

        source_capabilities = get_source_content_type_capabilities()
        source_modes = []
        for source_name in sorted(source_capabilities):
            supported_types = sorted(
                source_capabilities[source_name],
                key=lambda ct: (ct != "ebook", ct),
            )
            modes = {
                content_type: resolve_policy_mode(
                    source=source_name,
                    content_type=content_type,
                    global_settings=global_settings,
                    user_settings=user_settings,
                ).value
                for content_type in supported_types
            }
            source_modes.append(
                {
                    "source": source_name,
                    "supported_content_types": supported_types,
                    "modes": modes,
                }
            )

        return jsonify(
            {
                "requests_enabled": requests_enabled,
                "is_admin": is_admin,
                "allow_notes": _as_bool(effective.get("REQUESTS_ALLOW_NOTES"), default=True),
                "defaults": {
                    "ebook": (
                        default_ebook_mode.value
                        if default_ebook_mode is not None
                        else REQUEST_POLICY_DEFAULT_FALLBACK_MODE.value
                    ),
                    "audiobook": (
                        default_audio_mode.value
                        if default_audio_mode is not None
                        else REQUEST_POLICY_DEFAULT_FALLBACK_MODE.value
                    ),
                },
                "rules": effective.get("REQUEST_POLICY_RULES", []),
                "source_modes": source_modes,
            }
        )

    @app.route("/api/requests", methods=["POST"])
    def api_create_request():
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _require_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate
        actor_username = _normalize_optional_text(session.get("user_id"))
        actor_label = _format_user_label(actor_username, db_user_id)

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "No data provided"}), 400

        context = data.get("context") or {}
        if not isinstance(context, dict):
            return jsonify({"error": "context must be an object"}), 400

        source = normalize_source(context.get("source"))
        release_data = data.get("release_data")
        request_level = context.get("request_level")
        if request_level is None:
            request_level = "book" if release_data is None else "release"

        book_data = data.get("book_data")
        if not isinstance(book_data, dict):
            return jsonify({"error": "book_data must be an object"}), 400
        request_title = _resolve_title_from_book_data(book_data)

        content_type = normalize_content_type(
            context.get("content_type")
            or data.get("content_type")
            or book_data.get("content_type")
        )

        global_settings, user_settings, effective, requests_enabled = _resolve_effective_policy(
            user_db,
            db_user_id=db_user_id,
        )
        if not requests_enabled:
            logger.debug(
                "Request not created for '%s' by %s: requests are disabled",
                request_title,
                actor_label,
            )
            return _error_response(
                "Request workflow is disabled by policy",
                403,
                code="requests_unavailable",
            )

        max_pending = _as_int(
            effective.get("MAX_PENDING_REQUESTS_PER_USER"),
            default=20,
        )
        if max_pending < 1:
            max_pending = 1
        if max_pending > 1000:
            max_pending = 1000
        allow_notes = _as_bool(effective.get("REQUESTS_ALLOW_NOTES"), default=True)
        note_value = data.get("note") if allow_notes else None

        resolved_mode = resolve_policy_mode(
            source=source,
            content_type=content_type,
            global_settings=global_settings,
            user_settings=user_settings,
        )
        logger.debug(
            "request create policy user=%s db_user_id=%s source=%s content_type=%s request_level=%s resolved_mode=%s",
            session.get("user_id"),
            db_user_id,
            source,
            content_type,
            request_level,
            resolved_mode.value,
        )

        if resolved_mode == PolicyMode.BLOCKED:
            logger.debug(
                "Request blocked by policy for '%s' by %s",
                request_title,
                actor_label,
            )
            return _error_response(
                "Requesting is blocked by policy",
                403,
                code="policy_blocked",
                required_mode=PolicyMode.BLOCKED.value,
            )

        if resolved_mode == PolicyMode.REQUEST_BOOK:
            requested_level = str(request_level).strip().lower() if isinstance(request_level, str) else ""
            # Direct search results are already concrete releases, so allow release-level
            # request payloads even when the policy default is request_book.
            allow_direct_release_payload = source == "direct_download" and requested_level == "release"
            if requested_level != "book" and not allow_direct_release_payload:
                logger.debug(
                    "Request not created for '%s' by %s: policy requires book-level requests",
                    request_title,
                    actor_label,
                )
                return _error_response(
                    "Policy requires book-level requests",
                    403,
                    code="policy_requires_request",
                    required_mode=PolicyMode.REQUEST_BOOK.value,
                )

        try:
            created = create_request(
                user_db,
                user_id=db_user_id,
                source_hint=source,
                content_type=content_type,
                request_level=request_level,
                policy_mode=resolved_mode.value,
                book_data=book_data,
                release_data=release_data,
                note=note_value,
                max_pending_per_user=max_pending,
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        event_payload = {
            "request_id": created["id"],
            "status": created["status"],
            "title": _resolve_request_title(created),
        }
        logger.info(
            "Request created #%s for '%s' by %s",
            created["id"],
            event_payload["title"],
            actor_label,
        )
        _emit_request_event(
            ws_manager,
            event_name="new_request",
            payload=event_payload,
            room="admins",
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{db_user_id}",
        )

        _notify_admin_for_request_event(
            user_db,
            event=NotificationEvent.REQUEST_CREATED,
            request_row=created,
            fallback_username=actor_username,
        )

        return jsonify(created), 201

    @app.route("/api/requests", methods=["GET"])
    def api_list_requests():
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _require_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate

        status = request.args.get("status")
        limit = request.args.get("limit", type=int)
        offset = request.args.get("offset", type=int, default=0) or 0

        try:
            rows = user_db.list_requests(
                user_id=db_user_id,
                status=status,
                limit=limit,
                offset=offset,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(rows)

    @app.route("/api/requests/<int:request_id>", methods=["DELETE"])
    def api_cancel_request(request_id: int):
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate

        db_user_id, db_gate = _require_db_user_id()
        if db_gate is not None or db_user_id is None:
            return db_gate

        try:
            updated = cancel_request(
                user_db,
                request_id=request_id,
                actor_user_id=db_user_id,
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        _record_terminal_request_snapshot(activity_service, request_row=updated)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        actor_label = _format_user_label(_normalize_optional_text(session.get("user_id")), db_user_id)
        logger.info(
            "Request cancelled #%s for '%s' by %s",
            updated["id"],
            event_payload["title"],
            actor_label,
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{db_user_id}",
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        return jsonify(updated)

    @app.route("/api/admin/requests", methods=["GET"])
    def api_admin_list_requests():
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        status = request.args.get("status")
        limit = request.args.get("limit", type=int)
        offset = request.args.get("offset", type=int, default=0) or 0

        try:
            rows = user_db.list_requests(status=status, limit=limit, offset=offset)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        user_cache: dict[int, str] = {}
        for row in rows:
            requester_id = row["user_id"]
            if requester_id not in user_cache:
                requester = user_db.get_user(user_id=requester_id)
                user_cache[requester_id] = requester.get("username", "") if requester else ""
            row["username"] = user_cache[requester_id]

        return jsonify(rows)

    @app.route("/api/admin/requests/count", methods=["GET"])
    def api_admin_request_counts():
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        by_status = {
            status: len(user_db.list_requests(status=status))
            for status in ("pending", "fulfilled", "rejected", "cancelled")
        }
        return jsonify(
            {
                "pending": by_status["pending"],
                "total": sum(by_status.values()),
                "by_status": by_status,
            }
        )

    @app.route("/api/admin/requests/<int:request_id>/fulfil", methods=["POST"])
    def api_admin_fulfil_request(request_id: int):
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        raw_admin_id = session.get("db_user_id")
        if raw_admin_id is None:
            return jsonify({"error": "Admin user identity unavailable"}), 403
        try:
            admin_user_id = int(raw_admin_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Admin user identity unavailable"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        try:
            updated = fulfil_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                queue_release=queue_release,
                release_data=data.get("release_data"),
                admin_note=data.get("admin_note"),
                manual_approval=data.get("manual_approval", False),
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        admin_label = _format_user_label(_normalize_optional_text(session.get("user_id")), admin_user_id)
        requester_label = _format_user_label(
            _resolve_request_username(user_db, request_row=updated),
            _resolve_request_user_id(updated),
        )
        logger.info(
            "Request fulfilled #%s for '%s' by %s (requested by %s)",
            updated["id"],
            event_payload["title"],
            admin_label,
            requester_label,
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{updated['user_id']}",
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        _notify_admin_for_request_event(
            user_db,
            event=NotificationEvent.REQUEST_FULFILLED,
            request_row=updated,
        )

        return jsonify(updated)

    @app.route("/api/admin/requests/<int:request_id>/reject", methods=["POST"])
    def api_admin_reject_request(request_id: int):
        auth_gate = _require_request_endpoints_available(resolve_auth_mode)
        if auth_gate is not None:
            return auth_gate
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        raw_admin_id = session.get("db_user_id")
        if raw_admin_id is None:
            return jsonify({"error": "Admin user identity unavailable"}), 403
        try:
            admin_user_id = int(raw_admin_id)
        except (TypeError, ValueError):
            return jsonify({"error": "Admin user identity unavailable"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        try:
            updated = reject_request(
                user_db,
                request_id=request_id,
                admin_user_id=admin_user_id,
                admin_note=data.get("admin_note"),
            )
        except RequestServiceError as exc:
            return _error_response(str(exc), exc.status_code, code=exc.code)

        _record_terminal_request_snapshot(activity_service, request_row=updated)

        event_payload = {
            "request_id": updated["id"],
            "status": updated["status"],
            "title": _resolve_request_title(updated),
        }
        admin_label = _format_user_label(_normalize_optional_text(session.get("user_id")), admin_user_id)
        requester_label = _format_user_label(
            _resolve_request_username(user_db, request_row=updated),
            _resolve_request_user_id(updated),
        )
        logger.info(
            "Request rejected #%s for '%s' by %s (requested by %s)",
            updated["id"],
            event_payload["title"],
            admin_label,
            requester_label,
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room=f"user_{updated['user_id']}",
        )
        _emit_request_event(
            ws_manager,
            event_name="request_update",
            payload=event_payload,
            room="admins",
        )

        _notify_admin_for_request_event(
            user_db,
            event=NotificationEvent.REQUEST_REJECTED,
            request_row=updated,
        )

        return jsonify(updated)

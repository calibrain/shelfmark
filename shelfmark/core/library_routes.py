"""Library API routes — per-user ebook library per ticket #04's contract.

Routes live in this module registered via ``register_library_routes`` —
matches the project's existing ``register_*_routes`` convention
(activity_routes, request_routes, admin_routes, self_user_routes). Not a
Flask Blueprint — none exists in the codebase today.

Ownership rules enforce #04 sub-decisions:
- File serving gates on ``user_library`` membership (NOT ``download_history.user_id``)
  (sub-decision 7) — closes the existing cross-user byte-exposure leak.
- Admin scoping: instance-wide read, scoped-to-self mutations (sub-decision 2).

"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from flask import Flask, Response, jsonify, request, send_file, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import (
    get_session_db_user_id,
    normalize_optional_text,
    normalize_positive_int,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.library_service import LibraryService
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_USER_DB_IDENTITY_ERRORS = (sqlite3.Error, OSError)
_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_LIBRARY_PROVIDER_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)


class _ActorContext(NamedTuple):
    db_user_id: int
    is_admin: bool
    owner_scope: int | None  # None → admin sees all


type LibraryRouteResponse = tuple[Response, int]
type ActorResolution = tuple[_ActorContext | None, LibraryRouteResponse | None]


def _require_authenticated(
    resolve_auth_mode: Callable[[], str], *, action: str
) -> LibraryRouteResponse | None:
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return None
    if "user_id" not in session:
        logger.warning("Library %s rejected: status=401 reason=unauthorized", action)
        return jsonify({"error": "Unauthorized"}), 401
    return None


def _resolve_library_actor(
    *,
    user_db: UserDB,
    resolve_auth_mode: Callable[[], str],
    action: str,
) -> ActorResolution:
    """Resolve acting user identity for library operations.

    Auth-mode "none" is admin-equivalent (matches activity_routes' NOAUTH_VIEWER_SCOPE).
    """
    auth_mode = resolve_auth_mode()
    if auth_mode == "none":
        return _ActorContext(db_user_id=0, is_admin=True, owner_scope=None), None

    raw_db_user_id = get_session_db_user_id(session)
    if raw_db_user_id is None:
        logger.warning("Library %s rejected: missing db_user_id", action)
        return None, (jsonify({"error": "Forbidden"}), 403)

    try:
        db_user_id = int(raw_db_user_id)
    except TypeError, ValueError:
        logger.warning("Library %s rejected: invalid db_user_id=%r", action, raw_db_user_id)
        return None, (jsonify({"error": "Forbidden"}), 403)
    if db_user_id < 1:
        return None, (jsonify({"error": "Forbidden"}), 403)

    try:
        db_user = user_db.get_user(user_id=db_user_id)
    except _USER_DB_IDENTITY_ERRORS as exc:
        logger.warning("Library %s rejected: user lookup failed: %s", action, exc)
        return None, (jsonify({"error": "Forbidden"}), 403)
    if db_user is None:
        return None, (jsonify({"error": "Forbidden"}), 403)

    is_admin = bool(session.get("is_admin"))
    owner_scope = None if is_admin else db_user_id
    return _ActorContext(
        db_user_id=db_user_id,
        is_admin=is_admin,
        owner_scope=owner_scope,
    ), None


def _require_library_membership(
    *,
    actor: _ActorContext,
    library_service: LibraryService,
    book_id: int,
    action: str,
) -> LibraryRouteResponse | None:
    """Gate file serving & send-to-kindle on user_library membership (sub-decision 7).

    Admin sees any book (instance-wide read per sub-decision 2). Non-members
    get 403 — including for books with files they triggered but didn't keep
    linked. The dedicated library download endpoint is the only path that
    serves file bytes.
    """
    if actor.is_admin:
        return None
    if library_service.is_in_library(user_id=actor.db_user_id, book_id=book_id):
        return None
    logger.warning(
        "Library %s rejected: status=403 book_id=%s actor=%s reason=not_in_library",
        action,
        book_id,
        actor.db_user_id,
    )
    return jsonify({"error": "Forbidden"}), 403


def _error_response(
    *,
    action: str,
    status_code: int,
    error: str,
    book_id: int | None = None,
) -> LibraryRouteResponse:
    logger.warning(
        "Library %s rejected: status=%s book_id=%s reason=%s",
        action,
        status_code,
        book_id,
        error,
    )
    return jsonify({"error": error}), status_code


def _normalize_book_id_param(raw: Any) -> int | None:
    return normalize_positive_int(raw)


def _serialize_book_summary(book: dict[str, Any], *, library_added_at: Any) -> dict[str, Any]:
    """Per #04 route table: GET /api/library/books response shape."""
    return {
        "book_id": book["id"],
        "title": book.get("title"),
        "author": book.get("author"),
        "cover_url": book.get("cover_url"),
        "formats_on_disk": [],  # filled by caller with union of file list
        "added_at": library_added_at,
    }


def _serialize_book_detail(
    book: dict[str, Any],
    *,
    files: list[dict[str, Any]],
    in_flight: list[dict[str, Any]],
    downloadable_history_ids: set[int],
) -> dict[str, Any]:
    """Per #04 route table: GET /api/library/books/:book_id response shape."""
    return {
        "book_id": book["id"],
        "metadata_provider": book.get("metadata_provider"),
        "provider_book_id": book.get("provider_book_id"),
        "title": book.get("title"),
        "author": book.get("author"),
        "subtitle": book.get("subtitle"),
        "publish_year": book.get("publish_year"),
        "isbn_13": book.get("isbn_13"),
        "cover_url": book.get("cover_url"),
        "series_name": book.get("series_name"),
        "series_position": book.get("series_position"),
        "language": book.get("language"),
        "metadata_json": book.get("metadata_json") or {},
        "files": [
            {
                "history_id": f["id"],
                "task_id": f.get("task_id"),
                "format": f.get("format"),
                "size": f.get("size"),
                "indexer_display_name": f.get("source_display_name") or f.get("source"),
                "protocol": f.get("content_type"),
                "downloaded_at": f.get("terminal_at"),
                "downloadable_by_me": int(f["id"]) in downloadable_history_ids,
            }
            for f in files
        ],
        "in_flight": [
            {
                "history_id": f["id"],
                "task_id": f.get("task_id"),
                "format": f.get("format"),
                "source_display_name": f.get("source_display_name") or f.get("source"),
            }
            for f in in_flight
        ],
    }


def register_library_routes(
    app: Flask,
    user_db: UserDB,
    *,
    library_service: LibraryService,
    download_history_service: DownloadHistoryService,
    resolve_auth_mode: Callable[[], str],
    resolve_metadata_book: Callable[[str, str], dict[str, Any] | None],
) -> None:
    """Register library API routes.

    Args:
        app: Flask app.
        user_db: User database (for actor resolution).
        library_service: Library data-layer service.
        download_history_service: Download history service (for path resolution).
        resolve_auth_mode: Returns the active auth mode ("none"/"builtin"/...).
        resolve_metadata_book: Resolves ``(provider, provider_book_id)`` to a
            metadata dict (#04 sub-decision 13) — invoked at Add time. Returns
            ``None`` when the provider is unavailable. The route translates a
            ``None`` into a 503.

    """

    def _actor_gate(action: str) -> Response | LibraryRouteResponse | _ActorContext:
        auth_gate = _require_authenticated(resolve_auth_mode, action=action)
        if auth_gate is not None:
            return auth_gate
        actor, actor_error = _resolve_library_actor(
            user_db=user_db, resolve_auth_mode=resolve_auth_mode, action=action
        )
        if actor_error is not None:
            return actor_error
        if actor is None:  # Defensive — _resolve_library_actor always returns one or the other.
            return _error_response(action=action, status_code=500, error="Internal Server Error")
        return actor

    def _membership_or_403(
        actor: _ActorContext, book_id: int, action: str
    ) -> LibraryRouteResponse | None:
        return _require_library_membership(
            actor=actor, library_service=library_service, book_id=book_id, action=action
        )

    @app.route("/api/library/books", methods=["POST"])
    def api_library_add_book() -> Response | LibraryRouteResponse:
        action = "add_book"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error_response(action=action, status_code=400, error="Invalid payload")

        metadata_provider = normalize_optional_text(data.get("metadata_provider"))
        provider_book_id = normalize_optional_text(data.get("provider_book_id"))
        if not metadata_provider or not provider_book_id:
            return _error_response(
                action=action,
                status_code=400,
                error="metadata_provider and provider_book_id are required",
            )

        try:
            metadata_payload = resolve_metadata_book(metadata_provider, provider_book_id)
        except _LIBRARY_PROVIDER_ERRORS as exc:
            logger.warning("Library add_book metadata fetch failed: %s", exc)
            return jsonify({"error": "Metadata provider unavailable"}), 503
        if metadata_payload is None:
            return jsonify({"error": "Metadata provider unavailable"}), 503

        try:
            book = library_service.upsert_book_from_metadata(
                metadata_provider=metadata_provider,
                provider_book_id=provider_book_id,
                title=metadata_payload["title"],
                author=metadata_payload.get("author"),
                subtitle=metadata_payload.get("subtitle"),
                publish_year=metadata_payload.get("publish_year"),
                isbn_13=metadata_payload.get("isbn_13"),
                cover_url=metadata_payload.get("cover_url"),
                series_name=metadata_payload.get("series_name"),
                series_position=metadata_payload.get("series_position"),
                language=metadata_payload.get("language"),
                metadata_json=metadata_payload.get("metadata_json") or {},
            )
            library_service.add_to_library(user_id=actor.db_user_id, book_id=int(book["id"]))
        except _OPERATIONAL_ERRORS as exc:
            logger.warning("Library add_book failed: %s", exc)
            return jsonify({"error": str(exc)}), 500

        book_id = int(book["id"])
        return jsonify(
            {
                "book_id": book_id,
                "files_exist_globally": library_service.files_exist_globally(book_id),
                "in_flight_globally": library_service.in_flight_globally(book_id),
                "in_my_library": True,
            }
        )

    @app.route("/api/library/books", methods=["GET"])
    def api_library_list_books() -> Response | LibraryRouteResponse:
        action = "list_books"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        query = request.args.get("q", type=str) or None
        try:
            books = library_service.list_library_books(
                user_id=actor.owner_scope,
                is_admin=actor.is_admin,
                query=query,
            )
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500

        serialized: list[dict[str, Any]] = []
        for row in books:
            book_id = int(row["id"])
            files = library_service.get_files_on_disk(book_id)
            summary = _serialize_book_summary(row, library_added_at=row.get("library_added_at"))
            summary["formats_on_disk"] = [
                {"format": f.get("format"), "size": f.get("size")} for f in files
            ]
            serialized.append(summary)
        return jsonify({"books": serialized})

    @app.route("/api/library/books/<int:book_id>", methods=["GET"])
    def api_library_book_detail(book_id: int) -> Response | LibraryRouteResponse:
        action = "book_detail"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        try:
            book = library_service.get_book(book_id)
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        if book is None:
            return _error_response(
                action=action, status_code=404, error="Book not found", book_id=book_id
            )

        membership_error = _membership_or_403(actor, book_id, action)
        if membership_error is not None:
            return membership_error

        try:
            files = library_service.get_files_on_disk(book_id)
            in_flight = library_service.get_in_flight_files(book_id)
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500

        downloadable_history_ids: set[int] = set()
        if not actor.is_admin:
            for f in files:
                history_id = normalize_positive_int(f.get("id"))
                if history_id is None:
                    continue
                if library_service.download_linked_to_user(
                    user_id=actor.db_user_id, history_id=history_id
                ):
                    downloadable_history_ids.add(history_id)
        else:
            for f in files:
                history_id = normalize_positive_int(f.get("id"))
                if history_id is not None:
                    downloadable_history_ids.add(history_id)

        detail = _serialize_book_detail(
            book,
            files=files,
            in_flight=in_flight,
            downloadable_history_ids=downloadable_history_ids,
        )
        detail["in_my_library"] = library_service.is_in_library(
            user_id=actor.db_user_id, book_id=book_id
        )
        return jsonify(detail)

    @app.route("/api/library/books/<int:book_id>", methods=["DELETE"])
    def api_library_remove_book(book_id: int) -> Response | LibraryRouteResponse:
        action = "remove_book"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        # Admin scoped to own library (sub-decision 2).
        if not library_service.is_in_library(user_id=actor.db_user_id, book_id=book_id):
            return _error_response(
                action=action, status_code=404, error="Book not found", book_id=book_id
            )
        try:
            library_service.remove_from_library(user_id=actor.db_user_id, book_id=book_id)
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"status": "removed"})

    @app.route("/api/library/books/<int:book_id>/download", methods=["GET"])
    def api_library_download_file(book_id: int) -> Response | LibraryRouteResponse:
        action = "download_file"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        membership_error = _membership_or_403(actor, book_id, action)
        if membership_error is not None:
            return membership_error

        fmt = normalize_optional_text(request.args.get("format"))
        history_id = normalize_positive_int(request.args.get("history_id"))
        try:
            files = library_service.get_files_on_disk(book_id)
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500

        def _matches(row: dict[str, Any]) -> bool:
            if history_id is not None and normalize_positive_int(row.get("id")) != history_id:
                return False
            if fmt is None:
                return True
            row_format = normalize_optional_text(row.get("format"))
            return row_format is not None and row_format.lower() == fmt.lower()

        matching = [f for f in files if _matches(f)]
        if not matching:
            return _error_response(
                action=action,
                status_code=404,
                error="No compatible file found",
                book_id=book_id,
            )

        target = matching[0]
        download_path = normalize_optional_text(target.get("download_path"))
        if not download_path or not Path(download_path).exists():
            return _error_response(
                action=action,
                status_code=404,
                error="File not found on disk",
                book_id=book_id,
            )
        return send_file(download_path, download_name=Path(download_path).name, as_attachment=True)

    @app.route("/api/library/books/<int:book_id>/send-to-kindle", methods=["POST"])
    def api_library_send_to_kindle(book_id: int) -> Response | LibraryRouteResponse:
        action = "send_to_kindle"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        membership_error = _membership_or_403(actor, book_id, action)
        if membership_error is not None:
            return membership_error

        # Fail-fast ordering per #04 sub-decision 16.
        data = request.get_json(silent=True) or {}
        requested_format = (
            normalize_optional_text(data.get("format")) if isinstance(data, dict) else None
        )

        try:
            resolved = library_service.resolve_kindle_format(
                book_id=book_id, requested_format=requested_format
            )
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        if resolved is None:
            return _error_response(
                action=action,
                status_code=404,
                error="No compatible file found",
                book_id=book_id,
            )

        history_id = int(resolved["history_id"])
        # Per sub-decision 16: file must be linked to the user via user_downloads.
        # Sub-decision 7 gates on library membership, but Send-to-Kindle is stricter
        # — only files the user has linked are sendable. The library membership gate
        # above enforces "book is in the user's library"; this enforces "the user
        # owns this release".
        if not actor.is_admin and not library_service.download_linked_to_user(
            user_id=actor.db_user_id, history_id=history_id
        ):
            return _error_response(
                action=action,
                status_code=404,
                error="No compatible file found",
                book_id=book_id,
            )

        kindle_email = normalize_optional_text(
            user_db.get_personal_preferences(actor.db_user_id).get("kindle_address")
        )
        if not kindle_email:
            return _error_response(
                action=action,
                status_code=400,
                error="No email recipient configured",
                book_id=book_id,
            )

        download_path = normalize_optional_text(resolved.get("download_path"))
        if not download_path or not Path(download_path).exists():
            return _error_response(
                action=action,
                status_code=404,
                error="File not found on disk",
                book_id=book_id,
            )

        from shelfmark.download.outputs.email import (
            EmailOutputError,
            send_file_to_email,
        )

        try:
            masked_recipient = send_file_to_email(
                Path(download_path),
                kindle_email,
                label=kindle_email,
                subject=Path(download_path).name,
            )
        except EmailOutputError as exc:
            logger.warning("Send-to-Kindle SMTP failure book=%s: %s", book_id, exc)
            return jsonify({"error": str(exc)}), 500
        except _OPERATIONAL_ERRORS as exc:
            logger.exception("Send-to-Kindle unexpected error book=%s", book_id)
            return jsonify({"error": str(exc)}), 500

        return jsonify(
            {
                "status": "sent",
                "recipient": masked_recipient,
                "format": resolved.get("format"),
            }
        )

    @app.route("/api/library/books/<int:book_id>/downloads/<int:history_id>", methods=["POST"])
    def api_library_link_download(book_id: int, history_id: int) -> Response | LibraryRouteResponse:
        action = "link_download"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        membership_error = _membership_or_403(actor, book_id, action)
        if membership_error is not None:
            return membership_error

        try:
            row = library_service.get_download_history_row(history_id)
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        if row is None or normalize_positive_int(row.get("book_id")) != book_id:
            return _error_response(
                action=action,
                status_code=404,
                error="Download not found for this book",
                book_id=book_id,
            )
        try:
            library_service.link_download_to_user(
                user_id=actor.db_user_id,
                book_id=book_id,
                history_id=history_id,
            )
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify({"status": "linked"})

    @app.route("/api/library/books/<int:book_id>/downloads/<int:history_id>", methods=["DELETE"])
    def api_library_unlink_download(
        book_id: int, history_id: int
    ) -> Response | LibraryRouteResponse:
        action = "unlink_download"
        gate = _actor_gate(action)
        if isinstance(gate, _ActorContext):
            actor = gate
        else:
            return gate

        membership_error = _membership_or_403(actor, book_id, action)
        if membership_error is not None:
            return membership_error

        try:
            row = library_service.get_download_history_row(history_id)
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        if row is None or normalize_positive_int(row.get("book_id")) != book_id:
            return _error_response(
                action=action,
                status_code=404,
                error="Download not found for this book",
                book_id=book_id,
            )

        try:
            library_service.unlink_download_from_user(
                user_id=actor.db_user_id,
                book_id=book_id,
                history_id=history_id,
            )
        except _OPERATIONAL_ERRORS as exc:
            return jsonify({"error": str(exc)}), 500
        # Idempotent: returns "unlinked" whether or not a row was actually removed.
        return jsonify({"status": "unlinked"})

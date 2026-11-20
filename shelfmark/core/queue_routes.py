"""Download queue routes: status, cancel, retry, priority, order, local file and cover."""

from __future__ import annotations

import binascii
import os
import sqlite3
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from flask import Response, jsonify, request, send_file, session

from shelfmark.core.delivered_files import open_delivered_file
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import build_filename
from shelfmark.core.request_helpers import normalize_optional_text, normalize_positive_int
from shelfmark.core.requests_service import sync_delivery_states_from_queue_status
from shelfmark.core.viewer_redaction import (
    is_public_download_id,
    match_public_download_id,
    redact_status,
    viewer_download_id,
)
from shelfmark.download import orchestrator as backend
from shelfmark.download.zip_stream import stream_zip

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO

    from flask import Flask

    from shelfmark.core.download_history_service import DownloadHistoryService
    from shelfmark.core.models import DownloadTask
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_IMPORT_OPERATIONAL_ERRORS = (ImportError, *_OPERATIONAL_ERRORS)


def _task_owned_by_actor(
    task: Any, *, actor_user_id: int | None, actor_username: str | None
) -> bool:
    """Whether a queue task belongs to the actor: by user id when both have one, else by username."""
    raw_task_user_id = getattr(task, "user_id", None)
    try:
        task_user_id = int(raw_task_user_id) if raw_task_user_id is not None else None
    except TypeError, ValueError:
        task_user_id = None

    if actor_user_id is not None and task_user_id is not None:
        return task_user_id == actor_user_id

    task_username = getattr(task, "username", None)
    if isinstance(task_username, str) and task_username.strip() and isinstance(actor_username, str):
        return task_username.strip() == actor_username.strip()

    return False


def _download_row_owned_by_actor(
    row: dict[str, Any],
    *,
    actor_user_id: int | None,
    actor_username: str | None,
) -> bool:
    """Whether a download history row belongs to the actor, matched the same way as a task."""
    owner_user_id = normalize_positive_int(row.get("user_id"))
    if actor_user_id is not None and owner_user_id is not None:
        return owner_user_id == actor_user_id

    row_username = normalize_optional_text(row.get("username"))
    if row_username is not None and isinstance(actor_username, str):
        return row_username == actor_username.strip()

    return False


def _queue_task_visible_to_actor(
    task_id: str,
    *,
    is_admin: bool,
    actor_user_id: int | None,
    actor_username: str | None,
) -> bool:
    """Whether the actor may see a live queue task: admins see all, others only their own."""
    if is_admin:
        return True

    task = backend.book_queue.get_task(task_id)
    if task is None:
        return False

    return _task_owned_by_actor(
        task,
        actor_user_id=actor_user_id,
        actor_username=actor_username,
    )


def _viewer_queue_task_id(task_id: str) -> str:
    """Return the id a non-admin sees for a live queue task."""
    task = backend.book_queue.get_task(task_id)
    return viewer_download_id(task_id, getattr(task, "request_id", None))


def _attachment_filename_options(download_name: str) -> dict[str, str]:
    """Content-Disposition filename options, RFC 2231-encoded when not plain ASCII.

    Mirrors what send_file does, for responses built from a generator.
    """
    try:
        download_name.encode("ascii")
    except UnicodeEncodeError:
        simple = unicodedata.normalize("NFKD", download_name)
        simple = simple.encode("ascii", "ignore").decode("ascii")
        quoted = quote(download_name, safe="!#$&+^`|~")
        return {"filename": simple, "filename*": f"UTF-8''{quoted}"}
    return {"filename": download_name}


def _delivered_opener(identities: dict[str, str]) -> Callable[[str], BinaryIO]:
    """Open a file of a live download only while it is still the file delivered."""

    def opener(path: str) -> BinaryIO:
        handle = open_delivered_file(path, identities.get(path))
        if handle is None:
            msg = "a file of the download is not the file that was delivered"
            raise OSError(msg)
        return handle

    return opener


def _all_delivered(file_paths: list[str], identities: dict[str, str]) -> bool:
    """Check every file of a download before anything is sent, holding one open at a time."""
    for path in file_paths:
        handle = open_delivered_file(path, identities.get(path))
        if handle is None:
            return False
        handle.close()
    return True


def _send_delivered_file(handle: BinaryIO, download_name: str) -> Response:
    """Send an already opened file, closing it if the response cannot be built."""
    try:
        result = os.fstat(handle.fileno())
        response = send_file(
            handle,
            download_name=download_name,
            as_attachment=True,
            conditional=False,
            last_modified=result.st_mtime,
        )
        # send_file cannot size an open file, so it would leave out the length (and with
        # it download progress) and answer a Range request with the whole file.
        response.content_length = result.st_size
        response.make_conditional(
            request.environ, accept_ranges=True, complete_length=result.st_size
        )
    except BaseException:
        handle.close()
        raise
    return response


def _zip_download_response(
    file_paths: list[str], identities: dict[str, str], task: DownloadTask
) -> Response:
    """Send a multi-file download (audiobook chapters, a pack) as one streamed ZIP.

    Each file is opened and checked again as the archive reaches it, so only one is
    open at a time; one that changed after the check up front ends the archive there.
    """
    download_name = build_filename(task.title or "download", task.author, task.year, "zip")
    response = Response(
        stream_zip(file_paths, _delivered_opener(identities)), mimetype="application/zip"
    )
    response.headers.set(
        "Content-Disposition", "attachment", **_attachment_filename_options(download_name)
    )
    return response


def register_queue_routes(
    app: Flask,
    login_required: Callable[..., Any],
    *,
    get_user_db: Callable[[], UserDB | None],
    get_download_history_service: Callable[[], DownloadHistoryService | None],
    resolve_status_scope: Callable[..., tuple[bool, int | None, bool]],
    emit_request_update_events: Callable[[list[dict[str, Any]]], None],
) -> None:
    """Register the queue routes.

    The history service and the user database are called per request, so a runtime patch of
    either still takes effect. ``resolve_status_scope`` and ``emit_request_update_events`` stay
    in ``main.py`` because the release routes and the request routes use them too.
    """

    def _resolve_queue_actor() -> tuple[bool, int | None, str | None, Response | None]:
        """Return ``(is_admin, db_user_id, username, error)`` for the session.

        ``error`` is a response to send back when a non-admin has no usable identity.
        """
        is_admin, db_user_id, can_access_status = resolve_status_scope()
        actor_username = session.get("user_id")
        normalized_actor_username = actor_username if isinstance(actor_username, str) else None

        if not is_admin and (not can_access_status or db_user_id is None):
            return (
                is_admin,
                db_user_id,
                normalized_actor_username,
                jsonify(
                    {"error": "User identity unavailable", "code": "user_identity_unavailable"}
                ),
            )

        return is_admin, db_user_id, normalized_actor_username, None

    def _resolve_viewer_download_id(book_id: str) -> str:
        """Map a non-admin's opaque download id back to the task it names (#1418).

        Only the caller's own request-linked downloads are searched, so an id that
        doesn't resolve comes back unchanged and fails the route's usual lookup.
        """
        download_history_service = get_download_history_service()
        if not is_public_download_id(book_id):
            return book_id
        is_admin, db_user_id, _ = resolve_status_scope()
        if is_admin or db_user_id is None:
            return book_id
        candidates = [
            task_id
            for tasks in backend.book_queue.get_status(user_id=db_user_id).values()
            for task_id, task in tasks.items()
            if getattr(task, "request_id", None) is not None
        ]
        if download_history_service is not None:
            candidates.extend(download_history_service.list_request_task_ids(user_id=db_user_id))
        return match_public_download_id(book_id, candidates) or book_id

    @app.route("/api/status", methods=["GET"])
    @login_required
    def api_status() -> Response | tuple[Response, int]:
        """Get current download queue status.

        Returns:
            flask.Response: JSON object with queue status.

        """
        user_db = get_user_db()
        try:
            is_admin, db_user_id, can_access_status = resolve_status_scope()
            if not can_access_status:
                return jsonify({})

            user_id = None if is_admin else db_user_id
            status = backend.queue_status(user_id=user_id)
            if user_db is not None:
                updated_requests = sync_delivery_states_from_queue_status(
                    user_db,
                    queue_status=status,
                    user_id=user_id,
                )
                emit_request_update_events(updated_requests)
            return jsonify(status if is_admin else redact_status(status))
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Status error: {e}")
            return jsonify({"error": "Could not read the download status"}), 500

    @app.route("/api/localdownload", methods=["GET"])
    @login_required
    def api_local_download() -> Response | tuple[Response, int]:
        """Download a completed book from local storage if available.

        Query Parameters:
            id (str): Book identifier (MD5 hash)

        Returns:
            flask.Response: The file if found, a ZIP of all of them when the download
            produced several (a live task only; history keeps just the first), otherwise
            an error response.

        """
        download_history_service = get_download_history_service()
        book_id = request.args.get("id", "")
        if not book_id:
            return jsonify({"error": "No book ID provided"}), 400
        book_id = _resolve_viewer_download_id(book_id)

        try:
            # The path, not the bytes: send_file streams it off disk, where reading it
            # first cost two resident copies of the whole book.
            file_paths, book_info = backend.get_book_paths(book_id)
            if not file_paths:
                # Fallback for dismissed/history entries where queue task may no longer exist.
                if download_history_service is not None:
                    is_admin, db_user_id, can_access_status = resolve_status_scope()
                    if can_access_status:
                        history_row = download_history_service.get_by_task_id(book_id)
                        if history_row is not None:
                            owner_user_id = history_row.get("user_id")
                            if is_admin or owner_user_id == db_user_id:
                                download_path = normalize_optional_text(
                                    history_row.get("download_path")
                                )
                                handle = open_delivered_file(
                                    download_path, history_row.get("download_identity")
                                )
                                if download_path and handle is not None:
                                    return _send_delivered_file(handle, Path(download_path).name)

                # Book data not found or not available
                return jsonify({"error": "File not found"}), 404

            is_admin, db_user_id, can_access_status = resolve_status_scope()
            if not is_admin:
                actor_username = session.get("user_id")
                if not can_access_status or not _task_owned_by_actor(
                    book_info,
                    actor_user_id=db_user_id,
                    actor_username=actor_username if isinstance(actor_username, str) else None,
                ):
                    return jsonify({"error": "File not found"}), 404

            identities = book_info.download_identities if book_info is not None else {}
            if book_info is not None and len(file_paths) > 1:
                if not _all_delivered(file_paths, identities):
                    return jsonify({"error": "File not found"}), 404
                return _zip_download_response(file_paths, identities, book_info)

            handle = open_delivered_file(file_paths[0], identities.get(file_paths[0]))
            if handle is None:
                return jsonify({"error": "File not found"}), 404
            file_name = book_info.get_filename() if book_info is not None else Path(book_id).name
            return _send_delivered_file(handle, file_name)

        except _OPERATIONAL_ERRORS as e:
            # The details are logged. The message of an OSError names the file's path on the server,
            # which a non-admin never sees anywhere else.
            logger.error_trace(f"Local download error: {e}")
            return jsonify({"error": "Could not read the file"}), 500

    @app.route("/api/covers/<cover_id>", methods=["GET"])
    @login_required
    def api_cover(cover_id: str) -> Response | tuple[Response, int]:
        """Serve a cached book cover image.

        This endpoint proxies and caches cover images from external sources.
        Images are cached to disk for faster subsequent requests.

        Path Parameters:
            cover_id (str): Cover identifier (book ID or composite key for universal mode).
                Kept for the link shape only; the cache entry is chosen by `url`.

        Query Parameters:
            url (str): Base64-encoded original image URL (required)

        Returns:
            flask.Response: Binary image data with appropriate Content-Type, or 404.

        """
        try:
            import base64

            from shelfmark.config.env import is_covers_cache_enabled
            from shelfmark.core.image_cache import cover_cache_key, get_image_cache

            # Check if caching is enabled
            if not is_covers_cache_enabled():
                return jsonify({"error": "Cover caching is disabled"}), 404

            encoded_url = request.args.get("url")
            if not encoded_url:
                return jsonify({"error": "Cover URL not provided"}), 404

            try:
                original_url = base64.urlsafe_b64decode(encoded_url).decode()
            except (binascii.Error, UnicodeDecodeError) as e:
                logger.warning("Failed to decode cover URL: %s", e)
                return jsonify({"error": "Invalid cover URL encoding"}), 400

            # The entry is keyed by the URL, never by the caller-supplied id: the id would
            # let any user cache an image of their choosing under another book's link.
            cache = get_image_cache()
            cache_key = cover_cache_key(original_url)

            cached = cache.get(cache_key)
            if cached:
                image_data, content_type = cached
                response = app.response_class(
                    response=image_data, status=200, mimetype=content_type
                )
                response.headers["Cache-Control"] = "public, max-age=86400"
                response.headers["X-Cache"] = "HIT"
                return response

            # Fetch and cache the image
            result = cache.fetch_and_cache(cache_key, original_url)
            if not result:
                return jsonify({"error": "Failed to fetch cover image"}), 404

            image_data, content_type = result
            response = app.response_class(response=image_data, status=200, mimetype=content_type)
            response.headers["Cache-Control"] = "public, max-age=86400"
            response.headers["X-Cache"] = "MISS"
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Cover fetch error: {e}")
            return jsonify({"error": "Could not fetch the cover image"}), 500
        else:
            return response

    @app.route("/api/download/<path:book_id>/cancel", methods=["DELETE"])
    @login_required
    def api_cancel_download(book_id: str) -> Response | tuple[Response, int]:
        """Cancel a download.

        Path Parameters:
            book_id (str): Book identifier to cancel

        Returns:
            flask.Response: JSON status indicating success or failure.

        """
        viewer_book_id = book_id
        book_id = _resolve_viewer_download_id(book_id)
        try:
            task = backend.book_queue.get_task(book_id)
            if task is None:
                return jsonify({"error": "Failed to cancel download or book not found"}), 404

            is_admin, db_user_id, can_access_status = resolve_status_scope()
            if not is_admin:
                if not can_access_status or db_user_id is None:
                    return jsonify(
                        {"error": "User identity unavailable", "code": "user_identity_unavailable"}
                    ), 403

                actor_username = session.get("user_id")
                normalized_actor_username = (
                    actor_username if isinstance(actor_username, str) else None
                )
                if not _task_owned_by_actor(
                    task,
                    actor_user_id=db_user_id,
                    actor_username=normalized_actor_username,
                ):
                    return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

                if getattr(task, "request_id", None) is not None:
                    return jsonify(
                        {"error": "Forbidden", "code": "requested_download_cancel_forbidden"}
                    ), 403

            success = backend.cancel_download(book_id)
            if success:
                return jsonify({"status": "cancelled", "book_id": viewer_book_id})
            return jsonify({"error": "Failed to cancel download or book not found"}), 404
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Cancel download error: {e}")
            return jsonify({"error": "Could not cancel the download"}), 500

    @app.route("/api/download/<path:book_id>/retry", methods=["POST"])
    @login_required
    def api_retry_download(book_id: str) -> Response | tuple[Response, int]:
        """Retry a failed download."""
        download_history_service = get_download_history_service()
        viewer_book_id = book_id
        book_id = _resolve_viewer_download_id(book_id)
        try:
            task = backend.book_queue.get_task(book_id)
            history_row = None
            if task is None and download_history_service is not None:
                history_row = download_history_service.get_by_task_id(book_id)
            if task is None and history_row is None:
                return jsonify({"error": "Download not found"}), 404

            is_admin, db_user_id, can_access_status = resolve_status_scope()
            actor_username = session.get("user_id")
            normalized_actor_username = actor_username if isinstance(actor_username, str) else None
            if not is_admin:
                if not can_access_status or db_user_id is None:
                    return jsonify(
                        {"error": "User identity unavailable", "code": "user_identity_unavailable"}
                    ), 403

                if task is not None:
                    if not _task_owned_by_actor(
                        task,
                        actor_user_id=db_user_id,
                        actor_username=normalized_actor_username,
                    ):
                        return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403
                elif history_row is None or not _download_row_owned_by_actor(
                    history_row,
                    actor_user_id=db_user_id,
                    actor_username=normalized_actor_username,
                ):
                    return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

            if task is not None:
                task_status = backend.book_queue.get_task_status(book_id)
                if getattr(
                    task, "request_id", None
                ) is not None and not backend.can_retry_download_task(task, task_status):
                    return jsonify(
                        {"error": "Forbidden", "code": "requested_download_retry_forbidden"}
                    ), 403
                success, error = backend.retry_download(book_id)
            else:
                if history_row is None:
                    logger.error("Download history row disappeared while retrying task %s", book_id)
                    return jsonify({"error": "Download history not found"}), 404
                request_id = normalize_positive_int(history_row.get("request_id"))
                retry_payload = history_row.get("retry_payload")
                final_status = history_row.get("final_status")
                if request_id is not None:
                    history_service = download_history_service
                    if history_service is None:
                        logger.error(
                            "Download history service unavailable while retrying task %s", book_id
                        )
                        return jsonify({"error": "Download history unavailable"}), 500
                    if not history_service.is_retry_available(history_row):
                        return jsonify(
                            {"error": "Forbidden", "code": "requested_download_retry_forbidden"}
                        ), 403
                success, error = backend.retry_persisted_download(
                    retry_payload,
                    final_status=final_status,
                )

            if success:
                return jsonify({"status": "queued", "book_id": viewer_book_id})

            if error == "Download not found":
                return jsonify({"error": error}), 404

            return jsonify({"error": error or "Download cannot be retried"}), 409
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Retry download error: {e}")
            return jsonify({"error": "Could not retry the download"}), 500

    @app.route("/api/queue/<path:book_id>/priority", methods=["PUT"])
    @login_required
    def api_set_priority(book_id: str) -> Response | tuple[Response, int]:
        """Set priority for a queued book.

        Path Parameters:
            book_id (str): Book identifier

        Request Body:
            priority (int): New priority level (lower number = higher priority)

        Returns:
            flask.Response: JSON status indicating success or failure.

        """
        try:
            data = request.get_json(silent=True)
            if not data or "priority" not in data:
                return jsonify({"error": "Priority not provided"}), 400

            priority = int(data["priority"])

            is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
            if identity_error is not None:
                return identity_error, 403

            viewer_book_id = book_id
            book_id = _resolve_viewer_download_id(book_id)
            task = backend.book_queue.get_task(book_id)
            if task is None:
                return jsonify({"error": "Failed to update priority or book not found"}), 404

            if not is_admin and not _task_owned_by_actor(
                task,
                actor_user_id=db_user_id,
                actor_username=actor_username,
            ):
                return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403

            success = backend.set_book_priority(book_id, priority)

            if success:
                return jsonify(
                    {"status": "updated", "book_id": viewer_book_id, "priority": priority}
                )
            return jsonify({"error": "Failed to update priority or book not found"}), 404
        except ValueError:
            return jsonify({"error": "Invalid priority value"}), 400
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Set priority error: {e}")
            return jsonify({"error": "Could not set the priority"}), 500

    @app.route("/api/queue/reorder", methods=["POST"])
    @login_required
    def api_reorder_queue() -> Response | tuple[Response, int]:
        """Bulk reorder queue by setting new priorities.

        Request Body:
            book_priorities (dict): Mapping of book_id to new priority

        Returns:
            flask.Response: JSON status indicating success or failure.

        """
        try:
            data = request.get_json(silent=True)
            if not data or "book_priorities" not in data:
                return jsonify({"error": "book_priorities not provided"}), 400

            book_priorities = data["book_priorities"]
            if not isinstance(book_priorities, dict):
                return jsonify({"error": "book_priorities must be a dictionary"}), 400

            # Validate all priorities are integers
            for book_id, priority in book_priorities.items():
                if not isinstance(priority, int):
                    return jsonify({"error": f"Invalid priority for book {book_id}"}), 400

            is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
            if identity_error is not None:
                return identity_error, 403

            if not is_admin:
                book_priorities = {
                    _resolve_viewer_download_id(str(book_id)): priority
                    for book_id, priority in book_priorities.items()
                }
                owned_book_priorities = {}
                for book_id in book_priorities:
                    task = backend.book_queue.get_task(str(book_id))
                    if task is None:
                        continue
                    if not _task_owned_by_actor(
                        task, actor_user_id=db_user_id, actor_username=actor_username
                    ):
                        return jsonify({"error": "Forbidden", "code": "download_not_owned"}), 403
                    owned_book_priorities[book_id] = book_priorities[book_id]
                book_priorities = owned_book_priorities

            success = backend.reorder_queue(book_priorities)

            if success:
                return jsonify({"status": "reordered", "updated_count": len(book_priorities)})
            return jsonify({"error": "Failed to reorder queue"}), 500
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Reorder queue error: {e}")
            return jsonify({"error": "Could not reorder the queue"}), 500

    @app.route("/api/queue/order", methods=["GET"])
    @login_required
    def api_queue_order() -> Response | tuple[Response, int]:
        """Get current queue order for display.

        Returns:
            flask.Response: JSON array of queued books with their order and priorities.

        """
        try:
            queue_order = backend.get_queue_order()
            is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
            if identity_error is not None:
                return identity_error, 403
            if not is_admin:
                queue_order = [
                    {**item, "id": _viewer_queue_task_id(str(item.get("id", "")))}
                    for item in queue_order
                    if _queue_task_visible_to_actor(
                        str(item.get("id", "")),
                        is_admin=False,
                        actor_user_id=db_user_id,
                        actor_username=actor_username,
                    )
                ]
            return jsonify({"queue": queue_order})
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Queue order error: {e}")
            return jsonify({"error": "Could not read the queue order"}), 500

    @app.route("/api/downloads/active", methods=["GET"])
    @login_required
    def api_active_downloads() -> Response | tuple[Response, int]:
        """Get list of currently active downloads.

        Returns:
            flask.Response: JSON array of active download book IDs.

        """
        try:
            active_downloads = backend.get_active_downloads()
            is_admin, db_user_id, actor_username, identity_error = _resolve_queue_actor()
            if identity_error is not None:
                return identity_error, 403
            if not is_admin:
                active_downloads = [
                    _viewer_queue_task_id(task_id)
                    for task_id in active_downloads
                    if _queue_task_visible_to_actor(
                        task_id,
                        is_admin=False,
                        actor_user_id=db_user_id,
                        actor_username=actor_username,
                    )
                ]
            return jsonify({"active_downloads": active_downloads})
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Active downloads error: {e}")
            return jsonify({"error": "Could not read the active downloads"}), 500

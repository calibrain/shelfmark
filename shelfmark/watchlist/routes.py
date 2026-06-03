"""Flask blueprint for Pulsarr watchlist API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import Blueprint, jsonify, request

from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from shelfmark.watchlist.db import WatchlistDB

logger = setup_logger(__name__)

watchlist_bp = Blueprint("watchlist", __name__, url_prefix="/api/watchlist")

# Populated by init_watchlist_routes()
_watchlist_db: WatchlistDB | None = None


def init_watchlist_routes(watchlist_db: WatchlistDB) -> None:
    """Bind the WatchlistDB instance used by route handlers."""
    global _watchlist_db  # noqa: PLW0603
    _watchlist_db = watchlist_db


def _get_db() -> WatchlistDB:
    if _watchlist_db is None:
        msg = "WatchlistDB not initialized"
        raise RuntimeError(msg)
    return _watchlist_db


def _get_current_user_id() -> int | None:
    """Return the authenticated user's DB ID from the Flask session.

    Mirrors the pattern used in existing Shelfmark route handlers.
    """
    from flask import session
    raw = session.get("db_user_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _error(message: str, status: int = 400) -> Any:
    return jsonify({"error": message}), status


# ------------------------------------------------------------------
# Author watch endpoints
# ------------------------------------------------------------------

@watchlist_bp.get("/authors")
def list_authors() -> Any:
    """GET /api/watchlist/authors — list watched authors for current user."""
    user_id = _get_current_user_id()
    if user_id is None:
        return _error("Not authenticated", 401)

    include_inactive = request.args.get("include_inactive", "false").lower() == "true"

    authors = _get_db().list_authors(user_id, include_inactive=include_inactive)
    return jsonify(authors)


@watchlist_bp.post("/authors")
def add_author() -> Any:
    """POST /api/watchlist/authors — add an author to the watchlist."""
    user_id = _get_current_user_id()
    if user_id is None:
        return _error("Not authenticated", 401)

    body = request.get_json(silent=True)
    if not body:
        return _error("Request body must be JSON")

    author_name = body.get("author_name", "")
    hardcover_author_id = body.get("hardcover_author_id") or None
    ol_author_key = body.get("ol_author_key") or None
    watch_content_types = body.get("watch_content_types")

    if not author_name:
        return _error("author_name is required")
    if hardcover_author_id is None and ol_author_key is None:
        return _error("At least one of hardcover_author_id or ol_author_key is required")

    if watch_content_types is not None and not isinstance(watch_content_types, list):
        return _error("watch_content_types must be an array")

    try:
        entry = _get_db().add_author(
            user_id=user_id,
            author_name=author_name,
            hardcover_author_id=hardcover_author_id,
            ol_author_key=ol_author_key,
            watch_content_types=watch_content_types,
        )
    except ValueError as e:
        return _error(str(e))

    return jsonify(entry), 201


@watchlist_bp.delete("/authors/<int:watch_id>")
def remove_author(watch_id: int) -> Any:
    """DELETE /api/watchlist/authors/<id> — remove an author from the watchlist."""
    user_id = _get_current_user_id()
    if user_id is None:
        return _error("Not authenticated", 401)

    entry = _get_db().get_author(watch_id)
    if entry is None:
        return _error("Watch entry not found", 404)
    if entry["user_id"] != user_id:
        return _error("Forbidden", 403)

    _get_db().remove_author(watch_id)
    return jsonify({"deleted": True, "id": watch_id})


@watchlist_bp.patch("/authors/<int:watch_id>")
def update_author(watch_id: int) -> Any:
    """PATCH /api/watchlist/authors/<id> — update is_active or watch_content_types."""
    user_id = _get_current_user_id()
    if user_id is None:
        return _error("Not authenticated", 401)

    entry = _get_db().get_author(watch_id)
    if entry is None:
        return _error("Watch entry not found", 404)
    if entry["user_id"] != user_id:
        return _error("Forbidden", 403)

    body = request.get_json(silent=True)
    if not body:
        return _error("Request body must be JSON")

    is_active = body.get("is_active")
    watch_content_types = body.get("watch_content_types")
    author_name = body.get("author_name")

    if is_active is not None and not isinstance(is_active, bool):
        return _error("is_active must be a boolean")
    if watch_content_types is not None and not isinstance(watch_content_types, list):
        return _error("watch_content_types must be an array")

    try:
        updated = _get_db().update_author(
            watch_id,
            is_active=is_active,
            watch_content_types=watch_content_types,
            author_name=author_name,
        )
    except ValueError as e:
        return _error(str(e))

    if updated is None:
        return _error("Watch entry not found", 404)

    return jsonify(updated)


# ------------------------------------------------------------------
# Release endpoints
# ------------------------------------------------------------------

@watchlist_bp.get("/releases")
def list_releases() -> Any:
    """GET /api/watchlist/releases — list detected releases for current user."""
    user_id = _get_current_user_id()
    if user_id is None:
        return _error("Not authenticated", 401)

    action_status = request.args.get("action_status") or None

    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        return _error("limit and offset must be integers")

    limit = min(max(limit, 1), 200)
    offset = max(offset, 0)

    try:
        releases = _get_db().list_releases(
            user_id,
            action_status=action_status,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        return _error(str(e))

    return jsonify(releases)

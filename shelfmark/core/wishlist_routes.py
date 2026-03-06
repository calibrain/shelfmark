"""User wishlist routes."""

from functools import wraps
from typing import Any, Callable

from flask import Flask, g, jsonify, request, session

from shelfmark.config.env import CWA_DB_PATH
from shelfmark.core.auth_modes import load_active_auth_mode
from shelfmark.core.logger import setup_logger
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)


def register_wishlist_routes(app: Flask, user_db: UserDB) -> None:
    """Register wishlist API endpoints."""

    def _require_authenticated_user(f: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(f)
        def decorated(*args, **kwargs):
            auth_mode = load_active_auth_mode(CWA_DB_PATH, user_db=user_db)
            g.auth_mode = auth_mode
            if auth_mode != "none" and "user_id" not in session:
                return jsonify({"error": "Authentication required"}), 401
            if "db_user_id" not in session:
                if auth_mode == "none":
                    system_user = user_db.get_or_create_noauth_system_user()
                    session["db_user_id"] = system_user["id"]
                else:
                    return jsonify({"error": "Authenticated session is missing local user context"}), 403
            return f(*args, **kwargs)
        return decorated

    def _get_user_id() -> tuple[int | None, tuple[Any, int] | None]:
        raw_user_id = session.get("db_user_id")
        try:
            return int(raw_user_id), None
        except (TypeError, ValueError):
            return None, (jsonify({"error": "Invalid user context"}), 400)

    @app.route("/api/wishlist", methods=["GET"])
    @_require_authenticated_user
    def wishlist_list():
        user_id, err = _get_user_id()
        if err:
            return err
        items = user_db.list_wishlist_items(user_id)
        return jsonify([
            {
                "book_id": item["book_id"],
                "book_data": item["book_data"],
                "added_at": item["added_at"],
            }
            for item in items
        ])

    @app.route("/api/wishlist", methods=["POST"])
    @_require_authenticated_user
    def wishlist_add():
        user_id, err = _get_user_id()
        if err:
            return err

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        book_id = payload.get("book_id")
        book_data = payload.get("book_data")

        if not book_id or not isinstance(book_id, str) or not book_id.strip():
            return jsonify({"error": "book_id must be a non-empty string"}), 400
        if not isinstance(book_data, dict):
            return jsonify({"error": "book_data must be an object"}), 400

        try:
            item = user_db.add_wishlist_item(user_id, book_id, book_data)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify({
            "book_id": item["book_id"],
            "book_data": item["book_data"],
            "added_at": item["added_at"],
        }), 201

    @app.route("/api/wishlist/<path:book_id>", methods=["DELETE"])
    @_require_authenticated_user
    def wishlist_remove(book_id: str):
        user_id, err = _get_user_id()
        if err:
            return err

        removed = user_db.remove_wishlist_item(user_id, book_id)
        if not removed:
            return jsonify({"error": "Wishlist item not found"}), 404

        return jsonify({"ok": True})

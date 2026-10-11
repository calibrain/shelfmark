"""Admin routes for the series-gap report (see ``series_gap``)."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, ParamSpec

from flask import Response, g, jsonify, request, session

from shelfmark.core import series_gap
from shelfmark.core.auth_modes import load_active_auth_mode
from shelfmark.core.library_index import LibraryUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from flask.typing import ResponseReturnValue

    from shelfmark.core.user_db import UserDB

P = ParamSpec("P")
_NO_PROVIDER = "Hardcover is not configured: set a Hardcover API token first"


def register_series_gap_routes(app: Flask, user_db: UserDB) -> None:
    """Register ``GET /api/admin/series-gaps`` and ``POST /api/admin/series-gaps/requests``."""

    def _require_admin(
        f: Callable[P, ResponseReturnValue],
    ) -> Callable[P, ResponseReturnValue]:
        """Same gate as the admin user routes: everyone in no-auth mode, else admins."""

        @wraps(f)
        def decorated(*args: P.args, **kwargs: P.kwargs) -> ResponseReturnValue:
            auth_mode = load_active_auth_mode()
            g.auth_mode = auth_mode
            if auth_mode != "none":
                if "user_id" not in session:
                    return jsonify({"error": "Authentication required"}), 401
                if not session.get("is_admin", False):
                    return jsonify({"error": "Admin access required"}), 403
            return f(*args, **kwargs)

        return decorated

    def _provider() -> object | None:
        return series_gap.hardcover_provider()

    @app.route("/api/admin/series-gaps", methods=["GET"])
    @_require_admin
    def admin_series_gaps() -> Response | tuple[Response, int]:
        """The report. ``?refresh=1`` forgets cached Hardcover answers first."""
        if request.args.get("refresh") == "1":
            series_gap.clear_cache()
        provider = _provider()
        if provider is None:
            return jsonify({"error": _NO_PROVIDER}), 503
        try:
            report = series_gap.build_report(user_db, provider)
        except LibraryUnavailableError as exc:
            return jsonify({"error": str(exc)}), 503
        return jsonify(report)

    @app.route("/api/admin/series-gaps/requests", methods=["POST"])
    @_require_admin
    def admin_series_gaps_request() -> Response | tuple[Response, int]:
        """Request chosen volumes: ``{"items": [{series_id, provider_id, content_type}]}``."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "No data provided"}), 400
        provider = _provider()
        if provider is None:
            return jsonify({"error": _NO_PROVIDER}), 503
        try:
            result = series_gap.request_volumes(user_db, provider, data.get("items"))
        except series_gap.SeriesGapError as exc:
            return jsonify({"error": str(exc)}), exc.status_code
        except LibraryUnavailableError as exc:
            return jsonify({"error": str(exc)}), 503
        return jsonify(result)

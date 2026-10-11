"""Admin endpoints for the cross-format markers: list the pairs, sync the Audiobookshelf tag."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from flask import jsonify, request

from shelfmark.core import cross_format
from shelfmark.core.library_index import LibraryUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from flask.typing import ResponseReturnValue


def register_cross_format_routes(
    app: Flask, require_admin: Callable[[Callable[..., Any]], Callable[..., Any]]
) -> None:
    @app.route("/api/admin/library/cross-format", methods=["GET"])
    @require_admin
    def admin_cross_format_pairs() -> ResponseReturnValue:
        """Every work held in both libraries, keyed from each side."""
        try:
            return jsonify(cross_format.pairs_report())
        except cross_format.CrossFormatNotConfiguredError as exc:
            return jsonify({"error": str(exc)}), 400
        except LibraryUnavailableError as exc:
            return jsonify({"error": str(exc)}), 503

    @app.route("/api/admin/library/cross-format/audiobookshelf-tags", methods=["POST"])
    @require_admin
    def admin_cross_format_audiobookshelf_tags() -> ResponseReturnValue:
        """Sync the tag. Body ``{"dry_run": false}`` writes; anything else only reports."""
        data = request.get_json(silent=True) or {}
        dry_run = data.get("dry_run", True)
        if not isinstance(dry_run, bool):
            return jsonify({"error": "dry_run must be true or false"}), 400
        try:
            summary = cross_format.sync_audiobookshelf_tags(dry_run=dry_run)
        except cross_format.CrossFormatDisabledError as exc:
            return jsonify({"error": str(exc)}), 409
        except cross_format.CrossFormatNotConfiguredError as exc:
            return jsonify({"error": str(exc)}), 400
        except LibraryUnavailableError as exc:
            return jsonify({"error": str(exc)}), 503
        summary["message"] = cross_format.describe_summary(summary)
        # Audiobookshelf refused the write: the fault is upstream, not with the caller's
        # Shelfmark credentials, so it is not reported as this endpoint's own 403.
        return jsonify(summary), 502 if summary["forbidden"] else 200

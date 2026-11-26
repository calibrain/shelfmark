"""The one place an exception's text becomes an API response (#1688).

A route that put ``str(exc)`` straight into its JSON could hand a client a file path, a
connection string or a key that a library quoted in its message. Every such response goes
through here instead, so the masking is applied in one place and a test can fail a route
that bypasses it (``tests/core/test_api_errors_guard.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import jsonify

from shelfmark.core.redaction import redact_secrets

if TYPE_CHECKING:
    from werkzeug.wrappers import Response


def error_response(message: str, status: int) -> tuple[Response, int]:
    """An error answer for a message the code wrote for the user.

    That means a validation message, a source's "unavailable" text or a queue refusal. The
    message is still masked, in case it quotes a URL with credentials in it.
    """
    return jsonify({"error": redact_secrets(message)}), status


def exception_response(
    exc: BaseException, fallback: str, status: int = 500, *, show_detail: bool = False
) -> tuple[Response, int]:
    """An error answer for an unexpected exception, whose own text the code did not write.

    The client gets ``fallback``. With ``show_detail`` it gets the exception's text instead,
    masked: for the routes only an admin can reach (settings, onboarding), whose error
    toasts show what went wrong. Log the exception before calling this.
    """
    return error_response(str(exc) if show_detail else fallback, status)

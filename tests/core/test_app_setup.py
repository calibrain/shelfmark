"""Characterization tests for the app setup that moves out of main.py: the persisted secret key,
the session cookie settings, the security headers and the werkzeug log filter.
"""

from __future__ import annotations

import importlib
import logging
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def _record(message: str, *, level: int = logging.INFO, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("werkzeug", level, __file__, 1, message, None, exc_info)


class TestSecretKey:
    def test_a_persisted_key_is_reused(self, main_module, tmp_path):
        persisted = b"k" * 64
        (tmp_path / ".flask_secret").write_bytes(persisted)

        with patch.object(main_module, "CONFIG_DIR", tmp_path):
            assert main_module._load_or_create_secret_key() == persisted

    def test_a_missing_key_is_created_and_kept_private(self, main_module, tmp_path):
        with patch.object(main_module, "CONFIG_DIR", tmp_path):
            created = main_module._load_or_create_secret_key()
            again = main_module._load_or_create_secret_key()

        path = tmp_path / ".flask_secret"
        assert len(created) == 64
        assert path.read_bytes() == created == again
        assert path.stat().st_mode & 0o777 == 0o600

    def test_a_key_that_is_too_short_is_replaced(self, main_module, tmp_path):
        path = tmp_path / ".flask_secret"
        path.write_bytes(b"short")

        with patch.object(main_module, "CONFIG_DIR", tmp_path):
            key = main_module._load_or_create_secret_key()

        assert len(key) == 64
        assert path.read_bytes() == key

    def test_a_key_of_exactly_the_minimum_length_is_kept(self, main_module, tmp_path):
        minimum = b"m" * main_module.FLASK_SECRET_KEY_MIN_BYTES
        (tmp_path / ".flask_secret").write_bytes(minimum)

        with patch.object(main_module, "CONFIG_DIR", tmp_path):
            assert main_module._load_or_create_secret_key() == minimum

    def test_an_unwritable_config_dir_still_gives_a_key(self, main_module, tmp_path):
        blocked = tmp_path / "file-not-dir"
        blocked.write_text("x")

        with patch.object(main_module, "CONFIG_DIR", blocked / "config"):
            key = main_module._load_or_create_secret_key()

        assert len(key) == 64

    def test_the_app_and_the_opaque_id_key_use_the_same_secret(self, main_module):
        from shelfmark.core import viewer_redaction

        assert len(main_module.app.config["SECRET_KEY"]) >= 32
        assert viewer_redaction._public_id_key is not None


class TestSessionCookie:
    def test_cookie_settings(self, main_module):
        config = main_module.app.config

        assert config["SESSION_COOKIE_HTTPONLY"] is True
        assert config["SESSION_COOKIE_SAMESITE"] == "Lax"
        assert config["SESSION_COOKIE_NAME"] == main_module.SESSION_COOKIE_NAME
        assert config["SESSION_COOKIE_SECURE"] == main_module.SESSION_COOKIE_SECURE
        assert config["PERMANENT_SESSION_LIFETIME"] == 604800


class TestSecurityHeaders:
    def test_every_response_carries_the_baseline_headers(self, main_module):
        response = main_module.app.test_client().get("/api/health")

        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
        assert response.headers["Cross-Origin-Embedder-Policy"] == "credentialless"
        csp = response.headers["Content-Security-Policy"]
        assert csp.startswith("default-src 'self'; script-src 'self';")
        assert "connect-src 'self' ws: wss:" in csp

    def test_a_header_already_on_the_response_is_not_overridden(self, main_module):
        from flask import Response

        response = Response("{}")
        response.headers["X-Content-Type-Options"] = "route-value"

        result = main_module.set_security_headers(response)

        assert result is response
        assert response.headers["X-Content-Type-Options"] == "route-value"
        assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"


class TestLogNoiseFilter:
    def test_status_polling_is_dropped(self, main_module):
        noise = main_module.LogNoiseFilter()

        assert noise.filter(_record('127.0.0.1 - "GET /api/status HTTP/1.1" 200 -')) is False

    def test_other_requests_are_kept(self, main_module):
        noise = main_module.LogNoiseFilter()

        assert noise.filter(_record('127.0.0.1 - "GET /api/releases HTTP/1.1" 200 -')) is True
        assert noise.filter(_record('127.0.0.1 - "POST /api/status HTTP/1.1" 200 -')) is True

    def test_the_websocket_upgrade_noise_is_dropped(self, main_module):
        noise = main_module.LogNoiseFilter()

        assert noise.filter(_record("write() before start_response")) is False
        assert noise.filter(_record("Error on request:", level=logging.ERROR)) is False

    def test_an_assertion_error_from_the_upgrade_is_dropped_but_other_errors_are_not(
        self, main_module
    ):
        noise = main_module.LogNoiseFilter()
        upgrade = AssertionError("write() before start_response")
        other = AssertionError("something else")

        dropped = _record("boom", level=logging.ERROR, exc_info=(AssertionError, upgrade, None))
        kept = _record("boom", level=logging.ERROR, exc_info=(AssertionError, other, None))
        kept_type = _record("boom", level=logging.ERROR, exc_info=(ValueError, upgrade, None))

        assert noise.filter(dropped) is False
        assert noise.filter(kept) is True
        assert noise.filter(kept_type) is True

    def test_the_filter_is_installed_on_the_werkzeug_logger(self, main_module):
        filters = logging.getLogger("werkzeug").filters

        assert any(type(f).__name__ == "LogNoiseFilter" for f in filters)

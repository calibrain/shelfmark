"""Focused auth API regression tests for lockout handling."""

from __future__ import annotations

import importlib
from datetime import datetime
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    main_module.failed_login_attempts.clear()
    try:
        yield main_module.app.test_client()
    finally:
        main_module.failed_login_attempts.clear()


class TestLoginLockoutRepair:
    def test_is_account_locked_repairs_missing_timestamp(self, main_module):
        main_module.failed_login_attempts.clear()
        main_module.failed_login_attempts["locked-user"] = {
            "count": main_module.MAX_LOGIN_ATTEMPTS
        }

        assert main_module.is_account_locked("locked-user") is True
        assert isinstance(
            main_module.failed_login_attempts["locked-user"].get("lockout_until"), datetime
        )

    def test_login_keeps_account_locked_when_timestamp_is_missing(self, main_module, client):
        main_module.failed_login_attempts["locked-user"] = {
            "count": main_module.MAX_LOGIN_ATTEMPTS
        }

        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            response = client.post(
                "/api/auth/login",
                json={"username": "locked-user", "password": "secret", "remember_me": False},
            )

        assert response.status_code == 429
        assert "Account temporarily locked" in response.get_json()["error"]
        assert isinstance(
            main_module.failed_login_attempts["locked-user"].get("lockout_until"), datetime
        )

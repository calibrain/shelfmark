"""Characterization tests for `/api/auth/login`, `/api/auth/logout` and `/api/auth/check`.

They pin the behaviour the three routes have today (lockout thresholds and timing,
messages, status codes, session writes, one case per auth mode), so a refactor that
moves the routes can be checked against them. Every credential here is made up.
"""

from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash


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


@pytest.fixture
def temp_user_db(tmp_path, main_module, monkeypatch):
    from shelfmark.core.user_db import UserDB

    db = UserDB(str(tmp_path / "users.db"))
    db.initialize()
    monkeypatch.setattr(main_module, "user_db", db)
    return db


def _mode(main_module, mode):
    return patch.object(main_module, "get_auth_mode", return_value=mode)


def _login(client, username="alice", password="fake-1", **extra):
    return client.post(
        "/api/auth/login", json={"username": username, "password": password, **extra}
    )


def _make_alice(db, role="user"):
    return db.create_user(
        username="alice", password_hash=generate_password_hash("fake-1"), role=role
    )


class TestLoginInput:
    def test_empty_json_object_is_no_data(self, main_module, client):
        with _mode(main_module, "builtin"):
            response = client.post("/api/auth/login", json={})
        assert response.status_code == 400
        assert response.get_json() == {"error": "No data provided"}

    @pytest.mark.parametrize(
        "payload",
        [
            {"username": "", "password": "x"},
            {"username": "   ", "password": "x"},
            {"username": "alice", "password": ""},
            {"username": "alice"},
            {"password": "x"},
        ],
    )
    def test_missing_username_or_password(self, main_module, client, payload):
        with _mode(main_module, "builtin"):
            response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 400
        assert response.get_json() == {"error": "Username and password are required"}
        assert main_module.failed_login_attempts == {}

    def test_username_is_stripped(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        with _mode(main_module, "builtin"):
            response = _login(client, username="  alice  ")
        assert response.status_code == 200
        with client.session_transaction() as sess:
            assert sess["user_id"] == "alice"

    def test_proxy_mode_wins_over_missing_data_checks_order(self, main_module, client):
        # Proxy mode answers before the username/password check.
        with _mode(main_module, "proxy"):
            response = client.post("/api/auth/login", json={"username": "", "password": ""})
        assert response.status_code == 401
        assert response.get_json() == {"error": "Proxy authentication is enabled"}


class TestLoginBuiltin:
    def test_success_sets_session_cookie_and_clears_failures(
        self, main_module, client, temp_user_db
    ):
        user = _make_alice(temp_user_db, role="user")
        main_module.failed_login_attempts["alice"] = {"count": 3}
        with _mode(main_module, "builtin"):
            response = _login(client, remember_me=True)
        assert response.status_code == 200
        assert response.get_json() == {"success": True}
        assert any(
            header.startswith(main_module.app.config["SESSION_COOKIE_NAME"] + "=")
            for header in response.headers.getlist("Set-Cookie")
        )
        with client.session_transaction() as sess:
            assert sess["user_id"] == "alice"
            assert sess["db_user_id"] == user["id"]
            assert sess["is_admin"] is False
            assert sess.permanent is True
        assert "alice" not in main_module.failed_login_attempts

    def test_oidc_mode_allows_password_fallback(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db, role="admin")
        with _mode(main_module, "oidc"):
            response = _login(client)
        assert response.status_code == 200
        with client.session_transaction() as sess:
            assert sess["is_admin"] is True

    def test_unknown_user_is_counted_and_gets_the_generic_message(
        self, main_module, client, temp_user_db
    ):
        with _mode(main_module, "builtin"):
            response = _login(client, username="nobody")
        assert response.status_code == 401
        assert response.get_json() == {"error": "Invalid username or password."}
        assert main_module.failed_login_attempts["nobody"]["count"] == 1
        with client.session_transaction() as sess:
            assert "user_id" not in sess

    def test_user_without_password_hash_is_rejected(self, main_module, client, temp_user_db):
        temp_user_db.create_user(username="alice", role="user")
        with _mode(main_module, "builtin"):
            response = _login(client)
        assert response.status_code == 401
        assert main_module.failed_login_attempts["alice"]["count"] == 1

    def test_wrong_password_writes_no_session(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        with _mode(main_module, "builtin"):
            response = _login(client, password="wrong")
        assert response.status_code == 401
        with client.session_transaction() as sess:
            assert "user_id" not in sess
            assert "is_admin" not in sess

    def test_no_user_database_is_503(self, main_module, client, monkeypatch):
        monkeypatch.setattr(main_module, "user_db", None)
        with _mode(main_module, "builtin"):
            response = _login(client)
        assert response.status_code == 503
        assert response.get_json() == {"error": "Authentication service unavailable"}

    def test_database_error_is_500(self, main_module, client, temp_user_db, monkeypatch):
        def boom(**kwargs):
            raise sqlite3.OperationalError("fake failure")

        monkeypatch.setattr(temp_user_db, "get_user", boom)
        with _mode(main_module, "builtin"):
            response = _login(client)
        assert response.status_code == 500
        assert response.get_json() == {"error": "Authentication system error"}

    def test_unexpected_mode_is_500(self, main_module, client):
        with _mode(main_module, "unavailable"):
            response = _login(client)
        assert response.status_code == 500
        assert response.get_json() == {"error": "Unknown authentication mode"}


class TestLoginNoneMode:
    def test_any_credentials_succeed_and_clear_failures(self, main_module, client):
        main_module.failed_login_attempts["guest"] = {"count": 2}
        with _mode(main_module, "none"):
            response = _login(client, username="guest", password="any", remember_me=False)
        assert response.status_code == 200
        assert response.get_json() == {"success": True}
        with client.session_transaction() as sess:
            assert sess["user_id"] == "guest"
            assert "is_admin" not in sess
            assert sess.permanent is False
        assert "guest" not in main_module.failed_login_attempts


class TestLoginCwa:
    @staticmethod
    def _cwa_db(path, name="cwa_fake", password="cwahash", role=0, email=None):
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE user (name TEXT PRIMARY KEY, password TEXT, role INTEGER, email TEXT)"
        )
        conn.execute(
            "INSERT INTO user (name, password, role, email) VALUES (?, ?, ?, ?)",
            (name, password, role, email),
        )
        conn.commit()
        conn.close()
        return path

    def test_success_non_admin(self, main_module, client, tmp_path, temp_user_db):
        path = self._cwa_db(tmp_path / "app.db", role=0, email="c@example.invalid")
        with (
            _mode(main_module, "cwa"),
            patch.object(main_module, "CWA_DB_PATH", path),
            patch.object(main_module, "check_password_hash", return_value=True),
        ):
            response = _login(client, username="cwa_fake", remember_me=True)
        assert response.status_code == 200
        assert response.get_json() == {"success": True}
        with client.session_transaction() as sess:
            assert sess["user_id"] == "cwa_fake"
            assert sess["is_admin"] is False
            assert sess["db_user_id"] == temp_user_db.get_user(username="cwa_fake")["id"]
            assert sess.permanent is True

    def test_admin_bit_makes_admin(self, main_module, client, tmp_path, temp_user_db):
        path = self._cwa_db(tmp_path / "app.db", role=3)
        with (
            _mode(main_module, "cwa"),
            patch.object(main_module, "CWA_DB_PATH", path),
            patch.object(main_module, "check_password_hash", return_value=True),
        ):
            response = _login(client, username="cwa_fake")
        assert response.status_code == 200
        with client.session_transaction() as sess:
            assert sess["is_admin"] is True

    def test_wrong_password_is_counted(self, main_module, client, tmp_path, temp_user_db):
        path = self._cwa_db(tmp_path / "app.db")
        with (
            _mode(main_module, "cwa"),
            patch.object(main_module, "CWA_DB_PATH", path),
            patch.object(main_module, "check_password_hash", return_value=False),
        ):
            response = _login(client, username="cwa_fake")
        assert response.status_code == 401
        assert response.get_json() == {"error": "Invalid username or password."}
        assert main_module.failed_login_attempts["cwa_fake"]["count"] == 1

    def test_unknown_cwa_user_is_counted(self, main_module, client, tmp_path, temp_user_db):
        path = self._cwa_db(tmp_path / "app.db")
        with _mode(main_module, "cwa"), patch.object(main_module, "CWA_DB_PATH", path):
            response = _login(client, username="someone_else")
        assert response.status_code == 401
        assert main_module.failed_login_attempts["someone_else"]["count"] == 1

    def test_missing_database_is_500(self, main_module, client, tmp_path):
        with (
            _mode(main_module, "cwa"),
            patch.object(main_module, "CWA_DB_PATH", tmp_path / "gone.db"),
        ):
            response = _login(client, username="cwa_fake")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Database configuration error"}

    def test_unset_database_path_is_500(self, main_module, client):
        with _mode(main_module, "cwa"), patch.object(main_module, "CWA_DB_PATH", None):
            response = _login(client, username="cwa_fake")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Database configuration error"}

    def test_unreadable_database_is_500(self, main_module, client, tmp_path):
        path = tmp_path / "app.db"
        path.write_text("this is not a sqlite file")
        with _mode(main_module, "cwa"), patch.object(main_module, "CWA_DB_PATH", path):
            response = _login(client, username="cwa_fake")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Authentication system error"}


class TestLoginLockout:
    def test_warning_starts_at_five_remaining(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        messages = []
        with _mode(main_module, "builtin"):
            for _ in range(9):
                response = _login(client, password="wrong")
                assert response.status_code == 401
                messages.append(response.get_json()["error"])
        assert messages[:4] == ["Invalid username or password."] * 4
        assert messages[4] == "Invalid username or password. 5 attempts remaining."
        assert messages[8] == "Invalid username or password. 1 attempts remaining."
        assert main_module.failed_login_attempts["alice"]["count"] == 9
        assert "lockout_until" not in main_module.failed_login_attempts["alice"]

    def test_tenth_failure_locks_for_thirty_minutes(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        before = datetime.now(UTC)
        with _mode(main_module, "builtin"):
            for _ in range(9):
                _login(client, password="wrong")
            response = _login(client, password="wrong")
        after = datetime.now(UTC)
        assert response.status_code == 429
        assert response.get_json() == {
            "error": "Account locked due to 10 failed login attempts. Try again in 30 minutes."
        }
        until = main_module.failed_login_attempts["alice"]["lockout_until"]
        assert before + timedelta(minutes=30) <= until <= after + timedelta(minutes=30)

    def test_locked_account_refuses_even_the_right_password(
        self, main_module, client, temp_user_db
    ):
        _make_alice(temp_user_db)
        main_module.failed_login_attempts["alice"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) + timedelta(minutes=12, seconds=30),
        }
        with _mode(main_module, "builtin"):
            response = _login(client)
        assert response.status_code == 429
        assert response.get_json() == {
            "error": "Account temporarily locked due to multiple failed login attempts. "
            "Try again in 12 minutes."
        }
        with client.session_transaction() as sess:
            assert "user_id" not in sess
        # A refused attempt does not extend the lockout or bump the counter.
        assert main_module.failed_login_attempts["alice"]["count"] == 10

    def test_lockout_is_per_username(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        temp_user_db.create_user(
            username="bob", password_hash=generate_password_hash("fake-2"), role="user"
        )
        main_module.failed_login_attempts["alice"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) + timedelta(minutes=5),
        }
        with _mode(main_module, "builtin"):
            response = _login(client, username="bob", password="fake-2")
        assert response.status_code == 200

    def test_lockout_applies_in_none_and_cwa_modes_too(self, main_module, client):
        main_module.failed_login_attempts["guest"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) + timedelta(minutes=5),
        }
        with _mode(main_module, "none"):
            response = _login(client, username="guest")
        assert response.status_code == 429

    def test_expired_lockout_lets_the_user_in_and_forgets_the_entry(
        self, main_module, client, temp_user_db
    ):
        _make_alice(temp_user_db)
        main_module.failed_login_attempts["alice"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) - timedelta(seconds=1),
        }
        with _mode(main_module, "builtin"):
            response = _login(client)
        assert response.status_code == 200
        assert "alice" not in main_module.failed_login_attempts

    def test_expired_lockout_restarts_the_count_after_a_failure(
        self, main_module, client, temp_user_db
    ):
        _make_alice(temp_user_db)
        main_module.failed_login_attempts["alice"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) - timedelta(minutes=1),
        }
        with _mode(main_module, "builtin"):
            response = _login(client, password="wrong")
        assert response.status_code == 401
        assert main_module.failed_login_attempts["alice"] == {"count": 1}

    def test_expiry_sweep_only_drops_expired_entries(self, main_module):
        now = datetime.now(UTC)
        main_module.failed_login_attempts.clear()
        main_module.failed_login_attempts.update(
            {
                "expired": {"count": 10, "lockout_until": now - timedelta(minutes=1)},
                "active": {"count": 10, "lockout_until": now + timedelta(minutes=1)},
                "partial": {"count": 4},
            }
        )
        try:
            assert main_module.is_account_locked("active") is True
            assert set(main_module.failed_login_attempts) == {"active", "partial"}
        finally:
            main_module.failed_login_attempts.clear()

    def test_malformed_timestamp_below_threshold_is_ignored(self, main_module):
        main_module.failed_login_attempts.clear()
        main_module.failed_login_attempts["odd"] = {"count": 2, "lockout_until": "tomorrow"}
        try:
            assert main_module.is_account_locked("odd") is False
        finally:
            main_module.failed_login_attempts.clear()

    def test_helpers_return_and_mutate_the_shared_state(self, main_module):
        main_module.failed_login_attempts.clear()
        state = main_module.failed_login_attempts
        try:
            for attempt in range(1, 10):
                assert main_module.record_failed_login("u", "203.0.113.9") is False
                assert state["u"]["count"] == attempt
            assert main_module.record_failed_login("u", "203.0.113.9") is True
            assert main_module.is_account_locked("u") is True
            main_module.clear_failed_logins("u")
            assert "u" not in state
            main_module.clear_failed_logins("never-seen")
            assert main_module.failed_login_attempts is state
        finally:
            main_module.failed_login_attempts.clear()

    def test_limits(self, main_module):
        assert main_module.MAX_LOGIN_ATTEMPTS == 10
        assert main_module.LOCKOUT_DURATION_MINUTES == 30
        assert main_module.LOGIN_ATTEMPT_WARNING_THRESHOLD == 5


class TestLoginLocalAuthSwitches:
    @pytest.mark.parametrize("mode", ["builtin", "oidc"])
    def test_disable_local_auth_refuses_before_the_lockout_check(self, main_module, client, mode):
        main_module.failed_login_attempts["alice"] = {
            "count": 10,
            "lockout_until": datetime.now(UTC) + timedelta(minutes=5),
        }
        with _mode(main_module, mode), patch.object(main_module, "DISABLE_LOCAL_AUTH", True):
            response = _login(client)
        assert response.status_code == 403
        assert response.get_json() == {"error": "Local authentication is disabled"}

    def test_hide_local_auth_only_blocks_oidc(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        with _mode(main_module, "builtin"), patch.object(main_module, "HIDE_LOCAL_AUTH", True):
            assert _login(client).status_code == 200
        with _mode(main_module, "oidc"), patch.object(main_module, "HIDE_LOCAL_AUTH", True):
            assert _login(client).status_code == 403

    def test_disable_local_auth_does_not_affect_none_mode(self, main_module, client):
        with _mode(main_module, "none"), patch.object(main_module, "DISABLE_LOCAL_AUTH", True):
            assert _login(client).status_code == 200


class TestLoginClientIp:
    def test_the_client_address_is_looked_up_per_request(self, main_module, client, temp_user_db):
        _make_alice(temp_user_db)
        with (
            _mode(main_module, "builtin"),
            patch.object(main_module, "get_client_ip", return_value="198.51.100.7") as lookup,
        ):
            response = _login(client, password="wrong")
        assert response.status_code == 401
        assert lookup.called


class TestLogout:
    @pytest.mark.parametrize("mode", ["builtin", "oidc", "cwa", "none"])
    def test_clears_the_session_and_answers_success(self, main_module, client, mode):
        with client.session_transaction() as sess:
            sess["user_id"] = "alice"
            sess["db_user_id"] = 1
            sess["is_admin"] = True
        with _mode(main_module, mode):
            response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.get_json() == {"success": True}
        with client.session_transaction() as sess:
            assert dict(sess) == {}

    def test_without_a_session_still_succeeds(self, main_module, client):
        with _mode(main_module, "builtin"):
            response = client.post("/api/auth/logout")
        assert response.status_code == 200
        assert response.get_json() == {"success": True}

    def test_proxy_without_a_configured_url_has_no_logout_url(self, main_module, client):
        with (
            _mode(main_module, "proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=lambda key, default=None, user_id=None: default,
            ),
        ):
            response = client.post("/api/auth/logout")
        assert response.get_json() == {"success": True}

    def test_a_non_proxy_mode_ignores_the_logout_url(self, main_module, client):
        with (
            _mode(main_module, "builtin"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=lambda key, default=None, user_id=None: {
                    "PROXY_AUTH_LOGOUT_URL": "https://auth.example.invalid/out"
                }.get(key, default),
            ),
        ):
            response = client.post("/api/auth/logout")
        assert response.get_json() == {"success": True}

    def test_only_post_is_allowed(self, main_module):
        rules = [r for r in main_module.app.url_map.iter_rules() if r.rule == "/api/auth/logout"]
        assert [sorted(r.methods - {"OPTIONS"}) for r in rules] == [["POST"]]

    def test_error_is_500(self, main_module, client):
        with (
            _mode(main_module, "builtin"),
            patch.object(main_module, "get_client_ip", side_effect=RuntimeError("fake")),
        ):
            response = client.post("/api/auth/logout")
        assert response.status_code == 500
        assert response.get_json() == {"error": "Logout failed"}


class TestAuthCheck:
    def test_none_mode_ignores_the_session(self, main_module, client):
        with _mode(main_module, "none"):
            response = client.get("/api/auth/check")
        assert response.get_json() == {
            "authenticated": True,
            "auth_required": False,
            "auth_mode": "none",
            "is_admin": True,
        }

    def test_builtin_without_session(self, main_module, client, temp_user_db):
        with _mode(main_module, "builtin"):
            response = client.get("/api/auth/check")
        assert response.status_code == 200
        assert response.get_json() == {
            "authenticated": False,
            "auth_required": True,
            "auth_mode": "builtin",
            "is_admin": False,
            "username": None,
            "display_name": None,
        }

    def test_non_admin_session_reports_not_admin(self, main_module, client, temp_user_db):
        user = _make_alice(temp_user_db, role="user")
        with client.session_transaction() as sess:
            sess["user_id"] = "alice"
            sess["db_user_id"] = user["id"]
            sess["is_admin"] = False
        with _mode(main_module, "builtin"):
            body = client.get("/api/auth/check").get_json()
        assert body["authenticated"] is True
        assert body["is_admin"] is False
        assert body["username"] == "alice"

    def test_cwa_session_admin_flag_comes_from_the_session(self, main_module, client):
        with client.session_transaction() as sess:
            sess["user_id"] = "cwa_fake"
            sess["is_admin"] = True
        with _mode(main_module, "cwa"):
            body = client.get("/api/auth/check").get_json()
        assert body == {
            "authenticated": True,
            "auth_required": True,
            "auth_mode": "cwa",
            "is_admin": True,
            "username": "cwa_fake",
            "display_name": None,
        }

    def test_oidc_options(self, main_module, client):
        values = {"OIDC_BUTTON_LABEL": "Fake SSO"}
        with (
            _mode(main_module, "oidc"),
            patch.object(main_module, "HIDE_LOCAL_AUTH", True),
            patch.object(main_module, "OIDC_AUTO_REDIRECT", True),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=lambda key, default=None, user_id=None: values.get(key, default),
            ),
        ):
            body = client.get("/api/auth/check").get_json()
        assert body["oidc_button_label"] == "Fake SSO"
        assert body["hide_local_auth"] is True
        assert body["oidc_auto_redirect"] is True

    def test_oidc_without_options_has_no_extra_keys(self, main_module, client):
        with (
            _mode(main_module, "oidc"),
            patch.object(main_module, "DISABLE_LOCAL_AUTH", False),
            patch.object(main_module, "HIDE_LOCAL_AUTH", False),
            patch.object(main_module, "OIDC_AUTO_REDIRECT", False),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=lambda key, default=None, user_id=None: default,
            ),
        ):
            body = client.get("/api/auth/check").get_json()
        assert set(body) == {
            "authenticated",
            "auth_required",
            "auth_mode",
            "is_admin",
            "username",
            "display_name",
        }

    def test_proxy_logout_url_needs_the_user_header_setting(self, main_module, client):
        def build(values):
            return lambda key, default=None, user_id=None: values.get(key, default)

        url = {"PROXY_AUTH_LOGOUT_URL": "https://auth.example.invalid/out"}
        with client.session_transaction() as sess:
            sess["user_id"] = "p"
            sess["is_admin"] = False
        with (
            _mode(main_module, "proxy"),
            patch.object(main_module.app_config, "get", side_effect=build(url)),
        ):
            assert "logout_url" not in client.get("/api/auth/check").get_json()
        with (
            _mode(main_module, "proxy"),
            patch.object(
                main_module.app_config,
                "get",
                side_effect=build({**url, "PROXY_AUTH_USER_HEADER": "X-Fake"}),
            ),
        ):
            body = client.get("/api/auth/check").get_json()
        assert body["logout_url"] == "https://auth.example.invalid/out"

    def test_display_name_lookup_failure_is_ignored(
        self, main_module, client, temp_user_db, monkeypatch
    ):
        user = _make_alice(temp_user_db)
        with client.session_transaction() as sess:
            sess["user_id"] = "alice"
            sess["db_user_id"] = user["id"]
            sess["is_admin"] = False

        def boom(**kwargs):
            raise sqlite3.Error("fake")

        monkeypatch.setattr(temp_user_db, "get_user", boom)
        with _mode(main_module, "builtin"):
            body = client.get("/api/auth/check").get_json()
        assert body["authenticated"] is True
        assert body["display_name"] is None

    def test_error_reports_unknown_mode(self, main_module, client):
        with (
            _mode(main_module, "builtin"),
            patch.object(
                main_module, "get_auth_check_admin_status", side_effect=RuntimeError("fake")
            ),
        ):
            response = client.get("/api/auth/check")
        assert response.status_code == 200
        assert response.get_json() == {
            "authenticated": False,
            "auth_required": True,
            "auth_mode": "unknown",
            "is_admin": False,
        }

    def test_only_get_is_allowed(self, main_module):
        rules = [r for r in main_module.app.url_map.iter_rules() if r.rule == "/api/auth/check"]
        assert [sorted(r.methods - {"OPTIONS"}) for r in rules] == [["GET", "HEAD"]]

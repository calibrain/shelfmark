"""
Tests for admin user management API routes.

Tests CRUD endpoints for managing users from the admin panel.
"""

import os
import tempfile

from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.user_db import UserDB


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def app(user_db):
    from shelfmark.core.admin_routes import register_admin_routes

    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    register_admin_routes(test_app, user_db)
    return test_app


@pytest.fixture
def admin_client(app):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin"
        sess["is_admin"] = True
    return client


@pytest.fixture
def regular_client(app):
    """Non-admin client with auth mode set to builtin (auth-required)."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user"
        sess["is_admin"] = False
    with patch("shelfmark.core.admin_routes._get_auth_mode", return_value="builtin"):
        yield client


@pytest.fixture
def no_session_client(app):
    """Client with no session at all (unauthenticated, no-auth mode)."""
    return app.test_client()


@pytest.fixture
def no_session_auth_client(app):
    """Client with no session but auth mode enabled (should be rejected)."""
    client = app.test_client()
    with patch("shelfmark.core.admin_routes._get_auth_mode", return_value="builtin"):
        yield client


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------


class TestAdminUsersListEndpoint:
    """Tests for GET /api/admin/users."""

    def test_list_users_empty(self, admin_client):
        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        assert resp.json == []

    def test_list_users_returns_all(self, admin_client, user_db):
        user_db.create_user(username="alice", email="alice@example.com")
        user_db.create_user(username="bob", email="bob@example.com")

        resp = admin_client.get("/api/admin/users")
        assert resp.status_code == 200
        assert len(resp.json) == 2
        usernames = [u["username"] for u in resp.json]
        assert "alice" in usernames
        assert "bob" in usernames

    def test_list_users_excludes_password_hash(self, admin_client, user_db):
        user_db.create_user(username="alice", password_hash="secret_hash")

        resp = admin_client.get("/api/admin/users")
        users = resp.json
        assert "password_hash" not in users[0]

    def test_list_users_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/users")
        assert resp.status_code == 403

    def test_list_users_no_session_allows_access_in_no_auth(self, no_session_client):
        """No session + no-auth mode = admin access allowed."""
        resp = no_session_client.get("/api/admin/users")
        assert resp.status_code == 200

    def test_list_users_no_session_rejected_when_auth_enabled(self, no_session_auth_client):
        """No session + auth enabled = 401."""
        resp = no_session_auth_client.get("/api/admin/users")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/admin/users
# ---------------------------------------------------------------------------


class TestAdminUserCreateEndpoint:
    """Tests for POST /api/admin/users."""

    def test_create_user(self, admin_client, user_db):
        # Seed an existing user so alice doesn't get auto-promoted to admin
        user_db.create_user(username="seed_admin", role="admin")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        assert resp.status_code == 201
        assert resp.json["username"] == "alice"
        assert resp.json["role"] == "user"
        assert "password_hash" not in resp.json

    def test_create_user_with_all_fields(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={
                "username": "alice",
                "password": "pass1234",
                "email": "alice@example.com",
                "display_name": "Alice W",
                "role": "admin",
            },
        )
        assert resp.status_code == 201
        data = resp.json
        assert data["username"] == "alice"
        assert data["email"] == "alice@example.com"
        assert data["display_name"] == "Alice W"
        assert data["role"] == "admin"

    def test_create_user_password_is_hashed(self, admin_client, user_db):
        admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        user = user_db.get_user(username="alice")
        assert user["password_hash"] is not None
        assert user["password_hash"] != "pass1234"
        assert user["password_hash"].startswith("scrypt:") or user["password_hash"].startswith("pbkdf2:")

    def test_create_user_requires_admin(self, regular_client):
        resp = regular_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        assert resp.status_code == 403

    def test_create_user_missing_username(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"password": "pass1234"},
        )
        assert resp.status_code == 400
        assert "Username" in resp.json["error"]

    def test_create_user_empty_username(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "  ", "password": "pass1234"},
        )
        assert resp.status_code == 400

    def test_create_user_missing_password(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice"},
        )
        assert resp.status_code == 400
        assert "Password" in resp.json["error"]

    def test_create_user_short_password(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "abc"},
        )
        assert resp.status_code == 400
        assert "4 characters" in resp.json["error"]

    def test_create_user_invalid_role(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234", "role": "superadmin"},
        )
        assert resp.status_code == 400
        assert "Role" in resp.json["error"]

    def test_create_user_duplicate_username(self, admin_client, user_db):
        user_db.create_user(username="alice")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234"},
        )
        assert resp.status_code == 409
        assert "already exists" in resp.json["error"]

    def test_first_user_is_always_admin(self, admin_client, user_db):
        """First user created should be promoted to admin even if role=user."""
        assert len(user_db.list_users()) == 0

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "alice", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 201
        assert resp.json["role"] == "admin"

    def test_second_user_keeps_requested_role(self, admin_client, user_db):
        """After the first user, role should be respected."""
        user_db.create_user(username="admin_user", role="admin")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "pass1234", "role": "user"},
        )
        assert resp.status_code == 201
        assert resp.json["role"] == "user"

    def test_create_user_trims_whitespace(self, admin_client):
        resp = admin_client.post(
            "/api/admin/users",
            json={
                "username": "  alice  ",
                "password": "pass1234",
                "email": "  alice@example.com  ",
                "display_name": "  Alice  ",
            },
        )
        assert resp.status_code == 201
        assert resp.json["username"] == "alice"
        assert resp.json["email"] == "alice@example.com"
        assert resp.json["display_name"] == "Alice"

    def test_create_user_default_role_is_user(self, admin_client, user_db):
        """When role is omitted and DB already has users, default to 'user'."""
        user_db.create_user(username="existing", role="admin")

        resp = admin_client.post(
            "/api/admin/users",
            json={"username": "bob", "password": "pass1234"},
        )
        assert resp.status_code == 201
        assert resp.json["role"] == "user"


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>
# ---------------------------------------------------------------------------


class TestAdminUserGetEndpoint:
    """Tests for GET /api/admin/users/<id>."""

    def test_get_user(self, admin_client, user_db):
        user = user_db.create_user(username="alice", email="alice@example.com")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 200
        assert resp.json["username"] == "alice"
        assert resp.json["email"] == "alice@example.com"

    def test_get_user_includes_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"BOOKLORE_LIBRARY_ID": 5})

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.json["settings"]["BOOKLORE_LIBRARY_ID"] == 5

    def test_get_user_empty_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.json["settings"] == {}

    def test_get_user_excludes_password_hash(self, admin_client, user_db):
        user = user_db.create_user(username="alice", password_hash="secret_hash")

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert "password_hash" not in resp.json

    def test_get_nonexistent_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999")
        assert resp.status_code == 404

    def test_get_user_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/admin/users/<id>
# ---------------------------------------------------------------------------


class TestAdminUserUpdateEndpoint:
    """Tests for PUT /api/admin/users/<id>."""

    def test_update_user_role(self, admin_client, user_db):
        user = user_db.create_user(username="alice", role="user")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        updated = user_db.get_user(user_id=user["id"])
        assert updated["role"] == "admin"

    def test_update_user_email(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"email": "alice@new.com"},
        )
        assert resp.status_code == 200
        assert resp.json["email"] == "alice@new.com"

    def test_update_user_display_name(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"display_name": "Alice Wonderland"},
        )
        assert resp.status_code == 200
        assert resp.json["display_name"] == "Alice Wonderland"

    def test_update_multiple_fields(self, admin_client, user_db):
        user = user_db.create_user(username="alice", role="user")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin", "email": "alice@admin.com", "display_name": "Admin Alice"},
        )
        assert resp.status_code == 200
        assert resp.json["role"] == "admin"
        assert resp.json["email"] == "alice@admin.com"
        assert resp.json["display_name"] == "Admin Alice"

    def test_update_user_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOKLORE_LIBRARY_ID": 3}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["BOOKLORE_LIBRARY_ID"] == 3

    def test_update_settings_merges(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOKLORE_LIBRARY_ID": "2"}},
        )
        assert resp.status_code == 200
        assert resp.json["settings"]["DESTINATION"] == "/books/alice"
        assert resp.json["settings"]["BOOKLORE_LIBRARY_ID"] == "2"

    def test_update_response_includes_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"DESTINATION": "/books/alice"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        assert "settings" in resp.json
        assert resp.json["settings"]["DESTINATION"] == "/books/alice"

    def test_update_user_settings_rejects_unknown_key(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"UNKNOWN_SETTING": "value"}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any("Unknown setting: UNKNOWN_SETTING" in msg for msg in resp.json["details"])

    def test_update_user_settings_rejects_non_overridable_key(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"BOOKS_OUTPUT_MODE": "folder"}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any("Setting not user-overridable: BOOKS_OUTPUT_MODE" in msg for msg in resp.json["details"])

    def test_update_user_settings_rejects_lowercase_key(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"destination": "/books/alice"}},
        )
        assert resp.status_code == 400
        assert resp.json["error"] == "Invalid settings payload"
        assert any("Unknown setting: destination" in msg for msg in resp.json["details"])

    def test_update_response_excludes_password_hash(self, admin_client, user_db):
        user = user_db.create_user(username="alice", password_hash="secret")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert "password_hash" not in resp.json

    def test_update_nonexistent_user(self, admin_client):
        resp = admin_client.put(
            "/api/admin/users/9999",
            json={"role": "admin"},
        )
        assert resp.status_code == 404

    def test_update_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice", role="user")
        resp = regular_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# PUT /api/admin/users/<id> — password update
# ---------------------------------------------------------------------------


class TestAdminUserPasswordUpdate:
    """Tests for password update via PUT /api/admin/users/<id>."""

    def test_update_password(self, admin_client, user_db):
        """Setting a new password should hash and store it."""
        user = user_db.create_user(username="alice", password_hash="old_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99"},
        )
        assert resp.status_code == 200

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] != "old_hash"
        assert updated["password_hash"].startswith("scrypt:") or updated["password_hash"].startswith("pbkdf2:")

    def test_update_password_too_short(self, admin_client, user_db):
        """Password shorter than 4 characters should be rejected."""
        user = user_db.create_user(username="alice", password_hash="old_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "ab"},
        )
        assert resp.status_code == 400
        assert "4 characters" in resp.json["error"]

    def test_update_password_empty_string_ignored(self, admin_client, user_db):
        """Empty password string should not change existing hash."""
        user = user_db.create_user(username="alice", password_hash="original_hash")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": ""},
        )
        assert resp.status_code == 200

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] == "original_hash"

    def test_update_password_with_other_fields(self, admin_client, user_db):
        """Password update should work alongside other field updates."""
        user = user_db.create_user(username="alice", role="user", password_hash="old")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99", "role": "admin"},
        )
        assert resp.status_code == 200
        assert resp.json["role"] == "admin"

        updated = user_db.get_user(user_id=user["id"])
        assert updated["password_hash"] != "old"

    def test_update_password_hash_not_in_response(self, admin_client, user_db):
        """Response should never contain password_hash."""
        user = user_db.create_user(username="alice", password_hash="old")

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"password": "newpass99"},
        )
        assert resp.status_code == 200
        assert "password_hash" not in resp.json
        assert "password" not in resp.json


# ---------------------------------------------------------------------------
# GET /api/admin/download-defaults
# ---------------------------------------------------------------------------


class TestAdminDownloadDefaults:
    """Tests for GET /api/admin/download-defaults."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        """Create a temporary downloads config file."""
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        config = {
            "BOOKS_OUTPUT_MODE": "folder",
            "DESTINATION": "/books",
            "BOOKLORE_LIBRARY_ID": "2",
            "BOOKLORE_PATH_ID": "5",
            "EMAIL_RECIPIENTS": [{"nickname": "kindle", "email": "me@kindle.com"}],
        }
        (plugins_dir / "downloads.json").write_text(json.dumps(config))

    def test_returns_download_defaults(self, admin_client):
        resp = admin_client.get("/api/admin/download-defaults")
        assert resp.status_code == 200
        data = resp.json
        assert data["BOOKS_OUTPUT_MODE"] == "folder"
        assert data["DESTINATION"] == "/books"
        assert data["BOOKLORE_LIBRARY_ID"] == "2"
        assert data["BOOKLORE_PATH_ID"] == "5"
        assert data["EMAIL_RECIPIENTS"] == [{"nickname": "kindle", "email": "me@kindle.com"}]

    def test_returns_defaults_when_no_config(self, admin_client, tmp_path):
        """If no downloads config file exists, return sensible defaults."""

        config_path = tmp_path / "plugins" / "downloads.json"
        if config_path.exists():
            os.remove(config_path)

        resp = admin_client.get("/api/admin/download-defaults")
        assert resp.status_code == 200
        data = resp.json
        assert "BOOKS_OUTPUT_MODE" in data
        assert "DESTINATION" in data

    def test_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/download-defaults")
        assert resp.status_code == 403


class TestAdminBookloreOptions:
    """Tests for GET /api/admin/booklore-options."""

    def test_returns_library_and_path_options(self, admin_client, monkeypatch):
        mock_libraries = [{"value": "1", "label": "My Library"}]
        mock_paths = [{"value": "10", "label": "My Library: /books", "childOf": "1"}]
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_library_options",
            lambda: mock_libraries,
        )
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_path_options",
            lambda: mock_paths,
        )
        resp = admin_client.get("/api/admin/booklore-options")
        assert resp.status_code == 200
        data = resp.json
        assert data["libraries"] == mock_libraries
        assert data["paths"] == mock_paths

    def test_returns_empty_when_not_configured(self, admin_client, monkeypatch):
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_library_options",
            lambda: [],
        )
        monkeypatch.setattr(
            "shelfmark.core.admin_routes.get_booklore_path_options",
            lambda: [],
        )
        resp = admin_client.get("/api/admin/booklore-options")
        assert resp.status_code == 200
        data = resp.json
        assert data["libraries"] == []
        assert data["paths"] == []

    def test_requires_admin(self, regular_client):
        resp = regular_client.get("/api/admin/booklore-options")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/admin/users/<id>/effective-settings
# ---------------------------------------------------------------------------


class TestAdminEffectiveSettings:
    """Tests for GET /api/admin/users/<id>/effective-settings."""

    @pytest.fixture(autouse=True)
    def setup_config(self, tmp_path, monkeypatch):
        import json
        from pathlib import Path

        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        monkeypatch.setattr("shelfmark.config.env.CONFIG_DIR", Path(config_dir))

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        downloads_config = {
            "BOOKS_OUTPUT_MODE": "booklore",
            "BOOKLORE_LIBRARY_ID": "7",
        }
        (plugins_dir / "downloads.json").write_text(json.dumps(downloads_config))

        monkeypatch.setenv("INGEST_DIR", "/env/books")

        # Ensure config singleton sees the current test env/config dir.
        from shelfmark.core.config import config as app_config
        app_config.refresh()

    def test_returns_effective_values_with_sources(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(
            user["id"],
            {"EMAIL_RECIPIENTS": [{"nickname": "kindle", "email": "alice@kindle.com"}]},
        )

        resp = admin_client.get(f"/api/admin/users/{user['id']}/effective-settings")
        assert resp.status_code == 200

        data = resp.json
        assert data["DESTINATION"]["value"] == "/env/books"
        assert data["DESTINATION"]["source"] == "env_var"

        assert data["BOOKLORE_LIBRARY_ID"]["value"] == "7"
        assert data["BOOKLORE_LIBRARY_ID"]["source"] == "global_config"

        assert data["BOOKLORE_PATH_ID"]["value"] in ("", None)
        assert data["BOOKLORE_PATH_ID"]["source"] == "default"

        assert data["EMAIL_RECIPIENTS"]["value"] == [{"nickname": "kindle", "email": "alice@kindle.com"}]
        assert data["EMAIL_RECIPIENTS"]["source"] == "user_override"

    def test_returns_404_for_unknown_user(self, admin_client):
        resp = admin_client.get("/api/admin/users/9999/effective-settings")
        assert resp.status_code == 404

    def test_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.get(f"/api/admin/users/{user['id']}/effective-settings")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/admin/users/<id>
# ---------------------------------------------------------------------------


class TestAdminUserDeleteEndpoint:
    """Tests for DELETE /api/admin/users/<id>."""

    def test_delete_user(self, admin_client, user_db):
        user = user_db.create_user(username="alice")

        resp = admin_client.delete(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 200
        assert resp.json["success"] is True
        assert user_db.get_user(user_id=user["id"]) is None

    def test_delete_nonexistent_user(self, admin_client):
        resp = admin_client.delete("/api/admin/users/9999")
        assert resp.status_code == 404

    def test_delete_requires_admin(self, regular_client, user_db):
        user = user_db.create_user(username="alice")
        resp = regular_client.delete(f"/api/admin/users/{user['id']}")
        assert resp.status_code == 403

    def test_delete_user_removes_from_list(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.create_user(username="bob")

        admin_client.delete(f"/api/admin/users/{user['id']}")

        resp = admin_client.get("/api/admin/users")
        assert len(resp.json) == 1
        assert resp.json[0]["username"] == "bob"


# ---------------------------------------------------------------------------
# OIDC lockout prevention (security on_save handler)
# ---------------------------------------------------------------------------


class TestOIDCLockoutPrevention:
    """Tests for _on_save_security blocking OIDC without a local admin."""

    @pytest.fixture(autouse=True)
    def setup_config_dir(self, db_path, tmp_path, monkeypatch):
        """Point CONFIG_DIR to a temp dir so _on_save_security can find users.db."""
        config_dir = str(tmp_path)
        monkeypatch.setenv("CONFIG_DIR", config_dir)
        # Create user_db at the path _on_save_security will look for
        self._user_db = UserDB(os.path.join(config_dir, "users.db"))
        self._user_db.initialize()

    def _call_on_save(self, values):
        from shelfmark.config.security import _on_save_security
        return _on_save_security(values)

    def test_oidc_blocked_without_local_admin(self):
        """OIDC should be blocked when no local password admin exists."""
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is True
        assert "local admin" in result["message"].lower()

    def test_oidc_blocked_with_oidc_only_admin(self):
        """OIDC admin without password should not count as local admin."""
        self._user_db.create_user(
            username="sso_admin",
            oidc_subject="sub123",
            role="admin",
        )
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is True

    def test_oidc_blocked_with_local_non_admin(self):
        """A local password user who is not admin should not unblock OIDC."""
        self._user_db.create_user(
            username="regular",
            password_hash="hashed_pw",
            role="user",
        )
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is True

    def test_oidc_allowed_with_local_admin(self):
        """OIDC should be allowed when a local password admin exists."""
        self._user_db.create_user(
            username="admin_user",
            password_hash="hashed_pw",
            role="admin",
        )
        result = self._call_on_save({"AUTH_METHOD": "oidc"})
        assert result["error"] is False

    def test_non_oidc_methods_not_blocked(self):
        """Other auth methods should not trigger the OIDC check."""
        for method in ("none", "builtin", "proxy", "cwa"):
            result = self._call_on_save({"AUTH_METHOD": method})
            assert result["error"] is False, f"AUTH_METHOD={method} should not be blocked"

    def test_oidc_check_preserves_values(self):
        """When OIDC is blocked, the original values should be returned."""
        values = {"AUTH_METHOD": "oidc", "OIDC_CLIENT_ID": "myapp"}
        result = self._call_on_save(values)
        assert result["values"]["OIDC_CLIENT_ID"] == "myapp"

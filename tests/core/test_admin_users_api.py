"""
Tests for admin user management API routes.

Tests CRUD endpoints for managing users from the admin panel.
"""

import os
import tempfile

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
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user"
        sess["is_admin"] = False
    return client


@pytest.fixture
def no_session_client(app):
    """Client with no session at all (unauthenticated)."""
    return app.test_client()


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

    def test_list_users_no_session(self, no_session_client):
        resp = no_session_client.get("/api/admin/users")
        assert resp.status_code == 403


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
        user_db.set_user_settings(user["id"], {"booklore_library_id": 5})

        resp = admin_client.get(f"/api/admin/users/{user['id']}")
        assert resp.json["settings"]["booklore_library_id"] == 5

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
            json={"settings": {"booklore_library_id": 3}},
        )
        assert resp.status_code == 200
        settings = user_db.get_user_settings(user["id"])
        assert settings["booklore_library_id"] == 3

    def test_update_settings_merges(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"existing_key": "keep"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"settings": {"new_key": "added"}},
        )
        assert resp.status_code == 200
        assert resp.json["settings"]["existing_key"] == "keep"
        assert resp.json["settings"]["new_key"] == "added"

    def test_update_response_includes_settings(self, admin_client, user_db):
        user = user_db.create_user(username="alice")
        user_db.set_user_settings(user["id"], {"theme": "dark"})

        resp = admin_client.put(
            f"/api/admin/users/{user['id']}",
            json={"role": "admin"},
        )
        assert resp.status_code == 200
        assert "settings" in resp.json
        assert resp.json["settings"]["theme"] == "dark"

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

"""Tests for multi-user builtin authentication via users table."""

import os
import tempfile

import pytest
from werkzeug.security import generate_password_hash

from shelfmark.core.user_db import UserDB


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        yield user_db


class TestBuiltinMultiUserLogin:
    """Builtin auth should support multiple users via users table."""

    def test_create_builtin_user_with_password(self, db):
        password_hash = generate_password_hash("secret123")
        user = db.create_user(
            username="alice",
            password_hash=password_hash,
            role="user",
        )
        assert user["username"] == "alice"
        assert user["role"] == "user"

    def test_create_admin_and_regular_user(self, db):
        db.create_user(username="admin", password_hash=generate_password_hash("admin123"), role="admin")
        db.create_user(username="user1", password_hash=generate_password_hash("user123"), role="user")
        users = db.list_users()
        assert len(users) == 2
        roles = {u["username"]: u["role"] for u in users}
        assert roles["admin"] == "admin"
        assert roles["user1"] == "user"

    def test_authenticate_builtin_user(self, db):
        from werkzeug.security import check_password_hash

        password = "mypassword"
        password_hash = generate_password_hash(password)
        db.create_user(username="bob", password_hash=password_hash, role="user")
        user = db.get_user(username="bob")
        assert user is not None
        assert check_password_hash(user["password_hash"], password)

    def test_authenticate_wrong_password(self, db):
        from werkzeug.security import check_password_hash

        password_hash = generate_password_hash("correct")
        db.create_user(username="carol", password_hash=password_hash, role="user")
        user = db.get_user(username="carol")
        assert not check_password_hash(user["password_hash"], "wrong")

    def test_user_not_found(self, db):
        user = db.get_user(username="nonexistent")
        assert user is None


class TestMigrateBuiltinConfig:
    """When migrating from single-user config to multi-user DB,
    the existing admin credentials should be auto-imported."""

    def test_migrate_existing_admin(self, db):
        """Simulate migrating BUILTIN_USERNAME/BUILTIN_PASSWORD_HASH to users table."""
        existing_username = "myadmin"
        existing_hash = generate_password_hash("oldpassword")

        # No users yet
        assert len(db.list_users()) == 0

        # Migration: create admin user from config values
        user = db.create_user(
            username=existing_username,
            password_hash=existing_hash,
            role="admin",
        )
        assert user["username"] == "myadmin"
        assert user["role"] == "admin"
        assert user["password_hash"] == existing_hash

    def test_skip_migration_if_users_exist(self, db):
        """Don't re-migrate if users already exist in DB."""
        db.create_user(username="existing_admin", password_hash=generate_password_hash("pw"), role="admin")
        # Should have 1 user already, migration should be skipped
        assert len(db.list_users()) == 1


class TestBuiltinLoginLogic:
    """Test the login logic that mirrors what main.py will do for builtin multi-user."""

    def _builtin_login(self, db, username, password):
        """Mirror the multi-user builtin login logic."""
        from werkzeug.security import check_password_hash

        user = db.get_user(username=username)
        if not user:
            return None
        if not user.get("password_hash"):
            return None
        if not check_password_hash(user["password_hash"], password):
            return None
        return {
            "user_id": username,
            "db_user_id": user["id"],
            "is_admin": user["role"] == "admin",
        }

    def test_login_admin(self, db):
        db.create_user(username="admin", password_hash=generate_password_hash("admin123"), role="admin")
        result = self._builtin_login(db, "admin", "admin123")
        assert result is not None
        assert result["is_admin"] is True
        assert result["user_id"] == "admin"

    def test_login_regular_user(self, db):
        db.create_user(username="user1", password_hash=generate_password_hash("pass1"), role="user")
        result = self._builtin_login(db, "user1", "pass1")
        assert result is not None
        assert result["is_admin"] is False

    def test_login_wrong_password(self, db):
        db.create_user(username="user1", password_hash=generate_password_hash("correct"), role="user")
        result = self._builtin_login(db, "user1", "wrong")
        assert result is None

    def test_login_nonexistent_user(self, db):
        result = self._builtin_login(db, "nobody", "pass")
        assert result is None

    def test_login_sets_db_user_id(self, db):
        user = db.create_user(username="dave", password_hash=generate_password_hash("pw"), role="user")
        result = self._builtin_login(db, "dave", "pw")
        assert result["db_user_id"] == user["id"]

"""Tests for syncing builtin-admin credentials into the users DB."""

from __future__ import annotations

import os
import tempfile

from shelfmark.core.user_db import UserDB, sync_builtin_admin_user


def test_sync_builtin_admin_user_does_not_overwrite_external_user_with_same_username():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        existing = user_db.create_user(
            username="admin",
            auth_source="oidc",
            oidc_subject="oidc-subject",
            role="user",
        )

        sync_builtin_admin_user("admin", "builtin-hash", db_path=db_path)

        refreshed = user_db.get_user(user_id=existing["id"])
        assert refreshed is not None
        assert refreshed["auth_source"] == "oidc"
        assert refreshed["role"] == "user"
        assert refreshed["password_hash"] is None

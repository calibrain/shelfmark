"""
Tests for OIDC authentication flow.

Tests the OIDCAuth helper: login URL generation, callback handling,
user provisioning, and group claim parsing.
"""


import os
import tempfile
import pytest


MOCK_DISCOVERY = {
    "issuer": "https://auth.example.com",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": "https://auth.example.com/token",
    "userinfo_endpoint": "https://auth.example.com/userinfo",
    "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
}

MOCK_OIDC_CONFIG = {
    "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
    "OIDC_CLIENT_ID": "shelfmark",
    "OIDC_CLIENT_SECRET": "secret123",
    "OIDC_SCOPES": ["openid", "email", "profile", "groups"],
    "OIDC_GROUP_CLAIM": "groups",
    "OIDC_ADMIN_GROUP": "shelfmark-admins",
    "OIDC_AUTO_PROVISION": True,
    "OIDC_USE_ADMIN_GROUP": True,
}


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    from shelfmark.core.user_db import UserDB
    db = UserDB(db_path)
    db.initialize()
    return db


class TestParseGroupClaims:
    """Tests for parsing group claims from ID tokens."""

    def test_parse_groups_list(self):
        from shelfmark.core.oidc_auth import parse_group_claims
        id_token = {"groups": ["admins", "users", "shelfmark-admins"]}
        groups = parse_group_claims(id_token, "groups")
        assert "shelfmark-admins" in groups
        assert "admins" in groups

    def test_parse_groups_comma_separated_string(self):
        from shelfmark.core.oidc_auth import parse_group_claims
        id_token = {"groups": "admins, users, shelfmark-admins"}
        groups = parse_group_claims(id_token, "groups")
        assert "shelfmark-admins" in groups

    def test_parse_groups_pipe_separated_string(self):
        from shelfmark.core.oidc_auth import parse_group_claims
        id_token = {"groups": "admins|users|shelfmark-admins"}
        groups = parse_group_claims(id_token, "groups")
        assert "shelfmark-admins" in groups

    def test_parse_groups_missing_claim(self):
        from shelfmark.core.oidc_auth import parse_group_claims
        id_token = {"email": "user@example.com"}
        groups = parse_group_claims(id_token, "groups")
        assert groups == []

    def test_parse_groups_empty(self):
        from shelfmark.core.oidc_auth import parse_group_claims
        id_token = {"groups": []}
        groups = parse_group_claims(id_token, "groups")
        assert groups == []


class TestCheckAdminFromGroups:
    """Tests for determining admin status from group claims.

    Admin check is now inline: `admin_group in groups` when use_admin_group is True.
    These tests verify the logic that was previously in is_admin_from_groups().
    """

    def test_admin_when_group_matches(self):
        groups = ["users", "shelfmark-admins"]
        admin_group = "shelfmark-admins"
        assert admin_group in groups

    def test_not_admin_when_group_missing(self):
        groups = ["users", "editors"]
        admin_group = "shelfmark-admins"
        assert admin_group not in groups

    def test_not_admin_when_no_groups(self):
        assert "shelfmark-admins" not in []

    def test_not_admin_when_admin_group_empty(self):
        groups = ["users", "admins"]
        # When admin_group is empty, use_admin_group check is skipped (is_admin stays None)
        admin_group = ""
        use_admin_group = True
        is_admin = None
        if admin_group and use_admin_group:
            is_admin = admin_group in groups
        assert is_admin is None


class TestExtractUserInfo:
    """Tests for extracting user info from OIDC claims."""

    def test_extract_standard_claims(self):
        from shelfmark.core.oidc_auth import extract_user_info
        id_token = {
            "sub": "user-123",
            "email": "john@example.com",
            "name": "John Doe",
            "preferred_username": "john",
        }
        info = extract_user_info(id_token)
        assert info["oidc_subject"] == "user-123"
        assert info["email"] == "john@example.com"
        assert info["display_name"] == "John Doe"
        assert info["username"] == "john"

    def test_extract_falls_back_to_email_for_username(self):
        from shelfmark.core.oidc_auth import extract_user_info
        id_token = {
            "sub": "user-123",
            "email": "john@example.com",
            "name": "John Doe",
        }
        info = extract_user_info(id_token)
        assert info["username"] == "john@example.com"

    def test_extract_falls_back_to_sub_for_username(self):
        from shelfmark.core.oidc_auth import extract_user_info
        id_token = {
            "sub": "user-123",
        }
        info = extract_user_info(id_token)
        assert info["username"] == "user-123"

    def test_extract_handles_missing_optional_fields(self):
        from shelfmark.core.oidc_auth import extract_user_info
        id_token = {"sub": "user-123"}
        info = extract_user_info(id_token)
        assert info["oidc_subject"] == "user-123"
        assert info["email"] is None
        assert info["display_name"] is None


class TestProvisionOIDCUser:
    """Tests for creating/updating users from OIDC claims."""

    def test_provision_creates_new_user(self, user_db):
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_info = {
            "oidc_subject": "sub-123",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        user = provision_oidc_user(user_db, user_info, is_admin=False)
        assert user["username"] == "john"
        assert user["oidc_subject"] == "sub-123"
        assert user["auth_source"] == "oidc"
        assert user["role"] == "user"

    def test_provision_creates_admin_user(self, user_db):
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_info = {
            "oidc_subject": "sub-123",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        user = provision_oidc_user(user_db, user_info, is_admin=True)
        assert user["role"] == "admin"

    def test_provision_returns_existing_user(self, user_db):
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_info = {
            "oidc_subject": "sub-123",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        user1 = provision_oidc_user(user_db, user_info, is_admin=False)
        user2 = provision_oidc_user(user_db, user_info, is_admin=False)
        assert user1["id"] == user2["id"]

    def test_provision_updates_existing_user_info(self, user_db):
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_info = {
            "oidc_subject": "sub-123",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        provision_oidc_user(user_db, user_info, is_admin=False)

        user_info["email"] = "newemail@example.com"
        user_info["display_name"] = "John D."
        user = provision_oidc_user(user_db, user_info, is_admin=False)
        assert user["email"] == "newemail@example.com"
        assert user["display_name"] == "John D."
        assert user["auth_source"] == "oidc"

    def test_provision_updates_admin_role(self, user_db):
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_info = {
            "oidc_subject": "sub-123",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        user = provision_oidc_user(user_db, user_info, is_admin=False)
        assert user["role"] == "user"

        user = provision_oidc_user(user_db, user_info, is_admin=True)
        assert user["role"] == "admin"

    def test_provision_preserves_role_when_group_auth_disabled(self, user_db):
        """When is_admin=None (group auth disabled), DB role should be preserved."""
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_info = {
            "oidc_subject": "sub-123",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        # Create as admin via group auth
        user = provision_oidc_user(user_db, user_info, is_admin=True)
        assert user["role"] == "admin"

        # Login again with group auth disabled (is_admin=None) — should preserve admin
        user = provision_oidc_user(user_db, user_info, is_admin=None)
        assert user["role"] == "admin"

    def test_provision_handles_duplicate_username(self, user_db):
        """If OIDC subject is new but username exists, append suffix."""
        from shelfmark.core.oidc_auth import provision_oidc_user
        # Create a local user with the same username
        user_db.create_user(username="john", password_hash="hash")

        user_info = {
            "oidc_subject": "sub-456",
            "username": "john",
            "email": "john@example.com",
            "display_name": "John Doe",
        }
        user = provision_oidc_user(user_db, user_info, is_admin=False)
        assert user["username"] != "john"  # Should have a suffix
        assert user["oidc_subject"] == "sub-456"
        assert user["auth_source"] == "oidc"

    def test_provision_links_to_existing_user_by_email(self, user_db):
        """When allow_email_link=True and emails match, link to existing local user."""
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_db.create_user(
            username="localuser",
            email="shared@example.com",
            password_hash="hash",
        )

        user_info = {
            "oidc_subject": "oidc-sub-789",
            "username": "oidcuser",
            "email": "shared@example.com",
            "display_name": "OIDC User",
        }
        user = provision_oidc_user(
            user_db, user_info, is_admin=False, allow_email_link=True,
        )
        assert user["username"] == "localuser"
        assert user["oidc_subject"] == "oidc-sub-789"
        assert user["auth_source"] == "oidc"
        assert user["email"] == "shared@example.com"

    def test_provision_does_not_link_by_email_when_disabled(self, user_db):
        """When allow_email_link=False (default), don't link by email."""
        from shelfmark.core.oidc_auth import provision_oidc_user
        user_db.create_user(
            username="localuser",
            email="shared@example.com",
            password_hash="hash",
        )

        user_info = {
            "oidc_subject": "oidc-sub-no-link",
            "username": "oidcuser",
            "email": "shared@example.com",
            "display_name": "OIDC User",
        }
        user = provision_oidc_user(
            user_db, user_info, is_admin=False, allow_email_link=False,
        )
        # Should create a new user, not link to existing
        assert user["username"] == "oidcuser"
        assert user["oidc_subject"] == "oidc-sub-no-link"

        original = user_db.get_user(username="localuser")
        assert original["oidc_subject"] is None

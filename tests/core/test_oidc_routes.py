"""
Tests for OIDC Flask route handlers.

Tests the /api/auth/oidc/login and /api/auth/oidc/callback endpoints
using a minimal Flask test app (not the full shelfmark app).
"""

import os
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

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


MOCK_OIDC_CONFIG = {
    "AUTH_METHOD": "oidc",
    "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
    "OIDC_CLIENT_ID": "shelfmark",
    "OIDC_CLIENT_SECRET": "secret123",
    "OIDC_SCOPES": ["openid", "email", "profile", "groups"],
    "OIDC_GROUP_CLAIM": "groups",
    "OIDC_ADMIN_GROUP": "shelfmark-admins",
    "OIDC_AUTO_PROVISION": True,
    "OIDC_RESTRICT_SETTINGS_TO_ADMIN": True,
}

MOCK_DISCOVERY = {
    "issuer": "https://auth.example.com",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": "https://auth.example.com/token",
    "userinfo_endpoint": "https://auth.example.com/userinfo",
    "jwks_uri": "https://auth.example.com/.well-known/jwks.json",
}


@pytest.fixture
def app(user_db, db_path):
    """Create a minimal Flask test app with OIDC routes."""
    from shelfmark.core.oidc_routes import register_oidc_routes

    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True

    register_oidc_routes(test_app, user_db)

    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestOIDCLoginEndpoint:
    """Tests for GET /api/auth/oidc/login."""

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    def test_login_redirects_to_idp(self, mock_discovery, mock_config, client):
        resp = client.get("/api/auth/oidc/login")
        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert location.startswith("https://auth.example.com/authorize")

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    def test_login_includes_required_params(self, mock_discovery, mock_config, client):
        resp = client.get("/api/auth/oidc/login")
        location = resp.headers["Location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)

        assert params["client_id"] == ["shelfmark"]
        assert params["response_type"] == ["code"]
        assert "state" in params
        assert "code_challenge" in params
        assert params["code_challenge_method"] == ["S256"]

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    def test_login_includes_scopes(self, mock_discovery, mock_config, client):
        resp = client.get("/api/auth/oidc/login")
        location = resp.headers["Location"]
        parsed = urlparse(location)
        params = parse_qs(parsed.query)

        scope = params["scope"][0]
        assert "openid" in scope
        assert "email" in scope
        assert "profile" in scope
        assert "groups" in scope

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    def test_login_stores_state_in_session(self, mock_discovery, mock_config, client):
        with client.session_transaction() as sess:
            assert "oidc_state" not in sess

        client.get("/api/auth/oidc/login")

        with client.session_transaction() as sess:
            assert "oidc_state" in sess
            assert "oidc_code_verifier" in sess


class TestOIDCCallbackEndpoint:
    """Tests for GET /api/auth/oidc/callback."""

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    def test_callback_rejects_missing_state(self, mock_config, client):
        resp = client.get("/api/auth/oidc/callback?code=abc123")
        assert resp.status_code == 400

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    def test_callback_rejects_mismatched_state(self, mock_config, client):
        with client.session_transaction() as sess:
            sess["oidc_state"] = "correct-state"
            sess["oidc_code_verifier"] = "verifier"

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=wrong-state")
        assert resp.status_code == 400

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_creates_session(self, mock_exchange, mock_discovery, mock_config, client, user_db):
        mock_exchange.return_value = {
            "sub": "user-123",
            "email": "john@example.com",
            "name": "John Doe",
            "preferred_username": "john",
            "groups": ["users"],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302  # Redirect to frontend

        with client.session_transaction() as sess:
            assert sess["user_id"] == "john"
            assert "oidc_state" not in sess  # Cleaned up

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_sets_admin_from_groups(self, mock_exchange, mock_discovery, mock_config, client, user_db):
        mock_exchange.return_value = {
            "sub": "admin-123",
            "email": "admin@example.com",
            "preferred_username": "admin",
            "groups": ["users", "shelfmark-admins"],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        client.get("/api/auth/oidc/callback?code=abc123&state=test-state")

        with client.session_transaction() as sess:
            assert sess["is_admin"] is True

    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_provisions_user_in_db(self, mock_exchange, mock_discovery, mock_config, client, user_db):
        mock_exchange.return_value = {
            "sub": "user-789",
            "email": "new@example.com",
            "name": "New User",
            "preferred_username": "newuser",
            "groups": [],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        client.get("/api/auth/oidc/callback?code=abc123&state=test-state")

        user = user_db.get_user(oidc_subject="user-789")
        assert user is not None
        assert user["username"] == "newuser"
        assert user["email"] == "new@example.com"

    @patch("shelfmark.core.oidc_routes.load_config_file")
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_rejects_when_auto_provision_disabled(self, mock_exchange, mock_discovery, mock_config, client, user_db):
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        mock_config.return_value = config

        mock_exchange.return_value = {
            "sub": "unknown-user",
            "email": "unknown@example.com",
            "preferred_username": "unknown",
            "groups": [],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 403

    @patch("shelfmark.core.oidc_routes.load_config_file")
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_allows_pre_created_user_by_email_when_no_provision(
        self, mock_exchange, mock_discovery, mock_config, client, user_db
    ):
        """Pre-created user (by email) should log in even when auto-provision is off."""
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        mock_config.return_value = config

        # Admin pre-creates a user with this email (no oidc_subject yet)
        user_db.create_user(username="alice", email="alice@example.com", password_hash="hash")

        mock_exchange.return_value = {
            "sub": "oidc-alice-sub",
            "email": "alice@example.com",
            "preferred_username": "alice_oidc",
            "groups": [],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302  # Success, redirects to frontend

        with client.session_transaction() as sess:
            assert sess["user_id"] == "alice"
            assert sess.get("db_user_id") is not None

    @patch("shelfmark.core.oidc_routes.load_config_file")
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_links_oidc_subject_to_pre_created_user(
        self, mock_exchange, mock_discovery, mock_config, client, user_db
    ):
        """When a pre-created user logs in via OIDC, their oidc_subject should be linked."""
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        mock_config.return_value = config

        user = user_db.create_user(username="bob", email="bob@example.com", password_hash="hash")

        mock_exchange.return_value = {
            "sub": "oidc-bob-sub",
            "email": "bob@example.com",
            "preferred_username": "bob_oidc",
            "groups": [],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        client.get("/api/auth/oidc/callback?code=abc123&state=test-state")

        # The OIDC subject should now be linked to the existing user
        updated_user = user_db.get_user(user_id=user["id"])
        assert updated_user["oidc_subject"] == "oidc-bob-sub"

    @patch("shelfmark.core.oidc_routes.load_config_file")
    @patch("shelfmark.core.oidc_routes._fetch_discovery", return_value=MOCK_DISCOVERY)
    @patch("shelfmark.core.oidc_routes._exchange_code")
    def test_callback_rejects_unknown_email_when_no_provision(
        self, mock_exchange, mock_discovery, mock_config, client, user_db
    ):
        """When auto-provision is off and no user matches by email, reject login."""
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        mock_config.return_value = config

        # Pre-create a user with a different email
        user_db.create_user(username="charlie", email="charlie@example.com", password_hash="hash")

        mock_exchange.return_value = {
            "sub": "oidc-unknown-sub",
            "email": "stranger@example.com",
            "preferred_username": "stranger",
            "groups": [],
        }

        with client.session_transaction() as sess:
            sess["oidc_state"] = "test-state"
            sess["oidc_code_verifier"] = "test-verifier"

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 403

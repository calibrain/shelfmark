"""Tests for OIDC Flask route handlers using Authlib transport."""

import os
import tempfile
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from authlib.jose.errors import InvalidClaimError
from flask import Flask, redirect

from shelfmark.core.user_db import UserDB


def _get_oidc_error(resp) -> str | None:
    """Extract the oidc_error query param from a redirect response."""
    assert resp.status_code == 302
    parsed = urlparse(resp.headers["Location"])
    params = parse_qs(parsed.query)
    errors = params.get("oidc_error", [])
    return errors[0] if errors else None


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
    "OIDC_USE_ADMIN_GROUP": True,
}


@pytest.fixture
def app(user_db):
    from shelfmark.core.oidc_routes import register_oidc_routes

    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    register_oidc_routes(test_app, user_db)
    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


class TestOIDCClientRegistration:
    @patch("shelfmark.core.oidc_routes.load_config_file", return_value=MOCK_OIDC_CONFIG)
    @patch("shelfmark.core.oidc_routes.oauth.create_client")
    @patch("shelfmark.core.oidc_routes.oauth.register")
    def test_registers_client_with_pkce_and_expected_scopes(
        self, mock_register, mock_create_client, _mock_config
    ):
        from shelfmark.core.oidc_routes import _get_oidc_client

        fake_client = Mock()
        mock_create_client.return_value = fake_client

        client_obj, config = _get_oidc_client()

        assert client_obj is fake_client
        assert config["OIDC_CLIENT_ID"] == "shelfmark"
        kwargs = mock_register.call_args.kwargs
        assert kwargs["name"] == "shelfmark_idp"
        assert kwargs["server_metadata_url"] == MOCK_OIDC_CONFIG["OIDC_DISCOVERY_URL"]
        assert kwargs["overwrite"] is True
        assert kwargs["client_kwargs"]["code_challenge_method"] == "S256"
        scope_str = kwargs["client_kwargs"]["scope"]
        assert "openid" in scope_str
        assert "email" in scope_str
        assert "profile" in scope_str
        assert "groups" in scope_str

    @patch("shelfmark.core.oidc_routes.load_config_file")
    @patch("shelfmark.core.oidc_routes.oauth.create_client")
    @patch("shelfmark.core.oidc_routes.oauth.register")
    def test_does_not_append_group_claim_when_admin_group_auth_disabled(
        self, mock_register, mock_create_client, mock_config
    ):
        from shelfmark.core.oidc_routes import _get_oidc_client

        config = {
            **MOCK_OIDC_CONFIG,
            "OIDC_SCOPES": ["openid", "email", "profile"],
            "OIDC_USE_ADMIN_GROUP": False,
            "OIDC_GROUP_CLAIM": "groups",
        }
        mock_config.return_value = config
        mock_create_client.return_value = Mock()

        _get_oidc_client()

        scope_str = mock_register.call_args.kwargs["client_kwargs"]["scope"]
        assert "groups" not in scope_str


class TestOIDCLoginEndpoint:
    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_login_redirects_to_provider(self, mock_get_client, client):
        fake_client = Mock()
        fake_client.authorize_redirect.return_value = redirect("https://auth.example.com/authorize")
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/login")

        assert resp.status_code == 302
        assert resp.headers["Location"].startswith("https://auth.example.com/authorize")
        fake_client.authorize_redirect.assert_called_once()
        redirect_uri = fake_client.authorize_redirect.call_args.args[0]
        assert redirect_uri.endswith("/api/auth/oidc/callback")

    @patch("shelfmark.core.oidc_routes._get_oidc_client", side_effect=ValueError("OIDC not configured"))
    def test_login_returns_500_when_not_configured(self, _mock_get_client, client):
        resp = client.get("/api/auth/oidc/login")
        assert resp.status_code == 500
        assert resp.get_json()["error"] == "OIDC not configured"


class TestOIDCCallbackEndpoint:
    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_creates_session(self, mock_get_client, client):
        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "user-123",
                "email": "john@example.com",
                "name": "John Doe",
                "preferred_username": "john",
                "groups": ["users"],
            }
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302
        fake_client.userinfo.assert_not_called()

        with client.session_transaction() as sess:
            assert sess["user_id"] == "john"
            assert sess["db_user_id"] is not None

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_sets_admin_from_groups(self, mock_get_client, client):
        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "admin-123",
                "email": "admin@example.com",
                "preferred_username": "admin",
                "groups": ["users", "shelfmark-admins"],
            }
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        client.get("/api/auth/oidc/callback?code=abc123&state=test-state")

        with client.session_transaction() as sess:
            assert sess["is_admin"] is True

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_falls_back_to_userinfo_endpoint(self, mock_get_client, client):
        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {}
        fake_client.userinfo.return_value = {
            "sub": "fallback-123",
            "email": "fallback@example.com",
            "preferred_username": "fallback",
            "groups": [],
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_fetches_userinfo_when_token_claims_are_sparse(self, mock_get_client, client):
        fake_client = Mock()
        token = {"userinfo": {"sub": "sparse-sub"}}
        fake_client.authorize_access_token.return_value = token
        fake_client.userinfo.return_value = {
            "sub": "sparse-sub",
            "email": "sparse@example.com",
            "preferred_username": "sparse-user",
            "groups": [],
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")

        assert resp.status_code == 302
        fake_client.userinfo.assert_called_once_with(token=token)
        with client.session_transaction() as sess:
            assert sess["user_id"] == "sparse-user"

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_uses_sparse_claims_when_userinfo_fetch_fails(self, mock_get_client, client):
        fake_client = Mock()
        token = {"userinfo": {"sub": "fallback-sub"}}
        fake_client.authorize_access_token.return_value = token
        fake_client.userinfo.side_effect = RuntimeError("userinfo failed")
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")

        assert resp.status_code == 302
        fake_client.userinfo.assert_called_once_with(token=token)
        with client.session_transaction() as sess:
            assert sess["user_id"] == "fallback-sub"

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_redirects_with_error_when_claims_missing(self, mock_get_client, client):
        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {}
        fake_client.userinfo.side_effect = RuntimeError("userinfo failed")
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        error = _get_oidc_error(resp)
        assert error is not None
        assert "missing user claims" in error

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_redirects_with_issuer_guidance_on_invalid_issuer_claim(
        self, mock_get_client, client
    ):
        fake_client = Mock()
        fake_client.authorize_access_token.side_effect = InvalidClaimError("iss")
        fake_client.load_server_metadata.return_value = {"issuer": "https://auth.example.com/application/o/shelfmark/"}
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        error = _get_oidc_error(resp)
        assert error is not None
        assert "issuer validation failed" in error

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_redirects_when_auto_provision_disabled_and_no_email_match(
        self, mock_get_client, client
    ):
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "unknown-user",
                "preferred_username": "unknown",
                "groups": [],
            }
        }
        mock_get_client.return_value = (fake_client, config)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        error = _get_oidc_error(resp)
        assert error is not None
        assert "Account not found" in error

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_links_pre_created_user_by_email_when_no_provision(
        self, mock_get_client, client, user_db
    ):
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        user_db.create_user(username="alice", email="alice@example.com", password_hash="hash")

        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "oidc-alice-sub",
                "email": "alice@example.com",
                "preferred_username": "alice_oidc",
                "groups": [],
            }
        }
        mock_get_client.return_value = (fake_client, config)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302

        with client.session_transaction() as sess:
            assert sess["user_id"] == "alice"
            assert sess.get("db_user_id") is not None

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_does_not_link_when_no_email_and_no_provision(
        self, mock_get_client, client, user_db
    ):
        config = {**MOCK_OIDC_CONFIG, "OIDC_AUTO_PROVISION": False}
        user_db.create_user(username="bob", email="bob@example.com", password_hash="hash")

        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "oidc-bob-sub",
                "preferred_username": "bob_oidc",
                "groups": [],
            }
        }
        mock_get_client.return_value = (fake_client, config)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        error = _get_oidc_error(resp)
        assert error is not None
        assert "Account not found" in error

        updated_user = user_db.get_user(username="bob")
        assert updated_user["oidc_subject"] is None

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_redirects_on_idp_error(self, mock_get_client, client):
        mock_get_client.return_value = (Mock(), MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?error=access_denied")
        error = _get_oidc_error(resp)
        assert error is not None
        assert "Authentication failed" in error

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_error_redirect_honors_script_root(self, mock_get_client, client):
        mock_get_client.return_value = (Mock(), MOCK_OIDC_CONFIG)

        resp = client.get(
            "/api/auth/oidc/callback?error=access_denied",
            environ_overrides={"SCRIPT_NAME": "/shelfmark"},
        )

        assert resp.status_code == 302
        parsed = urlparse(resp.headers["Location"])
        assert parsed.path == "/shelfmark/login"
        error = _get_oidc_error(resp)
        assert error is not None
        assert "Authentication failed" in error

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_redirects_on_generic_exception(self, mock_get_client, client):
        fake_client = Mock()
        fake_client.authorize_access_token.side_effect = RuntimeError("unexpected")
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        error = _get_oidc_error(resp)
        assert error is not None
        assert "Authentication failed" in error

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_links_to_existing_user_by_email(
        self, mock_get_client, client, user_db
    ):
        """OIDC login with matching email should link to existing local user."""
        user_db.create_user(username="localuser", email="shared@example.com", password_hash="hash")

        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "oidc-new-sub",
                "email": "shared@example.com",
                "preferred_username": "oidcuser",
                "groups": [],
            }
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302

        with client.session_transaction() as sess:
            assert sess["user_id"] == "localuser"

        linked = user_db.get_user(username="localuser")
        assert linked["oidc_subject"] == "oidc-new-sub"
        assert linked["auth_source"] == "oidc"

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_creates_new_user_when_no_email_match(
        self, mock_get_client, client, user_db
    ):
        """OIDC login without matching email creates a new user."""
        user_db.create_user(username="existing", email="other@example.com", password_hash="hash")

        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "oidc-nomatch",
                "email": "different@example.com",
                "preferred_username": "newuser",
                "groups": [],
            }
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302

        with client.session_transaction() as sess:
            assert sess["user_id"] == "newuser"

        original = user_db.get_user(username="existing")
        assert original["oidc_subject"] is None

    @patch("shelfmark.core.oidc_routes._get_oidc_client")
    def test_callback_no_email_link_when_oidc_has_no_email(
        self, mock_get_client, client, user_db
    ):
        """OIDC login without email in claims should not attempt email linking."""
        user_db.create_user(username="existing", email="existing@example.com", password_hash="hash")

        fake_client = Mock()
        fake_client.authorize_access_token.return_value = {
            "userinfo": {
                "sub": "oidc-noemail",
                "preferred_username": "noemailuser",
                "groups": [],
            }
        }
        mock_get_client.return_value = (fake_client, MOCK_OIDC_CONFIG)

        resp = client.get("/api/auth/oidc/callback?code=abc123&state=test-state")
        assert resp.status_code == 302

        with client.session_transaction() as sess:
            assert sess["user_id"] == "noemailuser"

        original = user_db.get_user(username="existing")
        assert original["oidc_subject"] is None

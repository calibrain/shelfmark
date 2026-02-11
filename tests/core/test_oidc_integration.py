"""Tests for auth mode and admin policy helpers used by OIDC integration."""

from shelfmark.core.auth_modes import (
    determine_auth_mode,
    get_auth_check_admin_status,
    is_settings_or_onboarding_path,
    should_restrict_settings_to_admin,
)


class TestDetermineAuthMode:
    def test_returns_oidc_when_fully_configured(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
            "OIDC_CLIENT_ID": "shelfmark",
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "oidc"

    def test_returns_none_when_oidc_missing_client_id(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "none"

    def test_returns_none_when_oidc_missing_discovery_url(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_CLIENT_ID": "shelfmark",
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "none"

    def test_builtin_still_works(self):
        config = {
            "AUTH_METHOD": "builtin",
            "BUILTIN_USERNAME": "admin",
            "BUILTIN_PASSWORD_HASH": "hash",
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "builtin"

    def test_proxy_still_works(self):
        config = {
            "AUTH_METHOD": "proxy",
            "PROXY_AUTH_USER_HEADER": "X-Auth-User",
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "proxy"


class TestSettingsRestrictionPolicy:
    def test_settings_path_detection(self):
        assert is_settings_or_onboarding_path("/api/settings/downloads")
        assert is_settings_or_onboarding_path("/api/onboarding")
        assert not is_settings_or_onboarding_path("/api/search")

    def test_oidc_is_always_admin_restricted_for_settings(self):
        config = {}
        session_data = {"user_id": "user", "is_admin": False}
        assert should_restrict_settings_to_admin("oidc", config, session_data) is True

    def test_cwa_is_always_admin_restricted_for_settings(self):
        assert should_restrict_settings_to_admin("cwa", {}, {"user_id": "user"}) is True

    def test_proxy_respects_config_toggle(self):
        assert should_restrict_settings_to_admin(
            "proxy",
            {"PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": True},
            {"user_id": "user"},
        )
        assert not should_restrict_settings_to_admin(
            "proxy",
            {"PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": False},
            {"user_id": "user"},
        )

    def test_builtin_restricts_only_in_multi_user_session(self):
        assert should_restrict_settings_to_admin("builtin", {}, {"db_user_id": 1})
        assert not should_restrict_settings_to_admin("builtin", {}, {"user_id": "admin"})


class TestAuthCheckAdminStatus:
    def test_oidc_authenticated_admin(self):
        result = get_auth_check_admin_status("oidc", {}, {"user_id": "admin", "is_admin": True})
        assert result is True

    def test_oidc_authenticated_non_admin(self):
        result = get_auth_check_admin_status("oidc", {}, {"user_id": "user", "is_admin": False})
        assert result is False

    def test_oidc_auth_check_uses_session_admin_state_only(self):
        result = get_auth_check_admin_status(
            "oidc",
            {},
            {"user_id": "user", "is_admin": False},
        )
        assert result is False

    def test_proxy_defaults_to_non_admin_when_restricted(self):
        result = get_auth_check_admin_status(
            "proxy",
            {"PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": True},
            {"user_id": "user"},
        )
        assert result is False

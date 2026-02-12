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
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "builtin"

    def test_builtin_requires_local_admin(self):
        config = {
            "AUTH_METHOD": "builtin",
        }
        assert determine_auth_mode(config, cwa_db_path=None, has_local_admin=False) == "none"

    def test_proxy_still_works(self):
        config = {
            "AUTH_METHOD": "proxy",
            "PROXY_AUTH_USER_HEADER": "X-Auth-User",
        }
        assert determine_auth_mode(config, cwa_db_path=None) == "proxy"

    def test_oidc_requires_local_admin(self):
        config = {
            "AUTH_METHOD": "oidc",
            "OIDC_DISCOVERY_URL": "https://auth.example.com/.well-known/openid-configuration",
            "OIDC_CLIENT_ID": "shelfmark",
        }
        assert determine_auth_mode(config, cwa_db_path=None, has_local_admin=False) == "none"


class TestSettingsRestrictionPolicy:
    def test_settings_path_detection(self):
        assert is_settings_or_onboarding_path("/api/settings/downloads")
        assert is_settings_or_onboarding_path("/api/onboarding")
        assert not is_settings_or_onboarding_path("/api/search")

    def test_default_is_admin_restricted(self):
        assert should_restrict_settings_to_admin({}) is True

    def test_respects_global_users_toggle(self):
        assert should_restrict_settings_to_admin({"RESTRICT_SETTINGS_TO_ADMIN": True}) is True
        assert should_restrict_settings_to_admin({"RESTRICT_SETTINGS_TO_ADMIN": False}) is False


class TestAuthCheckAdminStatus:
    def test_authenticated_admin_when_restricted(self):
        result = get_auth_check_admin_status(
            "oidc",
            {"RESTRICT_SETTINGS_TO_ADMIN": True},
            {"user_id": "admin", "is_admin": True},
        )
        assert result is True

    def test_authenticated_non_admin_when_restricted(self):
        result = get_auth_check_admin_status(
            "oidc",
            {"RESTRICT_SETTINGS_TO_ADMIN": True},
            {"user_id": "user", "is_admin": False},
        )
        assert result is False

    def test_authenticated_user_when_not_restricted(self):
        result = get_auth_check_admin_status(
            "proxy",
            {"RESTRICT_SETTINGS_TO_ADMIN": False},
            {"user_id": "user", "is_admin": False},
        )
        assert result is True

    def test_unauthenticated_is_never_admin(self):
        result = get_auth_check_admin_status(
            "builtin",
            {"RESTRICT_SETTINGS_TO_ADMIN": False},
            {"is_admin": True},
        )
        assert result is False

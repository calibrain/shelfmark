"""Tests for auth mode and admin policy helpers used by OIDC integration."""

import pytest

from shelfmark.core.auth_modes import (
    AUTH_MODE_UNAVAILABLE,
    determine_auth_mode,
    get_auth_check_admin_status,
    get_settings_tab_from_path,
    is_settings_or_onboarding_path,
    load_active_auth_mode,
    requires_admin_for_settings_access,
    should_restrict_settings_to_admin,
)


class TestDetermineAuthMode:
    @pytest.mark.parametrize("auth_method", ["builtin", "oidc", "proxy", "cwa", "none"])
    def test_returns_configured_method(self, auth_method):
        assert determine_auth_mode(auth_method) == auth_method

    @pytest.mark.parametrize(
        ("auth_method", "expected"), [("OIDC", "oidc"), (" Builtin ", "builtin")]
    )
    def test_normalizes_case_and_whitespace(self, auth_method, expected):
        assert determine_auth_mode(auth_method) == expected

    @pytest.mark.parametrize("auth_method", [None, "", "  "])
    def test_unset_method_means_none(self, auth_method):
        assert determine_auth_mode(auth_method) == "none"

    @pytest.mark.parametrize("auth_method", ["local", "ldap", "openid", "no"])
    def test_unrecognized_method_fails_closed(self, auth_method):
        assert determine_auth_mode(auth_method) == AUTH_MODE_UNAVAILABLE

    def test_load_active_auth_mode_fails_closed_when_config_unreadable(self, monkeypatch):
        from shelfmark.core.config import config as app_config

        def _boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(app_config, "get", _boom)
        assert load_active_auth_mode() == AUTH_MODE_UNAVAILABLE

    @pytest.mark.parametrize("auth_method", ["builtin", "oidc"])
    def test_load_active_auth_mode_keeps_local_modes_without_local_admin(
        self, monkeypatch, tmp_path, auth_method
    ):
        """Regression for #1387: no local admin must not turn authentication off."""
        from shelfmark.core.config import config as app_config

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("AUTH_METHOD", auth_method)
        app_config.refresh(force=True)

        try:
            assert load_active_auth_mode() == auth_method
        finally:
            monkeypatch.delenv("AUTH_METHOD", raising=False)
            app_config.refresh(force=True)

    def test_load_active_auth_mode_keeps_cwa_without_database(self, monkeypatch, tmp_path):
        from shelfmark.core.config import config as app_config

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("AUTH_METHOD", "cwa")
        app_config.refresh(force=True)

        try:
            assert load_active_auth_mode() == "cwa"
        finally:
            monkeypatch.delenv("AUTH_METHOD", raising=False)
            app_config.refresh(force=True)

    def test_load_active_auth_mode_reads_env_backed_proxy_setting(self, monkeypatch, tmp_path):
        from shelfmark.core.config import config as app_config

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("AUTH_METHOD", "proxy")
        monkeypatch.setenv("PROXY_AUTH_USER_HEADER", "X-Forwarded-User")
        app_config.refresh(force=True)

        try:
            assert load_active_auth_mode() == "proxy"
        finally:
            monkeypatch.delenv("AUTH_METHOD", raising=False)
            monkeypatch.delenv("PROXY_AUTH_USER_HEADER", raising=False)
            app_config.refresh(force=True)


class TestSettingsRestrictionPolicy:
    def test_settings_path_detection(self):
        assert is_settings_or_onboarding_path("/api/settings/downloads")
        assert is_settings_or_onboarding_path("/api/onboarding")
        assert not is_settings_or_onboarding_path("/api/releases")

    def test_default_is_admin_restricted(self):
        assert should_restrict_settings_to_admin({}) is True

    def test_restriction_is_always_enabled(self):
        assert should_restrict_settings_to_admin({"RESTRICT_SETTINGS_TO_ADMIN": True}) is True
        assert should_restrict_settings_to_admin({"RESTRICT_SETTINGS_TO_ADMIN": False}) is True

    def test_extracts_settings_tab_from_path(self):
        assert get_settings_tab_from_path("/api/settings/security") == "security"
        assert get_settings_tab_from_path("/api/settings/users/action/open_users_tab") == "users"
        assert get_settings_tab_from_path("/api/settings") is None

    def test_security_and_users_tabs_always_require_admin(self):
        users_config = {"RESTRICT_SETTINGS_TO_ADMIN": False}
        assert requires_admin_for_settings_access("/api/settings/security", users_config) is True
        assert requires_admin_for_settings_access("/api/settings/users", users_config) is True

    def test_other_tabs_also_require_admin(self):
        assert (
            requires_admin_for_settings_access(
                "/api/settings/general",
                {"RESTRICT_SETTINGS_TO_ADMIN": False},
            )
            is True
        )
        assert (
            requires_admin_for_settings_access(
                "/api/settings/general",
                {"RESTRICT_SETTINGS_TO_ADMIN": True},
            )
            is True
        )


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

    def test_authenticated_non_admin_user_is_not_admin(self):
        result = get_auth_check_admin_status(
            "proxy",
            {"RESTRICT_SETTINGS_TO_ADMIN": False},
            {"user_id": "user", "is_admin": False},
        )
        assert result is False

    def test_unauthenticated_is_never_admin(self):
        result = get_auth_check_admin_status(
            "builtin",
            {"RESTRICT_SETTINGS_TO_ADMIN": False},
            {"is_admin": True},
        )
        assert result is False

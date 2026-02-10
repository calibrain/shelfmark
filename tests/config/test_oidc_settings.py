"""
Tests for OIDC settings fields in security configuration.

Tests that OIDC fields are registered correctly with proper
show_when conditions, defaults, and field types.
"""

from shelfmark.core.settings_registry import (
    TextField,
    PasswordField,
    CheckboxField,
    TagListField,
)


def _reload_security_module():
    """Reload security module to pick up patched values."""
    import importlib
    import shelfmark.config.security
    importlib.reload(shelfmark.config.security)
    return shelfmark.config.security.security_settings()


def _get_field(fields, key):
    """Find a field by key."""
    return next((f for f in fields if f.key == key), None)


class TestOIDCAuthMethodOption:
    """Tests that OIDC appears as an auth method option."""

    def test_oidc_option_available(self):
        fields = _reload_security_module()
        auth_field = _get_field(fields, "AUTH_METHOD")
        option_values = [opt["value"] for opt in auth_field.options]
        assert "oidc" in option_values

    def test_oidc_option_label(self):
        fields = _reload_security_module()
        auth_field = _get_field(fields, "AUTH_METHOD")
        oidc_option = next(o for o in auth_field.options if o["value"] == "oidc")
        assert "OIDC" in oidc_option["label"]


class TestOIDCFieldsPresent:
    """Tests that all OIDC configuration fields are registered."""

    def test_discovery_url_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_DISCOVERY_URL")
        assert field is not None
        assert isinstance(field, TextField)

    def test_client_id_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_CLIENT_ID")
        assert field is not None
        assert isinstance(field, TextField)

    def test_client_secret_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_CLIENT_SECRET")
        assert field is not None
        assert isinstance(field, PasswordField)

    def test_scopes_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_SCOPES")
        assert field is not None
        assert isinstance(field, TagListField)

    def test_scopes_default_includes_groups(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_SCOPES")
        assert "openid" in field.default
        assert "email" in field.default
        assert "profile" in field.default
        assert "groups" in field.default

    def test_restrict_to_admin_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_RESTRICT_SETTINGS_TO_ADMIN")
        assert field is not None
        assert isinstance(field, CheckboxField)
        assert field.default is False

    def test_group_claim_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_GROUP_CLAIM")
        assert field is not None
        assert field.default == "groups"

    def test_admin_group_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_ADMIN_GROUP")
        assert field is not None

    def test_auto_provision_field_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_AUTO_PROVISION")
        assert field is not None
        assert isinstance(field, CheckboxField)
        assert field.default is True

    def test_test_connection_button_exists(self):
        fields = _reload_security_module()
        field = _get_field(fields, "test_oidc")
        assert field is not None


class TestOIDCFieldShowWhen:
    """Tests that OIDC fields are conditionally shown."""

    def test_oidc_fields_show_when_oidc_selected(self):
        fields = _reload_security_module()
        oidc_keys = [
            "OIDC_DISCOVERY_URL",
            "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET",
            "OIDC_SCOPES",
            "OIDC_RESTRICT_SETTINGS_TO_ADMIN",
            "OIDC_AUTO_PROVISION",
        ]
        for key in oidc_keys:
            field = _get_field(fields, key)
            assert field is not None, f"Field {key} not found"
            show_when = field.show_when
            # show_when can be a dict or list of dicts
            if isinstance(show_when, list):
                conditions = show_when
            else:
                conditions = [show_when]
            # At least one condition should reference AUTH_METHOD=oidc
            has_oidc_condition = any(
                c.get("field") == "AUTH_METHOD" and c.get("value") == "oidc"
                for c in conditions
            )
            assert has_oidc_condition, f"Field {key} missing AUTH_METHOD=oidc show_when"

    def test_group_claim_shows_when_restrict_enabled(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_GROUP_CLAIM")
        show_when = field.show_when
        if isinstance(show_when, list):
            conditions = show_when
        else:
            conditions = [show_when]
        has_restrict_condition = any(
            c.get("field") == "OIDC_RESTRICT_SETTINGS_TO_ADMIN" and c.get("value") is True
            for c in conditions
        )
        assert has_restrict_condition

    def test_admin_group_shows_when_restrict_enabled(self):
        fields = _reload_security_module()
        field = _get_field(fields, "OIDC_ADMIN_GROUP")
        show_when = field.show_when
        if isinstance(show_when, list):
            conditions = show_when
        else:
            conditions = [show_when]
        has_restrict_condition = any(
            c.get("field") == "OIDC_RESTRICT_SETTINGS_TO_ADMIN" and c.get("value") is True
            for c in conditions
        )
        assert has_restrict_condition


class TestOIDCFieldsEnvSupport:
    """Tests that OIDC fields are UI-only (no env var support)."""

    def test_oidc_fields_not_env_supported(self):
        fields = _reload_security_module()
        oidc_keys = [
            "OIDC_DISCOVERY_URL",
            "OIDC_CLIENT_ID",
            "OIDC_CLIENT_SECRET",
            "OIDC_SCOPES",
            "OIDC_RESTRICT_SETTINGS_TO_ADMIN",
            "OIDC_GROUP_CLAIM",
            "OIDC_ADMIN_GROUP",
            "OIDC_AUTO_PROVISION",
        ]
        for key in oidc_keys:
            field = _get_field(fields, key)
            assert field is not None, f"Field {key} not found"
            assert field.env_supported is False, f"Field {key} should not support env vars"

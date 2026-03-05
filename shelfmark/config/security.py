"""Authentication settings registration."""

from typing import Any, Dict, Callable

from shelfmark.config.migrations import migrate_security_settings
from shelfmark.config.security_handlers import (
    on_save_security,
    test_oidc_connection,
)
from shelfmark.core.logger import setup_logger
from shelfmark.core.settings_registry import (
    register_settings,
    register_on_save,
    load_config_file,
    TextField,
    SelectField,
    PasswordField,
    CheckboxField,
    ActionButton,
    TagListField,
    CustomComponentField,
)
from shelfmark.core.user_db import sync_builtin_admin_user

logger = setup_logger(__name__)


def _auth_condition(auth_method: str) -> dict[str, str]:
    return {"field": "AUTH_METHOD", "value": auth_method}


def _auth_field(factory: Callable[..., Any], auth_method: str, **kwargs: Any) -> Any:
    return factory(show_when=_auth_condition(auth_method), **kwargs)


def _migrate_security_settings() -> None:
    from shelfmark.core.settings_registry import (
        _get_config_file_path,
        _ensure_config_dir,
        save_config_file,
    )

    migrate_security_settings(
        load_security_config=lambda: load_config_file("security"),
        load_users_config=lambda: load_config_file("users"),
        save_users_config=lambda values: save_config_file("users", values),
        ensure_config_dir=lambda: _ensure_config_dir("security"),
        get_config_path=lambda: _get_config_file_path("security"),
        sync_builtin_admin_user=sync_builtin_admin_user,
        logger=logger,
    )



def _on_save_security(values: Dict[str, Any]) -> Dict[str, Any]:
    return on_save_security(values)


def _test_oidc_connection(current_values: Dict[str, Any] = None) -> Dict[str, Any]:
    return test_oidc_connection(
        load_security_config=lambda: load_config_file("security"),
        current_values=current_values or {},
        logger=logger,
    )


@register_settings("security", "Security", icon="shield", order=5)
def security_settings():
    """Security and authentication settings."""
    from shelfmark.config.env import CWA_DB_PATH

    cwa_db_available = CWA_DB_PATH is not None and CWA_DB_PATH.exists()

    auth_method_options = [
        {"label": "No Authentication", "value": "none"},
        {"label": "Local", "value": "builtin"},
        {"label": "Proxy Authentication", "value": "proxy"},
        {"label": "OIDC (OpenID Connect)", "value": "oidc"},
        {"label": "Calibre-Web Database", "value": "cwa"},
    ]

    fields = [
        SelectField(
            key="AUTH_METHOD",
            label="Authentication Method",
            description="Select the authentication method for accessing Shelfmark.",
            options=auth_method_options,
            default="none",
        ),
        CustomComponentField(
            key="builtin_admin_requirement",
            component="oidc_admin_hint",
            label=(
                "Local authentication is inactive until a local admin account with a "
                "password is created."
            ),
            show_when=_auth_condition("builtin"),
        ),
        CustomComponentField(
            key="oidc_admin_requirement",
            component="oidc_admin_hint",
            label="A local admin account is required before OIDC can be enabled.",
            show_when=_auth_condition("oidc"),
        ),
        *([] if cwa_db_available else [
            CustomComponentField(
                key="cwa_db_missing",
                component="oidc_admin_hint",
                label=(
                    "Calibre-Web database not detected. Mount your app.db to "
                    "/auth/app.db to enable this method. Authentication will fall "
                    "back to none until the database is available."
                ),
                show_when=_auth_condition("cwa"),
            ),
        ]),
        ActionButton(
            key="open_users_tab",
            label="Go to Users",
            description="Configure local users and admin access in the Users tab.",
            style="primary",
            show_when={"field": "AUTH_METHOD", "value": ["builtin", "oidc"]},
        ),
        _auth_field(
            TextField,
            "proxy",
            key="PROXY_AUTH_USER_HEADER",
            label="Proxy Auth User Header",
            description="The HTTP header your proxy uses to pass the authenticated username.",
            placeholder="e.g. X-Auth-User",
            default="X-Auth-User",
        ),
        _auth_field(
            TextField,
            "proxy",
            key="PROXY_AUTH_LOGOUT_URL",
            label="Proxy Auth Logout URL",
            description="The URL to redirect users to for logging out. Leave empty to disable logout functionality.",
            placeholder="https://myauth.example.com/logout",
            default="",
        ),
        _auth_field(
            TextField,
            "proxy",
            key="PROXY_AUTH_ADMIN_GROUP_HEADER",
            label="Proxy Auth Admin Group Header",
            description="Optional: header your proxy uses to pass user groups/roles.",
            placeholder="e.g. X-Auth-Groups",
            default="X-Auth-Groups",
        ),
        _auth_field(
            TextField,
            "proxy",
            key="PROXY_AUTH_ADMIN_GROUP_NAME",
            label="Proxy Auth Admin Group",
            description="Optional: users in this group are treated as admins. Leave blank to skip group-based admin detection.",
            placeholder="e.g. admins",
            default="",
        ),
    ]

    fields.append(
        CustomComponentField(
            key="oidc_callback_url",
            component="settings_label",
            label="Callback URL",
            description="{origin}/api/auth/oidc/callback",
            show_when=_auth_condition("oidc"),
        )
    )

    oidc_specs = [
        (
            TextField,
            {
                "key": "OIDC_DISCOVERY_URL",
                "label": "Discovery URL",
                "description": "OpenID Connect discovery endpoint URL. Usually ends with /.well-known/openid-configuration.",
                "placeholder": "https://auth.example.com/.well-known/openid-configuration",
                "required": True,
            },
        ),
        (
            TextField,
            {
                "key": "OIDC_CLIENT_ID",
                "label": "Client ID",
                "description": "OAuth2 client ID from your identity provider.",
                "placeholder": "shelfmark",
                "required": True,
            },
        ),
        (
            PasswordField,
            {
                "key": "OIDC_CLIENT_SECRET",
                "label": "Client Secret",
                "description": "OAuth2 client secret from your identity provider.",
                "required": True,
            },
        ),
        (
            TagListField,
            {
                "key": "OIDC_SCOPES",
                "label": "Scopes",
                "description": "OAuth2 scopes to request from the identity provider. Managed automatically: includes essential scopes and the group claim when using admin group authorization.",
                "default": ["openid", "email", "profile"],
            },
        ),
        (
            TextField,
            {
                "key": "OIDC_GROUP_CLAIM",
                "label": "Group Claim Name",
                "description": "The name of the claim in the ID token that contains user groups.",
                "placeholder": "groups",
                "default": "groups",
            },
        ),
        (
            TextField,
            {
                "key": "OIDC_ADMIN_GROUP",
                "label": "Admin Group Name",
                "description": "Users in this group will be given admin access (if enabled below). Leave empty to use database roles only.",
                "placeholder": "shelfmark-admins",
                "default": "",
            },
        ),
        (
            CheckboxField,
            {
                "key": "OIDC_USE_ADMIN_GROUP",
                "label": "Use Admin Group for Authorization",
                "description": "When enabled, users in the Admin Group are granted admin access. When disabled, admin access is determined solely by database roles.",
                "default": True,
            },
        ),
        (
            CheckboxField,
            {
                "key": "OIDC_AUTO_PROVISION",
                "label": "Auto-Provision Users",
                "description": "Automatically create a user account on first OIDC login. When disabled, users must be pre-created by an admin.",
                "default": True,
            },
        ),
        (
            TextField,
            {
                "key": "OIDC_BUTTON_LABEL",
                "label": "Login Button Label",
                "description": "Custom label for the OIDC sign-in button on the login page.",
                "placeholder": "Sign in with OIDC",
                "default": "",
            },
        ),
    ]
    fields.extend(_auth_field(factory, "oidc", **spec) for factory, spec in oidc_specs)
    fields.append(
        ActionButton(
            key="test_oidc",
            label="Test Connection",
            description="Fetch the OIDC discovery document and validate configuration.",
            style="primary",
            callback=_test_oidc_connection,
            show_when=_auth_condition("oidc"),
        )
    )
    fields.append(
        CustomComponentField(
            key="oidc_env_info",
            component="oidc_env_info",
            label="Environment-Only Options",
            description="These options can only be set via environment variables because changing them through the UI could lock you out of the application.",
            wrap_in_field_wrapper=True,
            show_when=_auth_condition("oidc"),
        )
    )
    return fields


register_on_save("security", _on_save_security)

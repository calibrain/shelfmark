"""Authentication settings registration."""

from typing import Any, Dict

from werkzeug.security import generate_password_hash

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
)

logger = setup_logger(__name__)


def _migrate_security_settings() -> None:
    import json
    from shelfmark.core.settings_registry import _get_config_file_path, _ensure_config_dir

    try:
        config = load_config_file("security")
        migrated = False
        
        # Migrate USE_CWA_AUTH to AUTH_METHOD
        if "USE_CWA_AUTH" in config:
            old_value = config.pop("USE_CWA_AUTH")
            
            # Only set AUTH_METHOD if it doesn't already exist
            if "AUTH_METHOD" not in config:
                if old_value:
                    config["AUTH_METHOD"] = "cwa"
                    logger.info("Migrated USE_CWA_AUTH=True to AUTH_METHOD='cwa'")
                else:
                    # If USE_CWA_AUTH was False, determine auth method from credentials
                    if config.get("BUILTIN_USERNAME") and config.get("BUILTIN_PASSWORD_HASH"):
                        config["AUTH_METHOD"] = "builtin"
                        logger.info("Migrated USE_CWA_AUTH=False to AUTH_METHOD='builtin'")
                    else:
                        config["AUTH_METHOD"] = "none"
                        logger.info("Migrated USE_CWA_AUTH=False to AUTH_METHOD='none'")
                migrated = True
            else:
                logger.info("Removed deprecated USE_CWA_AUTH setting (AUTH_METHOD already exists)")
                migrated = True
        
        # Migrate RESTRICT_SETTINGS_TO_ADMIN to CWA_RESTRICT_SETTINGS_TO_ADMIN
        if "RESTRICT_SETTINGS_TO_ADMIN" in config:
            old_value = config.pop("RESTRICT_SETTINGS_TO_ADMIN")
            
            # Only migrate if new key doesn't exist
            if "CWA_RESTRICT_SETTINGS_TO_ADMIN" not in config:
                config["CWA_RESTRICT_SETTINGS_TO_ADMIN"] = old_value
                logger.info(f"Migrated RESTRICT_SETTINGS_TO_ADMIN={old_value} to CWA_RESTRICT_SETTINGS_TO_ADMIN={old_value}")
                migrated = True
            else:
                logger.info("Removed deprecated RESTRICT_SETTINGS_TO_ADMIN setting (CWA_RESTRICT_SETTINGS_TO_ADMIN already exists)")
                migrated = True
        
        # Save config if any migrations occurred
        if migrated:
            _ensure_config_dir("security")
            config_path = _get_config_file_path("security")
            with open(config_path, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("Security settings migration completed successfully")
        else:
            logger.debug("No security settings migration needed")
    
    except FileNotFoundError:
        logger.debug("No existing security config file found - nothing to migrate")
    except Exception as e:
        logger.error(f"Failed to migrate security settings: {e}")


def _clear_builtin_credentials() -> Dict[str, Any]:
    """Clear built-in credentials to allow public access."""
    import json
    from shelfmark.core.settings_registry import _get_config_file_path, _ensure_config_dir

    try:
        config = load_config_file("security")
        config.pop("BUILTIN_USERNAME", None)
        config.pop("BUILTIN_PASSWORD_HASH", None)

        _ensure_config_dir("security")
        config_path = _get_config_file_path("security")
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        logger.info("Cleared credentials")
        return {"success": True, "message": "Credentials cleared. The app is now publicly accessible."}

    except Exception as e:
        logger.error(f"Failed to clear credentials: {e}")
        return {"success": False, "message": f"Failed to clear credentials: {str(e)}"}


def _on_save_security(values: Dict[str, Any]) -> Dict[str, Any]:
    """
    Custom save handler for security settings.

    Handles password validation and hashing:
    - If new password is provided, validate confirmation and hash it
    - If password fields are empty, preserve existing hash
    - Never store raw passwords
    - Ensure username is present if password is set

    Returns:
        Dict with processed values to save and any validation errors.
    """
    password = values.get("BUILTIN_PASSWORD", "")
    password_confirm = values.get("BUILTIN_PASSWORD_CONFIRM", "")

    # Remove raw password fields - they should never be persisted
    values.pop("BUILTIN_PASSWORD", None)
    values.pop("BUILTIN_PASSWORD_CONFIRM", None)

    # If password is provided, validate and hash it
    if password:
        if not values.get("BUILTIN_USERNAME"):
            return {
                "error": True,
                "message": "Username cannot be empty",
                "values": values
            }

        if password != password_confirm:
            return {
                "error": True,
                "message": "Passwords do not match",
                "values": values
            }

        if len(password) < 4:
            return {
                "error": True,
                "message": "Password must be at least 4 characters",
                "values": values
            }

        # Hash the password
        values["BUILTIN_PASSWORD_HASH"] = generate_password_hash(password)
        logger.info("Password hash updated")

    # If no password provided but username is being set, preserve existing hash
    elif "BUILTIN_USERNAME" in values:
        existing = load_config_file("security")
        if "BUILTIN_PASSWORD_HASH" in existing:
            values["BUILTIN_PASSWORD_HASH"] = existing["BUILTIN_PASSWORD_HASH"]

    return {"error": False, "values": values}


@register_settings("security", "Security", icon="shield", order=5)
def security_settings():  
    """Security and authentication settings."""
    from shelfmark.config.env import CWA_DB_PATH

    cwa_db_available = CWA_DB_PATH is not None and CWA_DB_PATH.exists()

    auth_method_options = [
        {"label": "No Authentication", "value": "none"},
        {"label": "Username/Password", "value": "builtin"},
        {"label": "Proxy Authentication", "value": "proxy"},
    ]
    if cwa_db_available:
        auth_method_options.append({"label": "Calibre-Web Database", "value": "cwa"})

    auth_method_description = "Select the authentication method for accessing Shelfmark."
    if not cwa_db_available:
        auth_method_description += " Calibre-Web database option requires mounting your Calibre-Web app.db to /auth/app.db."

    fields = [
        SelectField(
            key="AUTH_METHOD",
            label="Authentication Method",
            description=auth_method_description,
            options=auth_method_options,
            default="none",
            env_supported=False,
        ),
        TextField(
            key="BUILTIN_USERNAME",
            label="Username",
            description="Set a username and password to require login. Leave both empty for public access.",
            placeholder="Enter username",
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "builtin"},
        ),
        PasswordField(
            key="BUILTIN_PASSWORD",
            label="Set Password",
            description="Fill in to set or change the password.",
            placeholder="Enter new password",
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "builtin"},
        ),
        PasswordField(
            key="BUILTIN_PASSWORD_CONFIRM",
            label="Confirm Password",
            placeholder="Confirm new password",
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "builtin"},
        ),
        ActionButton(
            key="clear_credentials",
            label="Clear Credentials",
            description="Remove login requirement and make the app publicly accessible.",
            style="danger",
            callback=_clear_builtin_credentials,
            show_when={"field": "AUTH_METHOD", "value": "builtin"},
        ),
        TextField(
            key="PROXY_AUTH_USER_HEADER",
            label="Proxy Auth User Header",
            description=(
                "The HTTP header your proxy uses to pass the authenticated username."
            ),
            placeholder="e.g. X-Auth-User",
            default="X-Auth-User",
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "proxy"},
        ),
        TextField(
            key="PROXY_AUTH_LOGOUT_URL",
            label="Proxy Auth Logout URL",
            description=(
                "The URL to redirect users to for logging out."
                " Leave empty to disable logout functionality."
            ),
            placeholder="https://myauth.example.com/logout",
            default="",
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "proxy"},
        ),
        CheckboxField(
            key="PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN",
            label="Restrict Settings to Admins authenticated via Proxy",
            description=(
                "Only users in the admin group can access settings."
            ),
            default=False,
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "proxy"},
        ),
        TextField(
            key="PROXY_AUTH_ADMIN_GROUP_HEADER",
            label="Proxy Auth Admin Group Header",
            description=(
                "The HTTP header your proxy uses to pass the user's groups/roles."
            ),
            placeholder="e.g. X-Auth-Groups",
            default="X-Auth-Groups",
            env_supported=False,
            show_when={"field": "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", "value": True},
        ),
        TextField(
            key="PROXY_AUTH_ADMIN_GROUP_NAME",
            label="Proxy Auth Admin Group Name",
            description=(
                "The name of the group/role that should have admin access."
            ),
            placeholder="e.g. admins",
            default="admins",
            env_supported=False,
            show_when={"field": "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN", "value": True},
        ),
        CheckboxField(
            key="CWA_RESTRICT_SETTINGS_TO_ADMIN",
            label="Restrict Settings to Admins authenticated via Calibre-Web",
            description=(
                "Only users with admin role in Calibre-Web can access settings."
            ),
            default=False,
            env_supported=False,
            show_when={"field": "AUTH_METHOD", "value": "cwa"},
        ),
    ]

    return fields


# Register the on_save handler for this tab
register_on_save("security", _on_save_security)

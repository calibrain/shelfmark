"""Configuration migration helpers."""

import json
from typing import Any, Callable


_DEPRECATED_SETTINGS_RESTRICTION_KEYS = (
    "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN",
    "CWA_RESTRICT_SETTINGS_TO_ADMIN",
    "RESTRICT_SETTINGS_TO_ADMIN",
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _pick_legacy_settings_restriction(config: dict[str, Any]) -> bool | None:
    """Pick the best legacy admin-restriction value to migrate."""
    auth_method = str(config.get("AUTH_METHOD", "")).strip().lower()

    if (
        auth_method == "proxy"
        and "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN" in config
    ):
        return _as_bool(config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN"))

    if auth_method == "cwa" and "CWA_RESTRICT_SETTINGS_TO_ADMIN" in config:
        return _as_bool(config.get("CWA_RESTRICT_SETTINGS_TO_ADMIN"))

    if "RESTRICT_SETTINGS_TO_ADMIN" in config:
        return _as_bool(config.get("RESTRICT_SETTINGS_TO_ADMIN"))

    if "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN" in config:
        return _as_bool(config.get("PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN"))

    if "CWA_RESTRICT_SETTINGS_TO_ADMIN" in config:
        return _as_bool(config.get("CWA_RESTRICT_SETTINGS_TO_ADMIN"))

    return None


def migrate_security_settings(
    *,
    load_security_config: Callable[[], dict[str, Any]],
    load_users_config: Callable[[], dict[str, Any]],
    save_users_config: Callable[[dict[str, Any]], None],
    ensure_config_dir: Callable[[], None],
    get_config_path: Callable[[], Any],
    sync_builtin_admin_user: Callable[[str, str], None],
    logger: Any,
) -> None:
    """Migrate legacy security keys and sync builtin admin credentials."""
    try:
        config = load_security_config()
        users_config = load_users_config()
        migrated_security = False
        migrated_users = False

        if "USE_CWA_AUTH" in config:
            old_value = config.pop("USE_CWA_AUTH")
            if "AUTH_METHOD" not in config:
                if old_value:
                    config["AUTH_METHOD"] = "cwa"
                    logger.info("Migrated USE_CWA_AUTH=True to AUTH_METHOD='cwa'")
                else:
                    if config.get("BUILTIN_USERNAME") and config.get("BUILTIN_PASSWORD_HASH"):
                        config["AUTH_METHOD"] = "builtin"
                        logger.info("Migrated USE_CWA_AUTH=False to AUTH_METHOD='builtin'")
                    else:
                        config["AUTH_METHOD"] = "none"
                        logger.info("Migrated USE_CWA_AUTH=False to AUTH_METHOD='none'")
                migrated_security = True
            else:
                logger.info("Removed deprecated USE_CWA_AUTH setting (AUTH_METHOD already exists)")
                migrated_security = True

        if "RESTRICT_SETTINGS_TO_ADMIN" not in users_config:
            legacy_restrict = _pick_legacy_settings_restriction(config)
            if legacy_restrict is not None:
                save_users_config({"RESTRICT_SETTINGS_TO_ADMIN": legacy_restrict})
                migrated_users = True
                logger.info(
                    "Migrated legacy settings-admin restriction to users.RESTRICT_SETTINGS_TO_ADMIN="
                    f"{legacy_restrict}"
                )

        for deprecated_key in _DEPRECATED_SETTINGS_RESTRICTION_KEYS:
            if deprecated_key in config:
                config.pop(deprecated_key, None)
                migrated_security = True
                logger.info(f"Removed deprecated security setting: {deprecated_key}")

        try:
            sync_builtin_admin_user(
                config.get("BUILTIN_USERNAME", ""),
                config.get("BUILTIN_PASSWORD_HASH", ""),
            )
        except Exception as exc:
            logger.error(
                "Failed to sync builtin credentials to users database during migration: "
                f"{exc}"
            )

        if migrated_security:
            ensure_config_dir()
            config_path = get_config_path()
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            logger.info("Security settings migration completed successfully")
        elif migrated_users:
            logger.info("Users settings migration completed successfully")
        else:
            logger.debug("No security settings migration needed")

    except FileNotFoundError:
        logger.debug("No existing security config file found - nothing to migrate")
    except Exception as exc:
        logger.error(f"Failed to migrate security settings: {exc}")

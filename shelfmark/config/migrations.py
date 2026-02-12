"""Configuration migration helpers."""

import json
from typing import Any, Callable


def migrate_security_settings(
    *,
    load_security_config: Callable[[], dict[str, Any]],
    ensure_config_dir: Callable[[], None],
    get_config_path: Callable[[], Any],
    sync_builtin_admin_user: Callable[[str, str], None],
    logger: Any,
) -> None:
    """Migrate legacy security keys and sync builtin admin credentials."""
    try:
        config = load_security_config()
        migrated = False

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
                migrated = True
            else:
                logger.info("Removed deprecated USE_CWA_AUTH setting (AUTH_METHOD already exists)")
                migrated = True

        if "RESTRICT_SETTINGS_TO_ADMIN" in config:
            old_value = config.pop("RESTRICT_SETTINGS_TO_ADMIN")
            if "CWA_RESTRICT_SETTINGS_TO_ADMIN" not in config:
                config["CWA_RESTRICT_SETTINGS_TO_ADMIN"] = old_value
                logger.info(
                    "Migrated RESTRICT_SETTINGS_TO_ADMIN="
                    f"{old_value} to CWA_RESTRICT_SETTINGS_TO_ADMIN={old_value}"
                )
                migrated = True
            else:
                logger.info(
                    "Removed deprecated RESTRICT_SETTINGS_TO_ADMIN setting "
                    "(CWA_RESTRICT_SETTINGS_TO_ADMIN already exists)"
                )
                migrated = True

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

        if migrated:
            ensure_config_dir()
            config_path = get_config_path()
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
            logger.info("Security settings migration completed successfully")
        else:
            logger.debug("No security settings migration needed")

    except FileNotFoundError:
        logger.debug("No existing security config file found - nothing to migrate")
    except Exception as exc:
        logger.error(f"Failed to migrate security settings: {exc}")

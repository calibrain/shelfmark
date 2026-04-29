"""Persistence helpers for the connected Hardcover account."""


def _save_connected_user(user_id: str | None, username: str | None) -> None:
    """Save or clear connected user metadata in config."""
    from shelfmark.core.settings_registry import load_config_file, save_config_file

    config = load_config_file("hardcover")
    if user_id:
        config["_connected_user_id"] = user_id
    else:
        config.pop("_connected_user_id", None)

    if username:
        config["_connected_username"] = username
    else:
        config.pop("_connected_username", None)

    save_config_file("hardcover", config)


def _get_connected_username() -> str | None:
    """Get the stored connected username."""
    from shelfmark.core.settings_registry import load_config_file

    config = load_config_file("hardcover")
    return config.get("_connected_username")


def _get_connected_user_id() -> str | None:
    """Get the stored connected Hardcover user id."""
    from shelfmark.core.settings_registry import load_config_file

    config = load_config_file("hardcover")
    value = config.get("_connected_user_id")
    return str(value) if value is not None else None

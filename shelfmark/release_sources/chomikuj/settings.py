"""Chomikuj.pl settings registration."""

import math
from typing import Any

from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)


def _resolve_setting_text(current_values: dict[str, Any], key: str) -> str:
    """Prefer current form values, then fall back to persisted config text."""
    from shelfmark.core.config import config

    current_value = normalize_optional_text(current_values.get(key))
    if current_value is not None:
        return current_value
    return normalize_optional_text(config.get(key, "")) or ""


def _account_summary(account: dict[str, object]) -> str:
    """Describe a free account's remaining transfer, mirroring the mobile app."""
    balance = account.get("BalanceInfo")
    if isinstance(balance, str) and balance.strip():
        return balance.strip()

    number: float | None = None
    transfer = account.get("Transfer")
    if isinstance(transfer, (int, float)) and not isinstance(transfer, bool):
        number = float(transfer)
    elif isinstance(transfer, str):
        try:
            number = float(transfer.strip())
        except ValueError:
            number = None
    if number is not None and math.isfinite(number):
        return f"{number:g} kB transfer"
    return "unknown balance"


def _test_chomikuj_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Log in with the current form credentials and report the account tier."""
    from shelfmark.release_sources.chomikuj.client import (
        ChomikujClient,
        ChomikujError,
        normalize_base_url,
    )
    from shelfmark.release_sources.chomikuj.session import clear_session, get_session

    current_values = current_values or {}
    username = _resolve_setting_text(current_values, "CHOMIKUJ_USERNAME")
    password = _resolve_setting_text(current_values, "CHOMIKUJ_PASSWORD")
    if not username or not password:
        return {"success": False, "message": "Username and password are required"}

    base_url = normalize_base_url(_resolve_setting_text(current_values, "CHOMIKUJ_BASE_URL"))
    try:
        client = ChomikujClient(base_url)
        session = get_session(client, username=username, password=password, base_url=base_url)
        account = client.account_info(session.api_key)
    except ChomikujError as exc:
        if exc.kind == "unauthorized":
            clear_session(username, password, base_url)
        return {"success": False, "message": f"Login failed: {exc}"}
    except Exception:  # noqa: BLE001 - a settings action must never raise or echo secrets
        return {"success": False, "message": "Login failed: unexpected error"}

    if account.get("HasUnlimitedTransfer") is True:
        return {"success": True, "message": "Chomikuj.pl (unlimited transfer)"}
    return {"success": True, "message": f"Chomikuj.pl (free, {_account_summary(account)})"}


@register_settings("chomikuj_config", "Chomikuj.pl", icon="download", order=48)
def chomikuj_config_settings() -> list[SettingsField]:
    """Chomikuj.pl configuration settings."""
    enabled = {"field": "CHOMIKUJ_ENABLED", "value": True}
    return [
        CheckboxField(
            key="CHOMIKUJ_ENABLED",
            label="Enable Chomikuj.pl",
            description=(
                "Search Chomikuj.pl's Documents collection and download matching ebooks "
                "with your own Chomikuj account. Chomikuj results often include PDF; "
                "enabling PDF under Supported Book Formats can show more results, while "
                "unsupported formats may be filtered out."
            ),
            default=False,
        ),
        TextField(
            key="CHOMIKUJ_USERNAME",
            label="Username or Email",
            description="Your Chomikuj.pl login.",
            required=True,
            show_when=enabled,
        ),
        PasswordField(
            key="CHOMIKUJ_PASSWORD",
            label="Password",
            description="Your Chomikuj.pl password.",
            required=True,
            show_when=enabled,
        ),
        ActionButton(
            key="test_chomikuj",
            label="Test Login",
            description="Log in to Chomikuj.pl and show the account's transfer tier.",
            style="primary",
            callback=_test_chomikuj_connection,
            show_when=enabled,
        ),
        CheckboxField(
            key="CHOMIKUJ_ALLOW_TRANSFER_SPEND",
            label="Allow Spending Transfer",
            description=(
                "Allow downloading or previewing paid Chomikuj.pl files. Previewing a "
                "paid file can spend transfer from your Chomikuj.pl account."
            ),
            default=True,
            show_when=enabled,
        ),
        NumberField(
            key="CHOMIKUJ_MAX_RESULTS",
            label="Max Results",
            description=(
                "Maximum number of files to request per search (1-200). Results are then "
                "filtered to your supported ebook formats."
            ),
            default=50,
            min_value=1,
            max_value=200,
            show_when=enabled,
        ),
        TextField(
            key="CHOMIKUJ_BASE_URL",
            label="Base URL",
            description="Chomikuj.pl base URL used for API requests.",
            default="https://mobile.chomikuj.pl",
            show_when=enabled,
        ),
    ]

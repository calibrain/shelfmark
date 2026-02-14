"""Notifications settings tab registration."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from shelfmark.core.notifications import NotificationEvent, send_test_notification
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    HeadingField,
    MultiSelectField,
    TagListField,
    load_config_file,
    register_on_save,
    register_settings,
)

_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*$")
_ADMIN_EVENT_OPTIONS = [
    {"value": NotificationEvent.REQUEST_CREATED.value, "label": "New request submitted"},
    {"value": NotificationEvent.REQUEST_FULFILLED.value, "label": "Request fulfilled"},
    {"value": NotificationEvent.REQUEST_REJECTED.value, "label": "Request rejected"},
    {"value": NotificationEvent.DOWNLOAD_COMPLETE.value, "label": "Download complete"},
    {"value": NotificationEvent.DOWNLOAD_FAILED.value, "label": "Download failed"},
]
_ADMIN_EVENT_ORDER = [option["value"] for option in _ADMIN_EVENT_OPTIONS]
_DEFAULT_ADMIN_EVENTS = [
    NotificationEvent.REQUEST_CREATED.value,
    NotificationEvent.DOWNLOAD_FAILED.value,
]


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _normalize_urls(value: Any) -> list[str]:
    if value is None:
        return []

    raw_values: list[Any]
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = [segment for part in value.splitlines() for segment in part.split(",")]
    else:
        raw_values = [value]

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_url in raw_values:
        url = str(raw_url or "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _normalize_admin_events(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = value.split(",")
    else:
        raw_values = [value]

    allowed = set(_ADMIN_EVENT_ORDER)
    selected = {str(raw_event or "").strip() for raw_event in raw_values if str(raw_event or "").strip() in allowed}
    return [event for event in _ADMIN_EVENT_ORDER if event in selected]


def _looks_like_apprise_url(url: str) -> bool:
    split = urlsplit(url)
    if not split.scheme:
        return False
    if not _URL_SCHEME_RE.match(split.scheme):
        return False
    return " " not in url


def _on_save_notifications(values: dict[str, Any]) -> dict[str, Any]:
    existing = load_config_file("notifications")
    effective: dict[str, Any] = dict(existing)
    effective.update(values)

    enabled = _as_bool(effective.get("NOTIFICATIONS_ENABLED", False))
    normalized_urls = _normalize_urls(effective.get("ADMIN_NOTIFICATION_URLS", []))
    invalid_count = sum(1 for url in normalized_urls if not _looks_like_apprise_url(url))
    if invalid_count:
        return {
            "error": True,
            "message": (
                f"Found {invalid_count} invalid notification URL(s). "
                "Use Apprise URLs with a valid scheme, e.g. discord://... or ntfys://..."
            ),
            "values": values,
        }

    normalized_events = _normalize_admin_events(effective.get("ADMIN_NOTIFICATION_EVENTS", []))
    if enabled and not normalized_events:
        return {
            "error": True,
            "message": "Select at least one notification event when notifications are enabled.",
            "values": values,
        }

    if "ADMIN_NOTIFICATION_URLS" in values:
        values["ADMIN_NOTIFICATION_URLS"] = normalized_urls
    if "ADMIN_NOTIFICATION_EVENTS" in values:
        values["ADMIN_NOTIFICATION_EVENTS"] = normalized_events

    return {"error": False, "values": values}


def _test_admin_notification_action(current_values: dict[str, Any]) -> dict[str, Any]:
    persisted = load_config_file("notifications")
    effective: dict[str, Any] = dict(persisted)
    if isinstance(current_values, dict):
        effective.update(current_values)

    if not _as_bool(effective.get("NOTIFICATIONS_ENABLED", False)):
        return {"success": False, "message": "Enable notifications first."}

    urls = _normalize_urls(effective.get("ADMIN_NOTIFICATION_URLS", []))
    invalid_count = sum(1 for url in urls if not _looks_like_apprise_url(url))
    if invalid_count:
        return {
            "success": False,
            "message": (
                f"Found {invalid_count} invalid notification URL(s). "
                "Fix URL formatting before running a test."
            ),
        }

    return send_test_notification(urls)


register_on_save("notifications", _on_save_notifications)


@register_settings("notifications", "Notifications", icon="bell", order=7)
def notifications_settings():
    """Global notifications settings."""
    return [
        HeadingField(
            key="notifications_heading",
            title="Notifications",
            description="Send push notifications via Apprise.",
        ),
        HeadingField(
            key="notifications_help",
            title="Setup Help",
            description="See Apprise service URL formats and setup steps.",
            link_url="https://github.com/caronc/apprise/wiki",
            link_text="Apprise Wiki",
        ),
        CheckboxField(
            key="NOTIFICATIONS_ENABLED",
            label="Enable Notifications",
            description="Master toggle for global notifications.",
            default=False,
        ),
        TagListField(
            key="ADMIN_NOTIFICATION_URLS",
            label="Notification URLs",
            description="One Apprise URL per entry.",
            default=[],
            placeholder="e.g. discord://WebhookID/Token",
            show_when={"field": "NOTIFICATIONS_ENABLED", "value": True},
        ),
        MultiSelectField(
            key="ADMIN_NOTIFICATION_EVENTS",
            label="Notification Events",
            description="Choose which events are sent to configured notification URLs.",
            options=_ADMIN_EVENT_OPTIONS,
            default=_DEFAULT_ADMIN_EVENTS,
            show_when={"field": "NOTIFICATIONS_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_admin_notification",
            label="Test Notification",
            description="Send a test notification to the configured URLs.",
            style="primary",
            callback=_test_admin_notification_action,
            show_when={"field": "NOTIFICATIONS_ENABLED", "value": True},
        ),
    ]


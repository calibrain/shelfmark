"""Administrator notification target settings."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from shelfmark.core.config import config as app_config
from shelfmark.core.notifications import NotificationEvent, send_test_notification
from shelfmark.core.settings_registry import (
    ActionButton,
    HeadingField,
    SettingsField,
    TableField,
    TextField,
    load_config_file,
    register_on_save,
    register_settings,
)

_EVENT_OPTIONS = [
    {"value": NotificationEvent.REQUEST_CREATED.value, "label": "New request submitted"},
    {"value": NotificationEvent.DOWNLOAD_COMPLETE.value, "label": "Download complete"},
    {"value": NotificationEvent.DOWNLOAD_FAILED.value, "label": "Download failed"},
]
_EVENT_VALUES = {item["value"] for item in _EVENT_OPTIONS}
_DEFAULT_TARGET = {"transport": "apprise", "destination": "", "events": []}


def _valid_destination(transport: str, destination: str) -> bool:
    if not destination:
        return True
    if transport == "email":
        return "@" in destination
    return bool(urlsplit(destination).scheme) and " " not in destination


def normalize_notification_routes(value: Any) -> list[dict[str, Any]]:
    """Normalize the instance-level operational notification targets."""
    if not isinstance(value, list):
        return []
    targets: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    for row in value:
        if not isinstance(row, dict):
            continue
        transport = str(row.get("transport") or "").strip().lower()
        destination = str(row.get("destination") or "").strip()
        raw_events = row.get("events", [])
        raw_events = raw_events if isinstance(raw_events, list) else [raw_events]
        events = [str(event).strip() for event in raw_events if str(event).strip() in _EVENT_VALUES]
        events = list(dict.fromkeys(events))
        if transport not in {"email", "apprise"}:
            continue
        key = (transport, destination, tuple(events))
        if key not in seen:
            seen.add(key)
            targets.append({"transport": transport, "destination": destination, "events": events})
    return targets


def _on_save_notifications(values: dict[str, Any]) -> dict[str, Any]:
    effective = dict(load_config_file("notifications"))
    effective.update(values)
    raw_targets = effective.get("ADMIN_NOTIFICATION_TARGETS", [])
    if not isinstance(raw_targets, list):
        return {"error": True, "message": "Notification targets must be a list.", "values": values}
    targets = normalize_notification_routes(raw_targets)
    if len(targets) != len([row for row in raw_targets if isinstance(row, dict)]):
        return {
            "error": True,
            "message": "Each notification target needs a valid transport.",
            "values": values,
        }
    if any(not _valid_destination(row["transport"], row["destination"]) for row in targets):
        return {
            "error": True,
            "message": "A notification target has an invalid destination.",
            "values": values,
        }
    if "ADMIN_NOTIFICATION_TARGETS" in values:
        values["ADMIN_NOTIFICATION_TARGETS"] = targets or [dict(_DEFAULT_TARGET)]
    return {"error": False, "values": values}


def _test_admin_notification_action(current_values: dict[str, Any]) -> dict[str, Any]:
    targets = normalize_notification_routes(
        current_values.get(
            "ADMIN_NOTIFICATION_TARGETS", app_config.get("ADMIN_NOTIFICATION_TARGETS", [])
        )
    )
    active = next((target for target in targets if target["destination"]), None)
    if active is None:
        return {"success": False, "message": "Add a global notification destination first."}
    if active["transport"] != "apprise":
        return {
            "success": False,
            "message": "Email targets can be tested from the configured SMTP service.",
        }
    return send_test_notification([active["destination"]])


register_on_save("notifications", _on_save_notifications)


@register_settings("notifications", "Notifications", icon="bell", order=7)
def notifications_settings() -> list[SettingsField]:
    return [
        HeadingField(
            key="notifications_heading",
            title="Administrator Notifications",
            description="Instance-level operational delivery targets. Personal notifications are configured by each user.",
        ),
        TextField(
            key="NOTIFICATION_BASE_URL",
            label="Public Base URL",
            description=(
                "The public URL where users reach this Shelfmark instance "
                "(e.g. https://shelfmark.example.com). Used to build clickable "
                "links to books in notification emails. Leave blank to send "
                "relative links."
            ),
            placeholder="https://shelfmark.example.com",
            env_supported=True,
        ),
        TableField(
            key="ADMIN_NOTIFICATION_TARGETS",
            label="",
            description="Create one target per destination. Apprise formats are documented at https://appriseit.com/services/.",
            columns=[
                {
                    "key": "transport",
                    "label": "Transport",
                    "type": "select",
                    "options": [
                        {"value": "email", "label": "Email"},
                        {"value": "apprise", "label": "Apprise"},
                    ],
                    "defaultValue": "apprise",
                },
                {
                    "key": "destination",
                    "label": "Destination",
                    "type": "text",
                    "placeholder": "admin@example.com or ntfys://ntfy.sh/shelfmark",
                },
                {
                    "key": "events",
                    "label": "Events",
                    "type": "multiselect",
                    "options": _EVENT_OPTIONS,
                    "defaultValue": [],
                },
            ],
            default=[dict(_DEFAULT_TARGET)],
            add_label="Add Target",
            empty_message="No targets configured.",
        ),
        ActionButton(
            key="test_admin_notification",
            label="Test Notification",
            description="Send a test notification to the first configured Apprise target.",
            style="primary",
            callback=_test_admin_notification_action,
        ),
    ]

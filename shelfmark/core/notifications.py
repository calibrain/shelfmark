"""Apprise notification dispatch for global and per-user events."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable

try:
    import apprise
except Exception:  # pragma: no cover - exercised in tests via monkeypatch
    apprise = None  # type: ignore[assignment]

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Small pool for non-blocking dispatch. Notification sends are I/O bound and infrequent.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="Notify")
_ROUTE_EVENT_ALL = "all"
_APPRISE_APP_ID = "Shelfmark"
_APPRISE_APP_DESC = "Shelfmark notifications"
_APPRISE_LOGO_URL = (
    "https://raw.githubusercontent.com/calibrain/shelfmark/main/src/frontend/public/logo.png"
)


class NotificationEvent(str, Enum):
    """Global notification event identifiers."""

    REQUEST_CREATED = "request_created"
    REQUEST_FULFILLED = "request_fulfilled"
    REQUEST_REJECTED = "request_rejected"
    DOWNLOAD_COMPLETE = "download_complete"
    DOWNLOAD_FAILED = "download_failed"


@dataclass
class NotificationContext:
    """Context used to render notification templates."""

    event: NotificationEvent
    title: str
    author: str
    username: str | None = None
    content_type: str | None = None
    format: str | None = None
    source: str | None = None
    admin_note: str | None = None
    error_message: str | None = None


def _normalize_urls(value: Any) -> list[str]:
    if value is None:
        return []

    raw_values: list[Any]
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        # Support legacy/manual configs.
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


def _normalize_routes(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    allowed_events = {_ROUTE_EVENT_ALL, *(event.value for event in NotificationEvent)}
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for row in value:
        if not isinstance(row, dict):
            continue

        raw_events = row.get("event")
        if isinstance(raw_events, list):
            event_values = raw_events
        elif isinstance(raw_events, (tuple, set)):
            event_values = list(raw_events)
        else:
            event_values = [raw_events]

        url = str(row.get("url") or "").strip()
        if not url:
            continue

        row_events: list[str] = []
        for raw_event in event_values:
            event = str(raw_event or "").strip().lower()
            if event not in allowed_events:
                continue
            if event in row_events:
                continue
            row_events.append(event)

        if _ROUTE_EVENT_ALL in row_events:
            row_events = [_ROUTE_EVENT_ALL]

        for event in row_events:
            key = (event, url)
            if key in seen:
                continue
            seen.add(key)

            normalized.append({"event": event, "url": url})

    return normalized


def _resolve_admin_routes() -> list[dict[str, str]]:
    return _normalize_routes(app_config.get("ADMIN_NOTIFICATION_ROUTES", []))


def _normalize_user_id(value: Any) -> int | None:
    try:
        user_id = int(value)
    except (TypeError, ValueError):
        return None
    if user_id < 1:
        return None
    return user_id


def _resolve_user_routes(user_id: int | None) -> list[dict[str, str]]:
    normalized_user_id = _normalize_user_id(user_id)
    if normalized_user_id is None:
        return []

    return _normalize_routes(
        app_config.get("USER_NOTIFICATION_ROUTES", [], user_id=normalized_user_id)
    )


def _resolve_route_urls_for_event(
    routes: list[dict[str, str]],
    event: NotificationEvent,
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    event_value = event.value

    for row in routes:
        row_event = row.get("event", "")
        if row_event not in {_ROUTE_EVENT_ALL, event_value}:
            continue

        url = row.get("url", "")
        if not url or url in seen:
            continue

        seen.add(url)
        selected.append(url)

    return selected


def _resolve_notify_type(event: NotificationEvent) -> Any:
    if apprise is None:
        fallback = {
            NotificationEvent.REQUEST_CREATED: "info",
            NotificationEvent.REQUEST_FULFILLED: "success",
            NotificationEvent.REQUEST_REJECTED: "warning",
            NotificationEvent.DOWNLOAD_COMPLETE: "success",
            NotificationEvent.DOWNLOAD_FAILED: "failure",
        }
        return fallback[event]

    mapping = {
        NotificationEvent.REQUEST_CREATED: apprise.NotifyType.INFO,
        NotificationEvent.REQUEST_FULFILLED: apprise.NotifyType.SUCCESS,
        NotificationEvent.REQUEST_REJECTED: apprise.NotifyType.WARNING,
        NotificationEvent.DOWNLOAD_COMPLETE: apprise.NotifyType.SUCCESS,
        NotificationEvent.DOWNLOAD_FAILED: apprise.NotifyType.FAILURE,
    }
    return mapping[event]


def _clean_text(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _render_message(context: NotificationContext) -> tuple[str, str]:
    event = context.event
    title = _clean_text(context.title, "Unknown title")
    author = _clean_text(context.author, "Unknown author")
    username = _clean_text(context.username, "A user")

    if event == NotificationEvent.REQUEST_CREATED:
        return "New Request", f'{username} requested "{title}" by {author}'
    if event == NotificationEvent.REQUEST_FULFILLED:
        return "Request Approved", f'Request for "{title}" by {author} was approved.'
    if event == NotificationEvent.REQUEST_REJECTED:
        note = _clean_text(context.admin_note, "")
        note_line = f"\nNote: {note}" if note else ""
        return "Request Rejected", f'Request for "{title}" by {author} was rejected.{note_line}'
    if event == NotificationEvent.DOWNLOAD_COMPLETE:
        return "Download Complete", f'"{title}" by {author} downloaded successfully.'

    error_message = _clean_text(context.error_message, "")
    error_line = f"\nError: {error_message}" if error_message else ""
    return "Download Failed", f'Failed to download "{title}" by {author}.{error_line}'


def _dispatch_to_apprise(
    urls: Iterable[str],
    *,
    title: str,
    body: str,
    notify_type: Any,
) -> dict[str, Any]:
    normalized_urls = _normalize_urls(list(urls))
    if not normalized_urls:
        return {"success": False, "message": "No notification URLs configured"}

    if apprise is None:
        return {"success": False, "message": "Apprise is not installed"}

    apobj = _create_apprise_client()
    if apobj is None:
        return {"success": False, "message": "Apprise is not installed"}
    valid_urls = 0
    invalid_urls = 0
    for url in normalized_urls:
        try:
            added = bool(apobj.add(url))
        except Exception:
            added = False
        if added:
            valid_urls += 1
        else:
            invalid_urls += 1

    if valid_urls == 0:
        return {
            "success": False,
            "message": "No valid notification URLs configured",
        }

    try:
        delivered = bool(apobj.notify(title=title, body=body, notify_type=notify_type))
    except Exception as exc:
        return {"success": False, "message": f"Notification send failed: {type(exc).__name__}: {exc}"}

    if not delivered:
        return {"success": False, "message": "Notification delivery failed"}

    message = f"Notification sent to {valid_urls} URL(s)"
    if invalid_urls:
        message += f" ({invalid_urls} invalid URL(s) skipped)"
    return {"success": True, "message": message}


def _create_apprise_client() -> Any:
    if apprise is None:
        return None

    apprise_cls = getattr(apprise, "Apprise", None)
    if apprise_cls is None:
        return None

    apprise_asset_cls = getattr(apprise, "AppriseAsset", None)
    if apprise_asset_cls is None:
        return apprise_cls()

    try:
        asset = apprise_asset_cls(
            app_id=_APPRISE_APP_ID,
            app_desc=_APPRISE_APP_DESC,
            image_url_logo=_APPRISE_LOGO_URL,
        )
    except TypeError:
        # Support older Apprise versions that do not expose image_url_logo.
        asset = apprise_asset_cls(
            app_id=_APPRISE_APP_ID,
            app_desc=_APPRISE_APP_DESC,
        )
    except Exception:
        return apprise_cls()

    try:
        return apprise_cls(asset=asset)
    except Exception:
        return apprise_cls()


def _send_admin_event(event: NotificationEvent, context: NotificationContext, urls: list[str]) -> dict[str, Any]:
    title, body = _render_message(context)
    notify_type = _resolve_notify_type(event)
    return _dispatch_to_apprise(urls, title=title, body=body, notify_type=notify_type)


def notify_admin(event: NotificationEvent, context: NotificationContext) -> None:
    """Send a global admin notification for an event if subscribed."""
    routes = _resolve_admin_routes()
    urls = _resolve_route_urls_for_event(routes, event)
    if not urls:
        return

    try:
        _executor.submit(_dispatch_admin_async, event, context, urls)
    except Exception as exc:
        logger.warning("Failed to queue admin notification '%s': %s", event.value, exc)


def notify_user(user_id: int | None, event: NotificationEvent, context: NotificationContext) -> None:
    """Send a per-user notification for an event if subscribed."""
    normalized_user_id = _normalize_user_id(user_id)
    if normalized_user_id is None:
        return

    routes = _resolve_user_routes(normalized_user_id)
    urls = _resolve_route_urls_for_event(routes, event)
    if not urls:
        return

    try:
        _executor.submit(_dispatch_user_async, normalized_user_id, event, context, urls)
    except Exception as exc:
        logger.warning(
            "Failed to queue user notification '%s' for user_id=%s: %s",
            event.value,
            normalized_user_id,
            exc,
        )


def _dispatch_admin_async(event: NotificationEvent, context: NotificationContext, urls: list[str]) -> None:
    result = _send_admin_event(event, context, urls)
    if not result.get("success", False):
        logger.warning("Admin notification failed for event '%s': %s", event.value, result.get("message"))


def _dispatch_user_async(
    user_id: int,
    event: NotificationEvent,
    context: NotificationContext,
    urls: list[str],
) -> None:
    result = _send_admin_event(event, context, urls)
    if not result.get("success", False):
        logger.warning(
            "User notification failed for event '%s' (user_id=%s): %s",
            event.value,
            user_id,
            result.get("message"),
        )


def send_test_notification(urls: list[str]) -> dict[str, Any]:
    """Send a synchronous test notification to the provided URLs."""
    normalized_urls = _normalize_urls(urls)
    if not normalized_urls:
        return {"success": False, "message": "No notification URLs configured"}

    test_context = NotificationContext(
        event=NotificationEvent.REQUEST_CREATED,
        title="Shelfmark Test Notification",
        author="Shelfmark",
        username="Shelfmark",
    )
    return _send_admin_event(NotificationEvent.REQUEST_CREATED, test_context, normalized_urls)

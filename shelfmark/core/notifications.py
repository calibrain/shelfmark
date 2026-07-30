"""Apprise notification dispatch for global and per-user events."""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import parseaddr
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard
from urllib.parse import urlsplit

try:
    import apprise
except ImportError:  # pragma: no cover - exercised in tests via monkeypatch
    apprise = None  # type: ignore[assignment]

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import normalize_positive_int
from shelfmark.download.outputs.email import (
    EmailOutputError,
    _get_email_settings,
    build_email_smtp_config,
    send_email_message,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = setup_logger(__name__)

# Small pool for non-blocking dispatch. Notification sends are I/O bound and infrequent.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="Notify")
_ROUTE_EVENT_ALL = "all"
_APPRISE_APP_ID = "Shelfmark"
_APPRISE_APP_DESC = "Shelfmark notifications"
_APPRISE_LOGO_URL = (
    "https://raw.githubusercontent.com/calibrain/shelfmark/main/src/frontend/public/logo.png"
)
_APPRISE_LOGGER_NAME = "apprise"
_APPRISE_DISPATCH_ERRORS = (RuntimeError, TypeError, ValueError)


class _ApprisePluginWithUrl(Protocol):
    app_id: object

    def url(self, *, privacy: bool = False) -> str:
        _ = privacy
        return ""


class _AppriseClient(Protocol):
    asset: object

    def add(self, plugin: object) -> object: ...

    def notify(self, *, title: str, body: str, notify_type: object) -> object: ...


def _is_apprise_client(candidate: object) -> TypeGuard[_AppriseClient]:
    return callable(getattr(candidate, "add", None)) and callable(
        getattr(candidate, "notify", None)
    )


def _has_plugin_url(candidate: object) -> TypeGuard[_ApprisePluginWithUrl]:
    return callable(getattr(candidate, "url", None))


class NotificationEvent(StrEnum):
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
    book_id: int | None = None


def _normalize_urls(value: object) -> list[str]:
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
        # Strip invisible/non-ASCII characters that can sneak in via copy-paste
        # (zero-width spaces, smart quotes, non-breaking spaces, etc.).
        # These pass Apprise URL validation but cause UnicodeEncodeError when
        # requests tries to latin-1 encode credentials for Basic Auth headers.
        url = url.encode("ascii", errors="ignore").decode("ascii").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        normalized.append(url)
    return normalized


def _extract_url_schemes(urls: Iterable[str]) -> list[str]:
    schemes: list[str] = []
    seen: set[str] = set()
    for raw_url in urls:
        scheme = urlsplit(str(raw_url or "")).scheme.lower()
        if not scheme or scheme in seen:
            continue
        seen.add(scheme)
        schemes.append(scheme)
    return schemes


class _AppriseLogCapture(logging.Handler):
    def __init__(self, *, thread_id: int) -> None:
        super().__init__(level=logging.INFO)
        self.records: list[tuple[int, str, str, str]] = []
        self._thread_id = thread_id

    def emit(self, record: logging.LogRecord) -> None:
        if record.thread != self._thread_id:
            return

        message = record.getMessage()
        if message:
            exception_summary = ""
            if record.exc_info and record.exc_info[0]:
                exc_type = getattr(record.exc_info[0], "__name__", "Exception")
                exc = record.exc_info[1]
                exception_summary = f"{exc_type}: {exc}"
            elif record.exc_text:
                exception_summary = str(record.exc_text).strip()

            self.records.append((record.levelno, record.name, str(message), exception_summary))


@contextmanager
def _capture_apprise_logs(
    *, min_level: int = logging.INFO
) -> Iterator[list[tuple[int, str, str, str]]]:
    apprise_logger = logging.getLogger(_APPRISE_LOGGER_NAME)
    previous_level = apprise_logger.level
    handler = _AppriseLogCapture(thread_id=threading.get_ident())
    apprise_logger.addHandler(handler)

    if previous_level == logging.NOTSET or previous_level > min_level:
        apprise_logger.setLevel(min_level)

    try:
        yield handler.records
    finally:
        apprise_logger.removeHandler(handler)
        apprise_logger.setLevel(previous_level)


def _log_apprise_records(records: Iterable[tuple[int, str, str, str]]) -> None:
    seen: set[tuple[int, str, str, str]] = set()
    for level, source, raw_message, raw_exception_summary in records:
        message = str(raw_message or "").strip()
        source_name = str(source or "").strip() or _APPRISE_LOGGER_NAME
        exception_summary = str(raw_exception_summary or "").strip()
        key = (int(level), source_name, message, exception_summary)
        if not message or key in seen:
            continue
        seen.add(key)

        full_message = message if not exception_summary else f"{message} ({exception_summary})"

        if level >= logging.ERROR:
            logger.error("Apprise source [%s]: %s", source_name, full_message)
        elif level >= logging.WARNING:
            logger.warning("Apprise source [%s]: %s", source_name, full_message)
        else:
            logger.info("Apprise source [%s]: %s", source_name, full_message)


def _log_apprise_exception_debug(*, action: str, scheme: str, exc: Exception) -> None:
    logger.debug(
        "Apprise %s raised %s for scheme '%s': %s",
        action,
        type(exc).__name__,
        scheme,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


def _build_apprise_warning_detail(
    records: Iterable[tuple[int, str, str, str]],
    *,
    scheme: str,
) -> str | None:
    for level, source, raw_message, raw_exception_summary in records:
        if level < logging.WARNING:
            continue

        message = str(raw_message or "").strip()
        if not message:
            continue

        source_name = str(source or "").strip()
        exception_summary = str(raw_exception_summary or "").strip()
        full_message = message if not exception_summary else f"{message} ({exception_summary})"

        if source_name and source_name != _APPRISE_LOGGER_NAME:
            return f"{scheme}: {source_name}: {full_message}"
        return f"{scheme}: {full_message}"
    return None


_ADMIN_EVENTS = {
    NotificationEvent.REQUEST_CREATED,
    NotificationEvent.DOWNLOAD_COMPLETE,
    NotificationEvent.DOWNLOAD_FAILED,
}


def _normalize_admin_targets(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []

    normalized: list[dict[str, object]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()

    for row in value:
        if not isinstance(row, dict):
            continue

        transport = str(row.get("transport") or "").strip().lower()
        destination = str(row.get("destination") or "").strip()
        if transport not in {"email", "apprise"} or not destination:
            continue
        raw_events = row.get("events")
        if isinstance(raw_events, list):
            event_values = raw_events
        elif isinstance(raw_events, (tuple, set)):
            event_values = list(raw_events)
        else:
            event_values = [raw_events]

        row_events: list[str] = []
        for raw_event in event_values:
            event = str(raw_event or "").strip().lower()
            if event not in {item.value for item in _ADMIN_EVENTS}:
                continue
            if event in row_events:
                continue
            row_events.append(event)

        key = (transport, destination, tuple(row_events))
        if row_events and key not in seen:
            seen.add(key)
            normalized.append(
                {"transport": transport, "destination": destination, "events": row_events}
            )

    return normalized


def _resolve_admin_targets() -> list[dict[str, object]]:
    return _normalize_admin_targets(app_config.get("ADMIN_NOTIFICATION_TARGETS", []))


def _normalize_user_id(value: object) -> int | None:
    return normalize_positive_int(value)


def _resolve_notify_type(event: NotificationEvent) -> object:
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


def _clean_text(value: object, fallback: str) -> str:
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
        link = f"\nView book: /library/{context.book_id}" if context.book_id else ""
        return "Requested Book Available", f'"{title}" by {author} is now available.{link}'
    if event == NotificationEvent.REQUEST_REJECTED:
        note = _clean_text(context.admin_note, "")
        note_line = f"\nNote: {note}" if note else ""
        return (
            "Request Rejected",
            f'Request for "{title}" by {author} was rejected.{note_line}',
        )
    if event == NotificationEvent.DOWNLOAD_COMPLETE:
        return "Download Complete", f'"{title}" by {author} downloaded successfully.'

    error_message = _clean_text(context.error_message, "")
    error_line = f"\nError: {error_message}" if error_message else ""
    return "Download Failed", f'Failed to download "{title}" by {author}.{error_line}'


def _plugin_label(plugin: object, fallback_scheme: str) -> str:
    """Build a human-readable label from a validated Apprise plugin.

    Combines the URL scheme with the plugin's service name (app_id) and
    privacy-safe URL for richer diagnostics, e.g.
    ``"slack (Slack - slack://TokenA/To...n/To...n/)"``
    """
    parts: list[str] = [fallback_scheme]

    app_id = getattr(plugin, "app_id", None)
    if app_id and str(app_id) != fallback_scheme:
        privacy_url: str | None = None
        if _has_plugin_url(plugin):
            with suppress(Exception):
                privacy_url = plugin.url(privacy=True)

        suffix = str(app_id)
        if privacy_url:
            suffix = f"{suffix} - {privacy_url}"
        parts.append(f"({suffix})")

    return " ".join(parts)


def _apprise_proxy_env() -> dict[str, str]:
    """Build proxy env vars from app config so Apprise respects the proxy setting."""
    import os

    from shelfmark.core.config import config as _cfg

    mode = str(_cfg.get("PROXY_MODE", "") or "").lower()
    env: dict[str, str] = {}

    if mode == "http":
        http = str(_cfg.get("HTTP_PROXY", "") or "").strip()
        https = str(_cfg.get("HTTPS_PROXY", "") or "").strip() or http
        if http:
            env["HTTP_PROXY"] = http
            env["http_proxy"] = http
        if https:
            env["HTTPS_PROXY"] = https
            env["https_proxy"] = https
    elif mode == "socks5":
        socks = str(_cfg.get("SOCKS5_PROXY", "") or "").strip()
        if socks:
            env["HTTP_PROXY"] = socks
            env["http_proxy"] = socks
            env["HTTPS_PROXY"] = socks
            env["https_proxy"] = socks

    no_proxy = str(_cfg.get("NO_PROXY", "") or "").strip()
    if no_proxy and env:
        env["NO_PROXY"] = no_proxy
        env["no_proxy"] = no_proxy

    # Don't override if the user already set these in the environment directly
    return {k: v for k, v in env.items() if not os.environ.get(k)}


def _dispatch_to_apprise(
    urls: Iterable[str],
    *,
    title: str,
    body: str,
    notify_type: object,
) -> dict[str, Any]:
    import os

    normalized_urls = _normalize_urls(list(urls))
    url_schemes = _extract_url_schemes(normalized_urls)
    if not normalized_urls:
        return {"success": False, "message": "No notification URLs configured"}

    if apprise is None:
        return {"success": False, "message": "Apprise is not installed"}

    proxy_env = _apprise_proxy_env()
    if proxy_env:
        logger.debug("Applying proxy env for Apprise dispatch: %s", list(proxy_env.keys()))
        os.environ.update(proxy_env)

    valid_urls = 0
    invalid_urls = 0
    delivered_urls = 0
    failed_delivery_urls = 0
    failure_details: list[str] = []

    for url in normalized_urls:
        scheme = urlsplit(url).scheme or "unknown"
        apobj = _create_apprise_client()
        if apobj is None:
            return {"success": False, "message": "Apprise is not installed"}

        registration_failure_detail: str | None = None
        with _capture_apprise_logs(min_level=logging.INFO) as apprise_records:
            try:
                plugin = apprise.Apprise.instantiate(url, asset=getattr(apobj, "asset", None))
            except _APPRISE_DISPATCH_ERRORS as exc:
                logger.warning(
                    "Failed to register notification route URL for scheme '%s': %s",
                    scheme,
                    exc,
                )
                _log_apprise_exception_debug(
                    action="route registration",
                    scheme=scheme,
                    exc=exc,
                )
                registration_failure_detail = (
                    f"{scheme}: route registration failed ({type(exc).__name__}: {exc})"
                )
                failure_details.append(registration_failure_detail)
                plugin = None

            if plugin is None:
                invalid_urls += 1
                logger.warning("Apprise rejected notification route URL for scheme '%s'", scheme)
                _log_apprise_records(apprise_records)
                warning_detail = _build_apprise_warning_detail(apprise_records, scheme=scheme)
                if warning_detail:
                    failure_details.append(warning_detail)
                elif registration_failure_detail is None:
                    failure_details.append(f"{scheme}: route URL rejected by Apprise")
                continue

            plugin_label = _plugin_label(plugin, scheme)
            apobj.add(plugin)
            valid_urls += 1

            try:
                delivered = bool(apobj.notify(title=title, body=body, notify_type=notify_type))
            except _APPRISE_DISPATCH_ERRORS as exc:
                _log_apprise_records(apprise_records)
                failed_delivery_urls += 1
                logger.warning(
                    "Apprise notify raised %s for %s: %s",
                    type(exc).__name__,
                    plugin_label,
                    exc,
                )
                _log_apprise_exception_debug(action="notify", scheme=scheme, exc=exc)
                warning_detail = _build_apprise_warning_detail(apprise_records, scheme=scheme)
                if warning_detail:
                    failure_details.append(warning_detail)
                else:
                    failure_details.append(f"{scheme}: notify raised {type(exc).__name__}: {exc}")
                continue

        _log_apprise_records(apprise_records)
        if delivered:
            delivered_urls += 1
            logger.debug("Notification delivered via %s", plugin_label)
            continue

        failed_delivery_urls += 1
        logger.warning("Apprise notify returned False for %s", plugin_label)
        warning_detail = _build_apprise_warning_detail(apprise_records, scheme=scheme)
        if warning_detail:
            failure_details.append(warning_detail)
        else:
            failure_details.append(f"{scheme}: delivery failed")

    scheme_summary = ", ".join(url_schemes) if url_schemes else "unknown"
    if valid_urls == 0:
        logger.warning(
            "No valid Apprise notification routes after registration for scheme(s): %s",
            scheme_summary,
        )
        result: dict[str, Any] = {
            "success": False,
            "message": "No valid notification URLs configured",
        }
        if failure_details:
            result["details"] = failure_details
        return result

    if delivered_urls == 0:
        logger.warning(
            (
                "Apprise notify returned False for scheme(s): %s "
                "(valid_urls=%s invalid_urls=%s failed_deliveries=%s)"
            ),
            scheme_summary,
            valid_urls,
            invalid_urls,
            failed_delivery_urls,
        )
        result = {"success": False, "message": "Notification delivery failed"}
        if failure_details:
            result["details"] = failure_details
        return result

    message = f"Notification sent to {delivered_urls} URL(s)"
    failed_urls = invalid_urls + failed_delivery_urls
    if failed_urls:
        message += f" ({failed_urls} URL(s) failed)"
    result = {"success": True, "message": message}
    if failure_details:
        result["details"] = failure_details
    return result


def _create_apprise_client() -> _AppriseClient | None:
    if apprise is None:
        return None

    apprise_cls = getattr(apprise, "Apprise", None)
    if apprise_cls is None:
        return None

    apprise_asset_cls = getattr(apprise, "AppriseAsset", None)
    if apprise_asset_cls is None:
        client = apprise_cls()
        return client if _is_apprise_client(client) else None

    try:
        asset = apprise_asset_cls(
            app_id=_APPRISE_APP_ID,
            app_desc=_APPRISE_APP_DESC,
            image_url_logo=_APPRISE_LOGO_URL,
        )
    except TypeError:
        # Support older Apprise versions that do not expose image_url_logo.
        try:
            asset = apprise_asset_cls(
                app_id=_APPRISE_APP_ID,
                app_desc=_APPRISE_APP_DESC,
            )
        except TypeError:
            client = apprise_cls()
            return client if _is_apprise_client(client) else None

    try:
        client = apprise_cls(asset=asset)
    except TypeError:
        client = apprise_cls()
    return client if _is_apprise_client(client) else None


def _send_apprise_event(
    event: NotificationEvent, context: NotificationContext, urls: list[str]
) -> dict[str, Any]:
    title, body = _render_message(context)
    notify_type = _resolve_notify_type(event)
    return _dispatch_to_apprise(urls, title=title, body=body, notify_type=notify_type)


def notify_admin(event: NotificationEvent, context: NotificationContext) -> None:
    """Send a global admin notification for an event if subscribed."""
    if event not in _ADMIN_EVENTS:
        return
    for target in _resolve_admin_targets():
        events = target["events"]
        if not isinstance(events, list) or event.value not in events:
            continue
        _submit_delivery(target["transport"], target["destination"], event, context, None)


def notify_user(
    user_db: Any, user_id: int | None, event: NotificationEvent, context: NotificationContext
) -> None:
    """Send the only personal events to the user's saved active destination."""
    if event not in {NotificationEvent.REQUEST_FULFILLED, NotificationEvent.REQUEST_REJECTED}:
        return
    normalized_user_id = _normalize_user_id(user_id)
    if normalized_user_id is None:
        return

    preferences = user_db.get_personal_preferences(normalized_user_id)
    user = user_db.get_user(user_id=normalized_user_id)
    if not preferences["notifications_enabled"]:
        return
    transport = preferences.get("notification_transport")
    destination = preferences.get("notification_destination")
    if transport == "apprise" and isinstance(destination, str):
        _submit_delivery(transport, destination, event, context, normalized_user_id)
        return
    if isinstance(user, dict) and isinstance(user.get("email"), str):
        _submit_delivery("email", user["email"], event, context, normalized_user_id)


def _dispatch_async(
    transport: str,
    destination: str,
    event: NotificationEvent,
    context: NotificationContext,
    user_id: int | None,
) -> None:
    result = _deliver(transport, destination, event, context)
    if not result.get("success", False):
        logger.warning(
            "Notification failed for event '%s' (user_id=%s): %s",
            event.value,
            user_id,
            result.get("message"),
        )


def _submit_delivery(
    transport: object,
    destination: object,
    event: NotificationEvent,
    context: NotificationContext,
    user_id: int | None,
) -> None:
    try:
        _executor.submit(_dispatch_async, str(transport), str(destination), event, context, user_id)
    except RuntimeError as exc:
        logger.warning("Failed to queue notification '%s': %s", event.value, exc)


def is_valid_email_destination(destination: str) -> bool:
    return bool(parseaddr(destination)[1]) and "@" in parseaddr(destination)[1]


def is_valid_apprise_destination(destination: str) -> bool:
    return (
        bool(destination.strip()) and bool(urlsplit(destination).scheme) and " " not in destination
    )


def _deliver(
    transport: str, destination: str, event: NotificationEvent, context: NotificationContext
) -> dict[str, Any]:
    title, body = _render_message(context)
    if transport == "apprise":
        return _send_apprise_event(event, context, [destination])
    if transport != "email" or not is_valid_email_destination(destination):
        return {"success": False, "message": "Invalid notification destination"}
    try:
        message = EmailMessage()
        message["To"] = destination
        message["Subject"] = title
        message.set_content(body)
        send_email_message(build_email_smtp_config(_get_email_settings()), message)
    except EmailOutputError as exc:
        return {"success": False, "message": str(exc)}
    return {"success": True, "message": "Notification sent"}


def send_personal_test_notification(user_db: Any, user_id: int) -> dict[str, Any]:
    preferences = user_db.get_personal_preferences(user_id)
    user = user_db.get_user(user_id=user_id)
    transport = preferences.get("notification_transport")
    destination = preferences.get("notification_destination")
    if not preferences.get("notifications_enabled"):
        return {
            "success": False,
            "message": "Enable personal notifications with a valid destination first",
        }
    if transport == "apprise":
        valid = isinstance(destination, str) and is_valid_apprise_destination(destination)
        target_transport, target_destination = transport, destination
    else:
        email = user.get("email") if isinstance(user, dict) else None
        valid = isinstance(email, str) and is_valid_email_destination(email)
        target_transport, target_destination = "email", email
    if not valid:
        return {
            "success": False,
            "message": "Enable personal notifications with a valid destination first",
        }
    if not isinstance(target_destination, str):
        return {
            "success": False,
            "message": "Enable personal notifications with a valid destination first",
        }
    return _deliver(
        target_transport,
        target_destination,
        NotificationEvent.REQUEST_CREATED,
        NotificationContext(
            event=NotificationEvent.REQUEST_CREATED,
            title="Shelfmark Test Notification",
            author="Shelfmark",
            username="Shelfmark",
        ),
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
    return _send_apprise_event(NotificationEvent.REQUEST_CREATED, test_context, normalized_urls)

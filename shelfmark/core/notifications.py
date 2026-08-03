"""Apprise notification dispatch for global and per-user events."""

from __future__ import annotations

import html
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

# Representative book shown in the test notification so the full email template
# (book card, cover placeholder, chips, description, library link) can be
# previewed. Real notifications use the requested book's stored Hardcover data.
_SAMPLE_BOOK: dict[str, Any] = {
    "id": None,
    "title": "The Lighthouse Keeper's Daughter",
    "author": "Jane Doe",
    "subtitle": "A sweeping tale of the sea",
    "publish_year": 2024,
    "series_name": None,
    "series_position": None,
    "language": "English",
    "isbn_13": "9781234567890",
    "cover_url": None,
    "metadata_json": {
        "description": (
            "When a lighthouse keeper's daughter discovers a washed-up journal, "
            "she is pulled into a century-old mystery that would change her "
            "coastal village forever. This sample book is shown to preview how "
            "Shelfmark notification emails look; real notifications show the "
            "requested book's details from Hardcover."
        ),
        "display_fields": [
            {"label": "Rating", "value": "4.6 (1,204)", "icon": "star"},
            {"label": "Readers", "value": "3,215", "icon": "users"},
        ],
    },
}


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
    book: dict[str, Any] | None = None
    is_test: bool = False


def _notification_public_base() -> str:
    """Return the configured public base URL (``scheme://host``) or empty string."""
    from shelfmark.core.utils import normalize_http_url, normalize_optional_text

    raw = normalize_optional_text(app_config.get("NOTIFICATION_BASE_URL", ""))
    if not raw:
        return ""
    return normalize_http_url(raw, default_scheme="https", strip_trailing_slash=True)


def _notification_base_path() -> str:
    from shelfmark.core.utils import normalize_base_path, normalize_optional_text

    return normalize_base_path(normalize_optional_text(app_config.get("URL_BASE", "")))


def _build_book_url(book_id: object) -> str:
    """Build the library link for a book, absolute when a base URL is set."""
    normalized_id = normalize_positive_int(book_id)
    if normalized_id is None:
        return ""
    path = f"{_notification_base_path()}/library/{normalized_id}"
    base = _notification_public_base()
    return f"{base}{path}" if base else path


def _build_library_home_url() -> str:
    """Build the library home link (used by the test email as a dead-link-safe CTA)."""
    path = f"{_notification_base_path()}/library"
    base = _notification_public_base()
    return f"{base}{path}" if base else path


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

    if context.is_test:
        return "Shelfmark Test Notification", "This is a test notification from Shelfmark."
    if event == NotificationEvent.REQUEST_CREATED:
        return "New Request", f'{username} requested "{title}" by {author}'
    if event == NotificationEvent.REQUEST_FULFILLED:
        link = _build_book_url(context.book_id)
        link_line = f"\nView book: {link}" if link else ""
        return "Requested Book Available", f'"{title}" by {author} is now available.{link_line}'
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


def _html_escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _html_action_copy(context: NotificationContext) -> tuple[str, str]:
    """Return (hero, detail) copy for the HTML template.

    One template serves every notification action: the hero message and detail
    copy change per event while the book card, cover, and library link stay the
    same. The test email uses the same template with its own message/subject.
    """
    event = context.event
    title = _clean_text(context.title, "Unknown title")
    author = _clean_text(context.author, "Unknown author")
    username = _clean_text(context.username, "A user")

    if context.is_test:
        return (
            "Shelfmark test email",
            "This test notification confirms your Shelfmark notification settings "
            "are working. A real notification would show the requested book here.",
        )
    if event == NotificationEvent.REQUEST_CREATED:
        return "New request submitted", f'{username} requested "{title}" by {author}.'
    if event == NotificationEvent.REQUEST_FULFILLED:
        return (
            "Your requested book is ready",
            f'"{title}" by {author} is now available in your library.',
        )
    if event == NotificationEvent.REQUEST_REJECTED:
        note = _clean_text(context.admin_note, "")
        detail = f'Request for "{title}" by {author} was declined.'
        if note:
            detail += f' The administrator left this note: "{note}".'
        return "Request declined", detail
    if event == NotificationEvent.DOWNLOAD_COMPLETE:
        return "Download complete", f'"{title}" by {author} downloaded successfully.'

    error_message = _clean_text(context.error_message, "")
    detail = f'Failed to download "{title}" by {author}.'
    if error_message:
        detail += f" Error: {error_message}"
    return "Download failed", detail


def _resolve_email_cover_url(cover_url: object) -> str | None:
    """Return an absolute cover URL for email clients, or None to use a placeholder.

    Stored covers are either absolute (external host) or relative proxy paths
    (cover caching enabled). Relative paths are only usable in email when a
    public base URL is configured.
    """
    raw = str(cover_url or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        base = _notification_public_base()
        return f"{base}{raw}" if base else None
    return raw


def _html_cta_button(url: str, title: object, label: str = "View in Library") -> str:
    return (
        '<div style="margin-top:26px;text-align:center;">'
        f'<a href="{_html_escape(url)}" '
        'style="display:inline-block;background:#7c3aed;color:#ffffff;padding:13px 28px;'
        'border-radius:8px;font-size:15px;font-weight:600;text-decoration:none;'
        f'text-align:center;">{_html_escape(label)}</a>'
        '<p style="margin:10px 0 0;font-size:12px;color:#9ca3af;">'
        f'{_html_escape(title)} — opens in your Shelfmark library</p>'
        "</div>"
    )


def _html_footer_link(url: str) -> str:
    """A plain clickable fallback link for clients that strip rich CTA buttons."""
    return (
        '<p style="margin:8px 0 0;font-size:12px;color:#9ca3af;">'
        f'<a href="{_html_escape(url)}" '
        f'style="color:#7c3aed;text-decoration:underline;">{_html_escape(url)}</a></p>'
    )


def _html_book_card(context: NotificationContext, cta_url: str) -> str:
    """Build the book card section (cover, title block, chips, description)."""
    book = context.book if isinstance(context.book, dict) else None
    if book is None:
        return (
            '<p style="margin:0;text-align:center;font-size:15px;color:#4b5563;'
            f'line-height:1.6;">{_html_escape(_html_action_copy(context)[1])}</p>'
        )

    cover_url = _resolve_email_cover_url(book.get("cover_url"))
    if cover_url:
        cover_html = (
            f'<img src="{_html_escape(cover_url)}" alt="{_html_escape(book.get("title"))}" '
            'style="width:112px;height:168px;object-fit:cover;border-radius:10px;'
            'display:block;box-shadow:0 4px 14px rgba(0,0,0,0.14);flex-shrink:0;" />'
        )
    else:
        cover_html = (
            '<div style="width:112px;height:168px;border-radius:10px;flex-shrink:0;'
            'background:#ede9fe;display:flex;align-items:center;justify-content:center;'
            'color:#7c3aed;font-size:12px;font-weight:600;letter-spacing:0.05em;">'
            "No cover</div>"
        )

    title = _html_escape(book.get("title") or context.title or "Unknown title")
    subtitle = _html_escape(book.get("subtitle"))
    author = _html_escape(book.get("author") or context.author or "Unknown author")

    meta_parts: list[str] = []
    if book.get("publish_year"):
        meta_parts.append(_html_escape(book["publish_year"]))
    series = book.get("series_name")
    if series:
        series_label = str(series)
        if book.get("series_position") is not None:
            series_label += f" #{book['series_position']}"
        meta_parts.append(_html_escape(series_label))
    if book.get("language"):
        meta_parts.append(_html_escape(str(book["language"]).upper()))
    if book.get("isbn_13"):
        meta_parts.append(_html_escape(f"ISBN {book['isbn_13']}"))
    meta_html = (
        f'<p style="margin:10px 0 0;font-size:12px;color:#6b7280;">{" · ".join(meta_parts)}</p>'
        if meta_parts
        else ""
    )

    metadata_json = book.get("metadata_json") or {}
    display_fields = (
        metadata_json.get("display_fields")
        if isinstance(metadata_json, dict) and isinstance(metadata_json.get("display_fields"), list)
        else []
    )
    chips: list[str] = []
    for field in display_fields[:3]:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or "").strip()
        value = str(field.get("value") or "").strip()
        if label and value:
            chips.append(
                '<span style="display:inline-block;background:#f5f3ff;color:#6d28d9;'
                'border-radius:8px;padding:5px 10px;font-size:12px;font-weight:600;'
                f'margin:0 6px 6px 0;">{_html_escape(label)}: {_html_escape(value)}</span>'
            )
    chips_html = f'<div style="margin-top:14px;">{"".join(chips)}</div>' if chips else ""

    info_html = (
        '<div style="min-width:0;flex:1;">'
        f'<h2 style="margin:0;font-size:22px;font-weight:700;color:#111827;line-height:1.3;">{title}</h2>'
        + (f'<p style="margin:4px 0 0;font-size:15px;color:#6b7280;line-height:1.4;">{subtitle}</p>' if subtitle else "")
        + f'<p style="margin:8px 0 0;font-size:13px;font-weight:600;color:#374151;">{author}</p>'
        + meta_html
        + chips_html
        + "</div>"
    )

    description = (
        metadata_json.get("description")
        if isinstance(metadata_json, dict)
        else None
    )
    description_html = ""
    if description:
        description_text = str(description).strip()
        if len(description_text) > 420:
            description_text = description_text[:420].rstrip() + "…"
        description_html = (
            '<div style="margin-top:26px;padding-top:22px;border-top:1px solid #f3f4f6;">'
            '<p style="margin:0;font-size:12px;font-weight:600;letter-spacing:0.06em;'
            'text-transform:uppercase;color:#6b7280;">About this book</p>'
            f'<p style="margin:8px 0 0;font-size:14px;line-height:1.7;color:#4b5563;">'
            f"{_html_escape(description_text)}</p>"
            "</div>"
        )

    cta_html = _html_cta_button(cta_url, book.get("title")) if cta_url else ""

    # Two-column table (not flexbox) so cover/text spacing renders reliably in
    # every email client; email clients do not consistently honor flex ``gap``.
    card_header = (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="width:100%;"><tr>'
        '<td style="vertical-align:top;padding-right:28px;width:112px;max-width:112px;">'
        + cover_html
        + "</td><td style=\"vertical-align:top;\">"
        + info_html
        + "</td></tr></table>"
    )
    return card_header + description_html + cta_html


def _render_html_email(context: NotificationContext) -> str:
    """Render the shared HTML notification email template.

    One template serves every notification action and the test email; only the
    hero message, detail copy, and subject differ between events. The book card
    (cover, title, author, series, year, description) and library link are the
    same for every book-related action.
    """
    hero, detail = _html_action_copy(context)
    cta_url = _build_book_url(context.book_id)
    if not cta_url and context.is_test:
        cta_url = _build_library_home_url()

    header_detail = detail
    if cta_url and not (context.book and isinstance(context.book, dict)):
        header_detail = f"{detail} Open it at {cta_url}."

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<meta name="x-apple-disable-message-reformatting" />
<title>{_html_escape(hero)}</title>
</head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{_html_escape(detail)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;">
<tr>
<td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
<tr>
<td style="background:#5b21b6;padding:26px 32px;">
<p style="margin:0;font-size:12px;font-weight:700;letter-spacing:0.14em;text-transform:uppercase;color:#c4b5fd;">Shelfmark</p>
<h1 style="margin:8px 0 0;font-size:22px;font-weight:700;color:#ffffff;line-height:1.3;">{_html_escape(hero)}</h1>
<p style="margin:8px 0 0;font-size:14px;color:#ddd6fe;line-height:1.5;">{_html_escape(header_detail)}</p>
</td>
</tr>
<tr>
<td style="padding:28px 32px;">
{_html_book_card(context, cta_url)}
</td>
</tr>
<tr>
<td style="background:#f9fafb;padding:18px 32px;">
<p style="margin:0;font-size:12px;color:#9ca3af;line-height:1.5;">
Sent by <strong>Shelfmark</strong>. Reply to this email is not monitored.
</p>
{_html_footer_link(cta_url) if cta_url else ""}
</td>
</tr>
</table>
</td>
</tr>
</table>
</body>
</html>
"""


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
        smtp_config = build_email_smtp_config(_get_email_settings())
        message = EmailMessage()
        message["From"] = smtp_config.from_addr
        message["To"] = destination
        message["Subject"] = title
        message.set_content(body)
        message.add_alternative(_render_html_email(context), subtype="html")
        send_email_message(smtp_config, message)
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
            is_test=True,
            book=dict(_SAMPLE_BOOK),
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
        is_test=True,
        book=dict(_SAMPLE_BOOK),
    )
    return _send_apprise_event(NotificationEvent.REQUEST_CREATED, test_context, normalized_urls)

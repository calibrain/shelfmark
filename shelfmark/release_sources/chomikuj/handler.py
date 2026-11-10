"""Chomikuj.pl download handler.

Resolves a Chomikuj file id to a one-shot download URL and streams the body into
``TMP_DIR``. Two steps are deliberately separate:

* the **preflight** logs in, reads the account, and asks the pure quota module
  whether this file may be fetched. Chomikuj charges transfer when
  ``files/download`` issues the URL, not when the bytes arrive, so a denied
  preflight must never call it.
* the **transfer** asks for the URL exactly once, then streams with the exact
  live-proven mobile API headers: the mobile ``User-Agent`` and ``Accept: */*``
  with no ``Range``. A ranged request makes the CDN answer ``206 text/html``
  instead of the PDF, so Shelfmark's generic Chrome download headers are
  intentionally not used here.

Retry context is source-private: the queue persists the file id, size, and
free/paid flag so a restart can re-resolve without re-searching. Error messages
and log lines never carry the API key or the account password.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import build_filename, resolve_staged_path
from shelfmark.core.redaction import redact_secrets
from shelfmark.core.request_helpers import coerce_bool
from shelfmark.download import network
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.chomikuj.client import (
    DEFAULT_USER_AGENT,
    ChomikujClient,
    ChomikujError,
)
from shelfmark.release_sources.chomikuj.quota import QuotaDecision, decide_download
from shelfmark.release_sources.chomikuj.session import get_session
from shelfmark.release_sources.chomikuj.source import get_base_url, get_credentials

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

# Same contract as the other direct handlers: anything smaller is an error page.
_MIN_VALID_FILE_SIZE = 10 * 1024
_CHUNK_SIZE = 256 * 1024
_DOWNLOAD_TIMEOUT = (10, 60)
_STREAM_HEADERS = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}

_INVALID_FILE_ID_MESSAGE = "Chomikuj: invalid file id"
_HTML_RESPONSE_MESSAGE = "Chomikuj returned an HTML page instead of a book"
_INVALID_FILE_MESSAGE = "Chomikuj returned an invalid file"
_NOT_ENOUGH_TRANSFER_MESSAGE = "Chomikuj: not enough transfer"
_GENERIC_FAILURE_MESSAGE = "Chomikuj download failed"

_TRANSFER_CODE = 605
_C0_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_EXTRA_CONTEXT_KEYS = (
    "file_name",
    "file_type",
    "is_free_for_user",
    "format",
    "author",
    "language",
    "access",
)


class _HtmlResponseError(Exception):
    """The server answered with an HTML page instead of a file body."""


def _normalize_file_id(value: object) -> str | None:
    """Return a positive ASCII decimal file id as a string, or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text.isascii() or not text.isdigit():
        return None
    return text if int(text) > 0 else None


def _positive_number(value: object) -> float:
    """Coerce a progress total, treating unusable values as unknown (0)."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, int | float):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return 0.0
    else:
        return 0.0
    return number if number > 0 else 0.0


def _sanitize_response_name(value: object) -> str:
    """Make a server-provided file name a direct child name.

    Both separators become underscores and C0 controls are removed. The result
    still goes through :func:`resolve_staged_path`, which refuses ``..`` and any
    other name that would leave ``TMP_DIR``.
    """
    if not isinstance(value, str):
        return ""
    flattened = value.replace("/", "_").replace("\\", "_")
    return _C0_CONTROL_RE.sub("", flattened).strip()


def _staging_name(task: DownloadTask, file_id: str, payload: dict[str, object]) -> str:
    """Choose the staging file name for this download."""
    if config.get("FILE_ORGANIZATION", "rename") != "none":
        return build_filename(task.title, task.author, task.year, task.format)

    name = _sanitize_response_name(payload.get("Name"))
    if name:
        return name
    if file_id.isascii() and file_id.isdigit():
        return f"{file_id}.{task.format or 'bin'}"
    msg = "Chomikuj did not return a file name"
    raise ValueError(msg)


def _safe_error(exc: BaseException, *secrets: str) -> str:
    """Render an exception for logs and status without credentials or links."""
    text = redact_secrets(str(exc)).strip()
    for secret in secrets:
        if secret:
            text = text.replace(secret, "(removed)")
    return text or _GENERIC_FAILURE_MESSAGE


def _file_url_from(payload: dict[str, object]) -> str:
    """Return the download payload's ``FileUrl`` or raise a clean ``ValueError``."""
    file_url = payload.get("FileUrl")
    if not isinstance(file_url, str) or not file_url.strip():
        msg = "Chomikuj did not return a download URL"
        raise ValueError(msg)
    return file_url


def _stream_file(
    url: str,
    target: Path,
    *,
    expected_size: object,
    cancel_flag: Event,
    progress_callback: Callable[[float], None],
) -> bool:
    """Stream ``url`` into ``target``; return False when cancelled midway.

    The request headers are the live-proven Chomikuj contract and must stay
    exactly the mobile API ``User-Agent`` / ``Accept: */*``. An HTML body is
    refused before ``target`` is opened, so no partial file is created for it.
    """
    with requests.get(
        url,
        headers=_STREAM_HEADERS,
        stream=True,
        timeout=_DOWNLOAD_TIMEOUT,
        proxies=network.get_proxies(url),
        verify=network.get_ssl_verify(url),
    ) as response:
        response.raise_for_status()

        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            raise _HtmlResponseError(_HTML_RESPONSE_MESSAGE)

        total = _positive_number(response.headers.get("Content-Length")) or _positive_number(
            expected_size
        )
        written = 0
        with target.open("wb") as file:
            for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                if cancel_flag.is_set():
                    return False
                if not chunk:
                    continue
                file.write(chunk)
                written += len(chunk)
                if total:
                    progress_callback(min(100.0, written * 100 / total))

    progress_callback(100.0)
    return True


def _is_unlimited(account: object) -> bool:
    return isinstance(account, dict) and account.get("HasUnlimitedTransfer") is True


@register_handler("chomikuj")
class ChomikujHandler(DownloadHandler):
    """Download handler for Chomikuj.pl releases."""

    def build_retry_resolution_fields(self, release_data: dict[str, object]) -> dict[str, object]:
        """Persist the source-private context a restart needs to re-resolve.

        Only ``retry_source_context`` is returned: the generic orchestrator
        keeps its own ``retry_download_url``/``retry_download_protocol`` values
        because a Chomikuj URL is one-shot.
        """
        extra = release_data.get("extra")
        if not isinstance(extra, dict):
            extra = {}

        context: dict[str, object] = {}
        size_bytes = release_data.get("size_bytes")
        if size_bytes is None:
            size_bytes = extra.get("size_bytes")
        if size_bytes is not None:
            context["size_bytes"] = size_bytes

        file_id = _normalize_file_id(extra.get("file_id"))
        if file_id is not None:
            context["file_id"] = file_id

        for key in _EXTRA_CONTEXT_KEYS:
            value = extra.get(key)
            if value is not None:
                context[key] = value

        return {"retry_source_context": context}

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Run the quota preflight, resolve once, and stream into ``TMP_DIR``."""
        file_id = self._resolve_file_id(task)
        if file_id is None:
            status_callback("error", _INVALID_FILE_ID_MESSAGE)
            return None

        book_path: Path | None = None
        api_key = ""
        password = ""
        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            status_callback("resolving", "Signing in to Chomikuj")
            username, password = get_credentials()
            base_url = get_base_url()
            client = ChomikujClient(base_url)
            session = get_session(
                client,
                username=username,
                password=password,
                base_url=base_url,
            )
            api_key = session.api_key
            account = client.account_info(api_key)

            context = task.retry_source_context
            size_bytes = context.get("size_bytes")
            is_free_for_user = context.get("is_free_for_user") is True
            decision: QuotaDecision = decide_download(
                file={
                    "size_bytes": size_bytes,
                    "is_free_for_user": is_free_for_user,
                },
                account=account,
                allow_transfer_spend=coerce_bool(
                    config.get("CHOMIKUJ_ALLOW_TRANSFER_SPEND", True),
                    default=True,
                ),
            )
            if not decision.allowed:
                status_callback("error", decision.reason)
                return None

            if not is_free_for_user and not _is_unlimited(account):
                status_callback("resolving", "Spending Chomikuj transfer for a paid file")

            status_callback("resolving", "Resolving Chomikuj download")
            payload = client.download_url(api_key, file_id)
            file_url = _file_url_from(payload)

            name = _staging_name(task, file_id, payload)
            book_path = resolve_staged_path(TMP_DIR, name)

            # An empty message clears "Resolving..." from the queue.
            status_callback("downloading", "")
            if not _stream_file(
                file_url,
                book_path,
                expected_size=size_bytes,
                cancel_flag=cancel_flag,
                progress_callback=progress_callback,
            ):
                book_path.unlink(missing_ok=True)
                status_callback("cancelled", "Cancelled")
                return None

            if book_path.stat().st_size < _MIN_VALID_FILE_SIZE:
                book_path.unlink(missing_ok=True)
                status_callback("error", _INVALID_FILE_MESSAGE)
                return None
            return str(book_path)

        except _HtmlResponseError:
            self._cleanup(book_path)
            logger.warning("Chomikuj download failed for %s: html response", file_id)
            status_callback("error", _HTML_RESPONSE_MESSAGE)
            return None
        except ChomikujError as exc:
            self._cleanup(book_path)
            if str(exc.code) == str(_TRANSFER_CODE):
                logger.warning("Chomikuj download failed for %s: not enough transfer", file_id)
                status_callback("error", _NOT_ENOUGH_TRANSFER_MESSAGE)
            elif cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
            else:
                self._report_failure(exc, file_id, api_key, password, status_callback)
            return None
        except (requests.exceptions.RequestException, OSError, ValueError) as exc:
            self._cleanup(book_path)
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
            else:
                self._report_failure(exc, file_id, api_key, password, status_callback)
            return None
        except Exception as exc:  # noqa: BLE001 - the orchestrator must never see a raise
            self._cleanup(book_path)
            self._report_failure(exc, file_id, api_key, password, status_callback)
            return None

    @staticmethod
    def _resolve_file_id(task: DownloadTask) -> str | None:
        context = getattr(task, "retry_source_context", None)
        if not isinstance(context, dict):
            context = {}
        if "file_id" in context:
            return _normalize_file_id(context.get("file_id"))
        return _normalize_file_id(task.task_id.split(":", 1)[-1])

    @staticmethod
    def _cleanup(book_path: Path | None) -> None:
        if book_path is not None:
            book_path.unlink(missing_ok=True)

    @staticmethod
    def _report_failure(
        exc: BaseException,
        file_id: str,
        api_key: str,
        password: str,
        status_callback: Callable[[str, str | None], None],
    ) -> None:
        message = _safe_error(exc, api_key, password)
        logger.warning("Chomikuj download failed for %s: %s", file_id, message)
        status_callback("error", message)

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled by the orchestrator via the cancel flag."""
        return False

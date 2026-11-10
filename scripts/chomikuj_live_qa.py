#!/usr/bin/env python3
"""Owner-run live QA gate for the Chomikuj.pl release source.

Offline self-test::

    uv run python scripts/chomikuj_live_qa.py --self-test

Owner-run live check::

    CHOMIKUJ_USERNAME='your-account' CHOMIKUJ_PASSWORD='your-password' \\
        uv run python scripts/chomikuj_live_qa.py

Environment:

``CHOMIKUJ_USERNAME``
    Chomikuj account name or email. Required for the live check.
``CHOMIKUJ_PASSWORD``
    Chomikuj password. Required for the live check.
``CHOMIKUJ_QUERY``
    Search text. Defaults to ``test``.
``CHOMIKUJ_TEST_FILE_ID``
    Optional numeric ``FileId``; when set, the search step is skipped and this
    file is used for the free-download probe.
``CHOMIKUJ_TEST_PAID_FILE_ID``
    Optional numeric paid ``FileId`` used by the spend-off proof. When unset, a
    synthetic paid file id is used.

Exit codes:

``0``
    ``LIVE_QA=PASS``
``1``
    ``LIVE_QA=FAIL`` or an unexpected error
``2``
    ``CHOMIKUJ_USERNAME`` / ``CHOMIKUJ_PASSWORD`` missing. No network call is
    made and no evidence file is written.

LOG_ROOT handling: if the default ``LOG_ROOT`` (``/var/log``) is not writable
when this script starts, it points ``LOG_ROOT`` at a fresh directory under the
system temp dir **before** importing any Shelfmark module. That keeps the
logger from failing with a permission error on a workstation. An explicitly
configured ``LOG_ROOT`` and a container where ``/var/log`` is writable are left
untouched.

The live path logs in, reads ``account/info``, widens the source's supported
formats to the Chomikuj importable set for its own search (Shelfmark's default
formats exclude ``pdf``), searches through the real ``ChomikujSource``, probes
the returned ``FileUrl`` once with the live-proven mobile API headers and no
``Range``, runs ``ChomikujHandler``, and finally proves that a paid file is
refused before ``files/download`` when transfer spending is disabled. The
username, password, API key, account id/name, and every URL are redacted from
stdout and from ``.omo/evidence/chomikuj/live-qa.txt``.
"""

from __future__ import annotations

import io
import logging
import os
import re
import sys
import tempfile
import threading
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from shelfmark.core.models import DownloadTask
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources import Release

_DEFAULT_LOG_ROOT = Path("/var/log")
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

REDACTED = "<redacted>"
DEFAULT_QUERY = "test"
PROBE_HEADERS = {"User-Agent": "android/3.8.4 (python; python)", "Accept": "*/*"}
MIN_DOWNLOAD_BYTES = 10 * 1024
DEFAULT_TIMEOUT = 30.0

EVIDENCE_DIR = _PROJECT_ROOT / ".omo" / "evidence" / "chomikuj"
EVIDENCE_FILE = EVIDENCE_DIR / "live-qa.txt"

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# The exact vectors proven against the live service (scripts/live-spike.mjs).
# Each entry is (path, query, body, digest).
TOKEN_VECTORS: tuple[tuple[str, str, str, str], ...] = (
    ("api/v3/account/info", "", "", "22dbb80e516477793934fa0add1b8929"),
    (
        "api/v3/files/search",
        "Query=test&PageNumber=1&MediaType=Documents&Extension=pdf",
        "",
        "83b420ec92a611e58b884b89e68ac530",
    ),
    ("api/v3/files/download", "FileId=123456", "", "bfdebdece4cef4a17ebe44808ab4cda4"),
    (
        "api/v3/account/login",
        "",
        '{"AccountName":"alice","Password":"p@ss"}',
        "9f78082445ecf973cb41180065a0fc50",
    ),
    (
        "api/v3/account/login",
        "",
        '{"AccountName":"żółć","Password":"päss"}',
        "801e16c63c58dede556adfa87c78476e",
    ),
)


def _ensure_writable_log_root() -> None:
    """Move ``LOG_ROOT`` to a temp dir when the default ``/var/log`` is not writable.

    Called at import time, before any Shelfmark module (and therefore the
    logger) is imported. An explicit ``LOG_ROOT`` is never overridden, and a
    writable ``/var/log`` is left alone.
    """
    if "LOG_ROOT" in os.environ:
        return

    try:
        _DEFAULT_LOG_ROOT.mkdir(parents=True, exist_ok=True)
        writable = os.access(_DEFAULT_LOG_ROOT, os.W_OK)
    except OSError:
        writable = False
    if writable:
        return

    fallback = Path(tempfile.gettempdir()) / "shelfmark-live-qa-logs"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    os.environ["LOG_ROOT"] = str(fallback)


_ensure_writable_log_root()


@dataclass(frozen=True)
class EnvironmentCheck:
    """Result of reading and validating the live-run environment."""

    ok: bool
    exit_code: int
    message: str
    username: str = ""
    password: str = ""
    query: str = DEFAULT_QUERY
    test_file_id: str = ""
    paid_file_id: str = ""


@dataclass(frozen=True)
class ProbeResult:
    """One live ``FileUrl`` probe, with no URL retained."""

    status: int
    content_type: str
    bytes_read: int


def _text(value: object) -> str:
    """Return a trimmed string for a string value, else an empty string."""
    return value.strip() if isinstance(value, str) else ""


def _missing_credentials_message(missing: list[str]) -> str:
    names = ", ".join(missing)
    return "\n".join(
        [
            f"Missing required environment variable(s): {names}",
            "",
            "This is the owner-run live QA gate; it logs in to Chomikuj with a real account.",
            "Exit code 2 means no credentials were provided and no network calls were made.",
            "",
            "Run:",
            "  CHOMIKUJ_USERNAME='your-account' CHOMIKUJ_PASSWORD='your-password' "
            "uv run python scripts/chomikuj_live_qa.py",
            "",
            "Optional: CHOMIKUJ_QUERY=<search text> (default: test).",
            "Optional: CHOMIKUJ_TEST_FILE_ID=<numeric FileId>.",
            "Optional: CHOMIKUJ_TEST_PAID_FILE_ID=<numeric FileId> for the spend-off proof.",
        ]
    )


def check_environment(env: Mapping[str, str] | None = None) -> EnvironmentCheck:
    """Validate the live-run environment without echoing any supplied value."""
    values = os.environ if env is None else env
    username = _text(values.get("CHOMIKUJ_USERNAME"))
    password = values.get("CHOMIKUJ_PASSWORD")
    password = password if isinstance(password, str) else ""
    query = _text(values.get("CHOMIKUJ_QUERY")) or DEFAULT_QUERY
    test_file_id = _text(values.get("CHOMIKUJ_TEST_FILE_ID"))
    paid_file_id = _text(values.get("CHOMIKUJ_TEST_PAID_FILE_ID"))

    for variable, value in (
        ("CHOMIKUJ_TEST_FILE_ID", test_file_id),
        ("CHOMIKUJ_TEST_PAID_FILE_ID", paid_file_id),
    ):
        if value and not (value.isascii() and value.isdigit() and int(value) > 0):
            return EnvironmentCheck(
                ok=False,
                exit_code=1,
                message=f"{variable} must be a positive numeric FileId (digits only).",
            )

    missing = [
        name
        for name, value in (
            ("CHOMIKUJ_USERNAME", username),
            ("CHOMIKUJ_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        return EnvironmentCheck(
            ok=False,
            exit_code=2,
            message=_missing_credentials_message(missing),
        )

    return EnvironmentCheck(
        ok=True,
        exit_code=0,
        message="",
        username=username,
        password=password,
        query=query,
        test_file_id=test_file_id,
        paid_file_id=paid_file_id,
    )


def _redact(value: object, secrets: Sequence[object] = ()) -> str:
    """Remove every known secret and every URL from ``value``.

    Secrets are replaced longest-first so a shorter credential cannot leave a
    fragment of a longer one behind. Emails and ``http(s)`` URLs are removed as
    well, whether or not they were listed explicitly.
    """
    text = str(value)
    known = sorted(
        {str(secret) for secret in secrets if secret is not None and str(secret) != ""},
        key=len,
        reverse=True,
    )
    for secret in known:
        text = text.replace(secret, REDACTED)
    text = _EMAIL_RE.sub(REDACTED, text)
    return _URL_RE.sub(REDACTED, text)


def _describe(exc: BaseException) -> str:
    """Render an exception as one line without a traceback."""
    text = " ".join(str(exc).split())
    return text or type(exc).__name__


def _expect(condition: object, message: str) -> None:
    """Raise ``AssertionError`` with a stable message when ``condition`` is false."""
    if not condition:
        raise AssertionError(message)


def _formats_override() -> tuple[str, ...]:
    """Return the Chomikuj importable formats used to widen Shelfmark's search.

    Shelfmark's default format list excludes ``pdf`` while the live-proven
    Chomikuj file is a PDF, so the gate overrides ``get_supported_formats`` for
    its own search only. Product settings are never changed.
    """
    from shelfmark.release_sources.chomikuj.filename import IMPORTABLE_EBOOK_FORMATS

    return tuple(IMPORTABLE_EBOOK_FORMATS)


def _synthetic_book(query: str) -> BookMetadata:
    """Build the synthetic metadata the source search needs."""
    from shelfmark.metadata_providers import BookMetadata

    return BookMetadata(
        provider="chomikuj-live-qa",
        provider_id="live-qa",
        title=query,
        authors=[],
    )


def _synthetic_plan(query: str) -> ReleaseSearchPlan:
    """Build a manual-query plan so the source searches exactly ``query``."""
    from shelfmark.core.search_plan import ReleaseSearchPlan

    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author="",
        title_variants=[],
        grouped_title_variants=[],
        manual_query=query,
    )


def _test_file_release(file_id: str) -> Release:
    """Build a release-shaped target for an owner-supplied ``CHOMIKUJ_TEST_FILE_ID``."""
    from shelfmark.release_sources import Release

    return Release(
        source="chomikuj",
        source_id=f"chomikuj:{file_id}",
        title=f"Chomikuj live QA file {file_id}",
        format="pdf",
        size_bytes=None,
        content_type="ebook",
        indexer="Chomikuj.pl",
        extra={
            "file_id": file_id,
            "file_name": f"{file_id}.pdf",
            "file_type": "pdf",
            "is_free_for_user": True,
            "freeleech": True,
            "author": None,
            "language": None,
            "access": "free",
        },
    )


def _release_file_id(release: object) -> str | None:
    """Return a release's positive numeric ``FileId`` as a string, or ``None``."""
    extra = getattr(release, "extra", None)
    if not isinstance(extra, dict):
        return None
    value = str(extra.get("file_id", "")).strip()
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        return None
    return value


def _is_free_release(release: object) -> bool:
    extra = getattr(release, "extra", None)
    if not isinstance(extra, dict):
        return False
    return extra.get("is_free_for_user") is True or extra.get("freeleech") is True


def _download_task(release: object, file_id: str, *, is_free: bool) -> DownloadTask:
    """Build the handler task for a release without copying any secret into it."""
    from shelfmark.core.models import DownloadTask

    extra = getattr(release, "extra", None)
    extra = extra if isinstance(extra, dict) else {}
    author = extra.get("author")
    return DownloadTask(
        task_id=f"chomikuj:{file_id}",
        source="chomikuj",
        title=str(getattr(release, "title", "") or f"Chomikuj file {file_id}"),
        author=author if isinstance(author, str) else None,
        year=None,
        format=str(getattr(release, "format", "") or "") or None,
        retry_source_context={
            "file_id": file_id,
            "size_bytes": getattr(release, "size_bytes", None),
            "is_free_for_user": is_free,
        },
    )


def _paid_probe_task(file_id: str) -> DownloadTask:
    """Build a synthetic paid task for the spend-off proof."""
    from shelfmark.core.models import DownloadTask

    return DownloadTask(
        task_id=f"chomikuj:{file_id}",
        source="chomikuj",
        title="Chomikuj live QA paid probe",
        format="pdf",
        retry_source_context={
            "file_id": file_id,
            "size_bytes": 1024 * 1024,
            "is_free_for_user": False,
        },
    )


def _config_getter(overrides: dict[str, object]) -> Callable[..., object]:
    """Return a ``config.get`` replacement backed by ``overrides``."""

    def get(key: str, default: object = None, user_id: int | None = None) -> object:
        del user_id
        return overrides.get(key, default)

    return get


def _probe_file_url(url: str) -> ProbeResult:
    """Fetch ``url`` exactly once with the live-proven headers and no ``Range``.

    Only the first chunk is needed: the gate asserts 200/206, a non-HTML
    content type, and at least one byte. The URL is never included in a result
    or an error message.
    """
    import requests

    from shelfmark.download import network

    with requests.get(
        url,
        headers=PROBE_HEADERS,
        stream=True,
        timeout=DEFAULT_TIMEOUT,
        proxies=network.get_proxies(url),
        verify=network.get_ssl_verify(url),
    ) as response:
        status = int(response.status_code)
        _expect(status in (200, 206), f"HTTP {status}")

        content_type = str(response.headers.get("Content-Type") or "")
        _expect(
            "html" not in content_type.lower(),
            f"HTML response: content-type={content_type!r}",
        )

        chunk = b""
        for candidate in response.iter_content(chunk_size=1024):
            if candidate:
                chunk = candidate
                break
        _expect(bool(chunk), "empty response body")
        return ProbeResult(
            status=status,
            content_type=content_type or "(none)",
            bytes_read=len(chunk),
        )


def _status_summary(statuses: list[tuple[str, str | None]]) -> str:
    """Return the last terminal handler status, or the last status seen."""
    for status, message in reversed(statuses):
        if status in {"error", "cancelled"}:
            return f"{status}: {message or '(no message)'}"
    if statuses:
        status, message = statuses[-1]
        return f"{status}: {message or '(no message)'}"
    return "(no status)"


def _login_secrets(session: object) -> list[str]:
    """Return the login response values that must never be rendered."""
    values = [
        getattr(session, "api_key", ""),
        getattr(session, "account_id", ""),
        getattr(session, "account_name", ""),
    ]
    return [str(value) for value in values if value not in (None, "")]


def _account_secrets(account: Mapping[str, object]) -> list[str]:
    """Return the account fields that must never be rendered."""
    values = [
        account.get("ApiKey"),
        account.get("AccountId"),
        account.get("AccountName"),
        account.get("Email"),
        account.get("OwnerId"),
    ]
    return [str(value) for value in values if value not in (None, "")]


def _step_line(name: str, status: str, detail: str) -> str:
    suffix = f": {detail}" if detail else ""
    return f"STEP {name}: {status}{suffix}"


def run_live(checked: EnvironmentCheck) -> int:
    """Run the credentialed gate and write the evidence file."""
    # Product log lines can quote request URLs. Only the redacted STEP lines
    # below may reach stdout, so mute the process loggers for the live run and
    # restore the previous level in finish().
    previous_log_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)

    from shelfmark.release_sources.chomikuj import handler as handler_module
    from shelfmark.release_sources.chomikuj import source as source_module
    from shelfmark.release_sources.chomikuj.client import ChomikujClient
    from shelfmark.release_sources.chomikuj.handler import ChomikujHandler
    from shelfmark.release_sources.chomikuj.session import get_session
    from shelfmark.release_sources.chomikuj.source import ChomikujSource

    steps: list[tuple[str, str, str]] = []
    secrets: list[str] = [value for value in (checked.username, checked.password) if value]
    failure: str | None = None
    formats = _formats_override()
    formats_line = f"formats_override={','.join(formats)}"
    paid_line = "paid_proof=SKIP_NOT_PROVEN"

    def record(name: str, status: str, detail: str = "") -> None:
        steps.append((name, status, detail))
        print(_redact(_step_line(name, status, detail), secrets))

    def fail(name: str, detail: str) -> None:
        nonlocal failure
        failure = detail
        record(name, "FAIL", detail)

    def finish() -> int:
        logging.disable(previous_log_disable)
        passed = failure is None
        gate = "LIVE_QA=PASS" if passed else f"LIVE_QA=FAIL: {failure}"
        lines = [
            "Chomikuj live QA",
            f"Captured: {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            f"Python: {sys.version.split()[0]}",
            f"Query: {checked.query}",
            formats_line,
            "",
        ]
        lines.extend(_step_line(name, status, detail) for name, status, detail in steps)
        lines.extend(["", paid_line, "", gate, ""])
        text = _redact("\n".join(lines), secrets)

        try:
            EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
            EVIDENCE_FILE.write_text(text, encoding="utf-8")
        except OSError as exc:
            print(
                _redact(f"live-qa: could not write {EVIDENCE_FILE}: {exc}", secrets),
                file=sys.stderr,
            )
            return 1

        print()
        print(_redact(gate, secrets))
        return 0 if passed else 1

    overrides: dict[str, object] = {
        "CHOMIKUJ_ENABLED": True,
        "CHOMIKUJ_USERNAME": checked.username,
        "CHOMIKUJ_PASSWORD": checked.password,
        "CHOMIKUJ_ALLOW_TRANSFER_SPEND": True,
        "CHOMIKUJ_MAX_RESULTS": 50,
        "CHOMIKUJ_BASE_URL": "",
        "FILE_ORGANIZATION": "rename",
    }
    settings_get = _config_getter(overrides)

    # Product config and formats are overridden in-process for this gate only.
    with (
        patch.object(handler_module.config, "get", side_effect=settings_get),
        patch.object(source_module, "get_supported_formats", return_value=list(formats)),
    ):
        try:
            client = ChomikujClient()
            session = get_session(
                client,
                username=checked.username,
                password=checked.password,
                base_url=client.base_url,
            )
        except Exception as exc:
            fail("login", _describe(exc))
            return finish()
        secrets.extend(_login_secrets(session))
        record("login", "PASS", "Api-Key received")

        try:
            account = client.account_info(session.api_key)
        except Exception as exc:
            fail("account-info", _describe(exc))
            return finish()
        secrets.extend(_account_secrets(account))
        tier = "unlimited" if account.get("HasUnlimitedTransfer") is True else "free"
        record("account-info", "PASS", f"tier={tier}; transfer={account.get('Transfer', '?')}")

        if checked.test_file_id:
            release: Release = _test_file_release(checked.test_file_id)
            record(
                "search",
                "PASS",
                f"skipped; CHOMIKUJ_TEST_FILE_ID={checked.test_file_id}",
            )
        else:
            try:
                releases = ChomikujSource().search(
                    _synthetic_book(checked.query),
                    _synthetic_plan(checked.query),
                )
            except Exception as exc:
                fail("search", _describe(exc))
                return finish()
            if not releases:
                fail(
                    "search",
                    f"no releases for query {checked.query!r}; set CHOMIKUJ_QUERY "
                    "to a query whose results include a free ebook",
                )
                return finish()
            record("search", "PASS", f"{len(releases)} release(s)")
            selected = next((item for item in releases if _is_free_release(item)), None)
            if selected is None:
                fail(
                    "free-release",
                    f"no free release among {len(releases)}; set CHOMIKUJ_QUERY "
                    "to a query whose results include a free ebook",
                )
                return finish()
            release = selected
            record(
                "free-release",
                "PASS",
                f"FileId={release.extra.get('file_id')}; format={release.format}",
            )

        file_id = _release_file_id(release)
        if file_id is None:
            fail("free-release", "the selected release has no positive numeric FileId")
            return finish()

        try:
            payload = client.download_url(session.api_key, file_id)
        except Exception as exc:
            fail("download-url", _describe(exc))
            return finish()
        file_url = payload.get("FileUrl")
        if not isinstance(file_url, str) or not file_url.strip():
            fail("download-url", "files/download returned no FileUrl")
            return finish()
        secrets.append(file_url)
        record("download-url", "PASS", "FileUrl received")

        try:
            probe = _probe_file_url(file_url)
        except Exception as exc:
            fail("ua-probe", _describe(exc))
            return finish()
        record(
            "ua-probe",
            "PASS",
            f"HTTP {probe.status}; content-type={probe.content_type}; bytes={probe.bytes_read}",
        )

        handler = ChomikujHandler()
        handler_statuses: list[tuple[str, str | None]] = []
        handler_progress: list[float] = []
        try:
            handler_module.TMP_DIR.mkdir(parents=True, exist_ok=True)
            # The URL was already resolved once above; the handler must reuse it
            # instead of spending a second files/download call.
            with patch.object(handler_module.ChomikujClient, "download_url", return_value=payload):
                path = handler.download(
                    _download_task(release, file_id, is_free=True),
                    threading.Event(),
                    handler_progress.append,
                    lambda status, message: handler_statuses.append((status, message)),
                )
        except Exception as exc:
            fail("handler-download", _describe(exc))
            return finish()

        if not isinstance(path, (str, Path)):
            fail("handler-download", f"no file ({_status_summary(handler_statuses)})")
            return finish()

        downloaded = Path(path)
        try:
            size = downloaded.stat().st_size
            with downloaded.open("rb") as handle:
                first = handle.read(1)
            if size < MIN_DOWNLOAD_BYTES:
                fail("handler-download", f"file is too small: {size} bytes")
            elif first == b"<":
                fail("handler-download", "file starts with '<' (HTML, not a book)")
            else:
                record(
                    "handler-download",
                    "PASS",
                    f"{size} bytes; first-byte=0x{first.hex() if first else 'none'}",
                )
        except OSError as exc:
            fail("handler-download", _describe(exc))
        finally:
            downloaded.unlink(missing_ok=True)

        if account.get("HasUnlimitedTransfer") is True:
            record(
                "spend-off",
                "SKIP",
                "unlimited account allows paid files; spend-off cannot be proven",
            )
            paid_line = "paid_proof=SKIP_NOT_PROVEN paid_source=synthetic"
        else:
            paid_file_id = checked.paid_file_id or "1"
            paid_source = "env" if checked.paid_file_id else "synthetic"
            download_called = False

            def forbidden_download(*args: object, **kwargs: object) -> object:
                nonlocal download_called
                del args, kwargs
                download_called = True
                raise AssertionError("files/download was called with spending disabled")

            proof_statuses: list[tuple[str, str | None]] = []
            overrides["CHOMIKUJ_ALLOW_TRANSFER_SPEND"] = False
            result = None
            guarded = True
            try:
                with patch.object(
                    handler_module.ChomikujClient,
                    "download_url",
                    side_effect=forbidden_download,
                ):
                    result = handler.download(
                        _paid_probe_task(paid_file_id),
                        threading.Event(),
                        lambda _value: None,
                        lambda status, message: proof_statuses.append((status, message)),
                    )
            except Exception as exc:
                guarded = False
                fail("spend-off", _describe(exc))
            finally:
                overrides["CHOMIKUJ_ALLOW_TRANSFER_SPEND"] = True

            if not guarded:
                paid_line = "paid_proof=FAIL paid_source=" + paid_source
            elif download_called:
                fail(
                    "spend-off",
                    f"ChomikujClient.download_url was called (paid_source={paid_source})",
                )
                paid_line = "paid_proof=FAIL paid_source=" + paid_source
            elif result is not None:
                fail("spend-off", "a paid download returned a file although spending is disabled")
                paid_line = "paid_proof=FAIL paid_source=" + paid_source
            elif not any(status == "error" for status, _ in proof_statuses):
                fail(
                    "spend-off",
                    f"expected an error status; got {_status_summary(proof_statuses)}",
                )
                paid_line = "paid_proof=FAIL paid_source=" + paid_source
            else:
                record(
                    "spend-off",
                    "PASS",
                    f"paid_source={paid_source}; download_url calls=0; status=error",
                )
                paid_line = "paid_proof=PASS paid_source=" + paid_source

    return finish()


def run_self_test() -> int:
    """Run the fully offline self-test; return 0 when every check passes."""
    from shelfmark.release_sources.chomikuj.quota import decide_download
    from shelfmark.release_sources.chomikuj.token import CHOMIKUJ_SECRET, sign_token

    failed: list[str] = []
    passed = 0

    def check(name: str, fn: Callable[[], None]) -> None:
        nonlocal passed
        try:
            fn()
        except Exception as exc:
            failed.append(name)
            print(f"SELF-TEST {name}: FAIL ({_redact(_describe(exc))})")
        else:
            passed += 1
            print(f"SELF-TEST {name}: PASS")

    def token_checks() -> None:
        _expect(len(CHOMIKUJ_SECRET) == 20, "secret length is not 20")
        for path, query, body, expected in TOKEN_VECTORS:
            actual = sign_token(path, query, body)
            _expect(actual == expected, f"{path}: expected {expected}, got {actual}")
        _expect(
            sign_token("/api/v3/account/info") == TOKEN_VECTORS[0][3],
            "leading slash was not stripped",
        )
        _expect(
            sign_token("api/v3/files/search", "PageNumber=1&Query=test")
            != sign_token("api/v3/files/search", TOKEN_VECTORS[1][1]),
            "query is not signed verbatim",
        )

    def quota_checks() -> None:
        unlimited = decide_download(
            file={"is_free_for_user": False},
            account={"HasUnlimitedTransfer": True, "Transfer": 0},
            allow_transfer_spend=False,
        )
        _expect(unlimited.allowed, "unlimited transfer should allow")
        free = decide_download(
            file={"is_free_for_user": True},
            account={"HasUnlimitedTransfer": False, "Transfer": 0},
            allow_transfer_spend=False,
        )
        _expect(free.allowed, "free file should allow with spending disabled")
        blocked = decide_download(
            file={"is_free_for_user": False, "size_bytes": 1024},
            account={"HasUnlimitedTransfer": False, "Transfer": 100_000},
            allow_transfer_spend=False,
        )
        _expect(not blocked.allowed, "paid file should be blocked with spending disabled")
        _expect(blocked.kind == "error", "blocked decision should be an error")
        _expect("disabled" in blocked.reason, "blocked reason should mention disabled spending")
        enough = decide_download(
            file={"is_free_for_user": False, "size_bytes": 1024},
            account={"HasUnlimitedTransfer": False, "Transfer": 100_000},
            allow_transfer_spend=True,
        )
        _expect(enough.allowed, "enough transfer should allow")
        short = decide_download(
            file={"is_free_for_user": False, "size_bytes": 1024 * 1024},
            account={"HasUnlimitedTransfer": False, "Transfer": 100},
            allow_transfer_spend=True,
        )
        _expect(not short.allowed, "insufficient transfer should deny")
        _expect("not enough transfer" in short.reason, "denial should say not enough transfer")

    def missing_credentials_check() -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([], env={})
        output = stdout.getvalue() + stderr.getvalue()
        _expect(code == 2, f"expected exit code 2, got {code}")
        _expect("CHOMIKUJ_USERNAME" in output, "missing username name was not printed")
        _expect("CHOMIKUJ_PASSWORD" in output, "missing password name was not printed")

    def redaction_check() -> None:
        known = ["owner@example.invalid", "hunter2", "API-KEY-123", "424242", "Owner Name"]
        text = " | ".join(
            [
                *known,
                "https://cdn.example.test/files/book.pdf?sig=secret",
                "someone@example.com",
            ]
        )
        rendered = _redact(text, known)
        for leaked in [*known, "https://", "cdn.example.test", "someone@example.com"]:
            _expect(leaked not in rendered, f"redaction leaked {leaked!r}")
        _expect(REDACTED in rendered, "redaction marker missing")

    def format_checks() -> None:
        override = _formats_override()
        _expect("pdf" in override, "pdf is missing from the formats override")
        _expect(
            PROBE_HEADERS
            == {"User-Agent": "android/3.8.4 (python; python)", "Accept": "*/*"},
            "probe headers drifted from the live-proven contract",
        )

    check("token vectors", token_checks)
    check("decide_download quota cases", quota_checks)
    check("missing credentials exit 2", missing_credentials_check)
    check("redaction of credentials, account fields, URLs, and emails", redaction_check)
    check("formats override and probe headers", format_checks)

    total = passed + len(failed)
    print(f"SELF-TEST: {'PASS' if not failed else 'FAIL'} ({passed}/{total})")
    return 0 if not failed else 1


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    """Entry point; ``--self-test`` is fully offline, the default path is live."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in arguments:
        return run_self_test()

    checked = check_environment(env)
    if not checked.ok:
        print(checked.message, file=sys.stderr)
        return checked.exit_code
    return run_live(checked)


if __name__ == "__main__":
    raise SystemExit(main())

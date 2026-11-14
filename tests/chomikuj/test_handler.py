"""Tests for the Chomikuj download handler.

The handler has three jobs, specified by plan todos 20-22:

* marshalling the retry context and running the transfer-quota preflight before
  ``files/download`` is charged;
* resolving a one-shot download URL and streaming it with the exact live-proven
  mobile API ``User-Agent``/``Accept: */*`` headers (no ``Range``, no Shelfmark
  ``DOWNLOAD_HEADERS``);
* cancelling, reporting progress, mapping business code 605, and cleaning up
  every partial file without ever leaking a credential.

Everything here is offline: the client, the session cache, the preflight, and
``requests.get`` are patched, and ``TMP_DIR`` points at ``tmp_path``.
"""

import threading
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.chomikuj import handler as handler_module
from shelfmark.release_sources.chomikuj.client import (
    DEFAULT_USER_AGENT,
    ChomikujAuthError,
    ChomikujError,
)
from shelfmark.release_sources.chomikuj.handler import ChomikujHandler
from shelfmark.release_sources.chomikuj.quota import (
    QuotaDecision,
)
from shelfmark.release_sources.chomikuj.quota import (
    decide_download as real_decide_download,
)

API_KEY = "SECRET-API-KEY"
PASSWORD = "hunter2"
FILE_URL = "https://cdn.chomikuj.test/files/book.pdf"


def _task(
    *,
    task_id: str = "chomikuj:123456",
    context: dict[str, object] | None = None,
    title: str = "Książka",
    author: str | None = "Autor",
    year: str | None = "2020",
    fmt: str | None = "pdf",
) -> DownloadTask:
    """Build a download task with optional overrides."""
    return DownloadTask(
        task_id=task_id,
        source="chomikuj",
        title=title,
        author=author,
        year=year,
        format=fmt,
        retry_source_context=(
            context
            if context is not None
            else {"file_id": "123456", "size_bytes": 20480, "is_free_for_user": True}
        ),
    )


def _response(
    body: bytes = b"x" * 20480,
    *,
    content_type: str | None = "application/pdf",
    content_length: bool = True,
    chunks: list[bytes] | None = None,
):
    """Build a mock streaming response with the given body."""
    response = MagicMock()
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    response.status_code = 200
    headers: dict[str, str] = {}
    if content_type is not None:
        headers["Content-Type"] = content_type
    if content_length:
        headers["Content-Length"] = str(len(body))
    response.headers = headers
    response.raise_for_status.return_value = None
    if chunks is None:
        chunks = [body[i : i + 8192] for i in range(0, len(body), 8192)]
    response.iter_content.return_value = chunks
    return response


def _run(
    tmp_path,
    *,
    task: DownloadTask | None = None,
    cancel: threading.Event | None = None,
    body: bytes = b"x" * 20480,
    content_type: str | None = "application/pdf",
    content_length: bool = True,
    chunks: list[bytes] | None = None,
    payload: object | None = None,
    account: dict[str, object] | None = None,
    organization: str = "none",
    allow_spend: bool = True,
    http_get=None,
    stream_impl=None,
    decision: QuotaDecision | None = None,
    download_url_error: BaseException | None = None,
    progress_hook=None,
):
    """Run the handler with every external dependency patched."""
    status = MagicMock()
    progress = MagicMock()
    cancel = cancel if cancel is not None else threading.Event()
    task = task if task is not None else _task()
    payload = {"FileUrl": FILE_URL, "Name": "Książka - Autor.pdf"} if payload is None else payload
    account = {"HasUnlimitedTransfer": True, "Transfer": 1_000_000} if account is None else account

    def progress_callback(value: float) -> None:
        """Forward progress values to the recorder and optional hook."""
        progress(value)
        if progress_hook is not None:
            progress_hook(value)

    client = MagicMock()
    client.account_info.return_value = account
    if download_url_error is not None:
        client.download_url.side_effect = download_url_error
    else:
        client.download_url.return_value = payload
    session = SimpleNamespace(api_key=API_KEY)
    response = _response(
        body,
        content_type=content_type,
        content_length=content_length,
        chunks=chunks,
    )
    get = MagicMock(return_value=response) if http_get is None else MagicMock(side_effect=http_get)
    decide = MagicMock()
    if decision is None:
        decide.side_effect = real_decide_download
    else:
        decide.return_value = decision

    values = {
        "FILE_ORGANIZATION": organization,
        "CHOMIKUJ_ALLOW_TRANSFER_SPEND": allow_spend,
    }

    with ExitStack() as stack:
        stack.enter_context(patch.object(handler_module, "TMP_DIR", tmp_path))
        client_cls = stack.enter_context(
            patch.object(handler_module, "ChomikujClient", return_value=client)
        )
        session_mock = stack.enter_context(
            patch.object(handler_module, "get_session", return_value=session)
        )
        clear_session_mock = stack.enter_context(patch.object(handler_module, "clear_session"))
        stack.enter_context(
            patch.object(
                handler_module,
                "get_credentials",
                return_value=("reader", PASSWORD),
            )
        )
        stack.enter_context(
            patch.object(
                handler_module,
                "get_base_url",
                return_value="https://mobile.chomikuj.pl/api/v3",
            )
        )
        stack.enter_context(patch.object(handler_module, "decide_download", decide))
        stack.enter_context(
            patch.object(
                handler_module.config,
                "get",
                side_effect=lambda key, default=None: values.get(key, default),
            )
        )
        stack.enter_context(patch.object(handler_module.network, "get_proxies", return_value={}))
        stack.enter_context(
            patch.object(handler_module.network, "get_ssl_verify", return_value=True)
        )
        stack.enter_context(patch.object(handler_module.requests, "get", get))
        if stream_impl is not None:
            stack.enter_context(
                patch.object(handler_module, "_stream_file", side_effect=stream_impl)
            )
        result = ChomikujHandler().download(task, cancel, progress_callback, status)

    return SimpleNamespace(
        result=result,
        status=status,
        progress=progress,
        cancel=cancel,
        task=task,
        client=client,
        client_cls=client_cls,
        get_session=session_mock,
        clear_session=clear_session_mock,
        get=get,
        decide=decide,
        session=session,
        response=response,
    )


# --- Todo 20: retry-context marshalling -------------------------------------


def test_retry_context_uses_top_level_size_bytes_and_only_retry_context():
    """Test retry context uses top level size bytes and only retry context."""
    fields = ChomikujHandler().build_retry_resolution_fields(
        {
            "source": "chomikuj",
            "source_id": "chomikuj:1",
            "size_bytes": 2048,
            "extra": {
                "file_id": "1",
                "file_name": "Ksiazka.epub",
                "file_type": "epub",
                "is_free_for_user": True,
                "format": "epub",
                "author": "Autor",
                "language": "pl",
                "access": "free",
                "size_bytes": 999,
            },
        }
    )

    assert set(fields) == {"retry_source_context"}
    assert "retry_download_url" not in fields
    assert "retry_download_protocol" not in fields

    context = fields["retry_source_context"]
    assert context["file_id"] == "1"
    assert context["size_bytes"] == 2048
    assert context["is_free_for_user"] is True
    assert context["file_name"] == "Ksiazka.epub"
    assert context["file_type"] == "epub"
    assert context["format"] == "epub"
    assert context["author"] == "Autor"
    assert context["language"] == "pl"
    assert context["access"] == "free"


def test_retry_context_falls_back_to_extra_size_bytes():
    """Test retry context falls back to extra size bytes."""
    fields = ChomikujHandler().build_retry_resolution_fields(
        {
            "source_id": "chomikuj:22",
            "extra": {"file_id": "22", "size_bytes": 4096},
        }
    )

    assert fields["retry_source_context"]["size_bytes"] == 4096


def test_retry_context_omits_none_and_invalid_file_id():
    """Test retry context omits none and invalid file id."""
    fields = ChomikujHandler().build_retry_resolution_fields(
        {
            "source_id": "chomikuj:1",
            "extra": {
                "file_id": "not-a-number",
                "file_name": None,
                "is_free_for_user": None,
            },
        }
    )

    assert fields == {"retry_source_context": {}}


def test_retry_context_normalizes_positive_integer_file_id():
    """Test retry context normalizes positive integer file id."""
    fields = ChomikujHandler().build_retry_resolution_fields(
        {"extra": {"file_id": 123, "is_free_for_user": False}}
    )

    context = fields["retry_source_context"]
    assert context["file_id"] == "123"
    assert context["is_free_for_user"] is False


def test_preflight_blocks_no_download_when_spend_disabled(tmp_path):
    """Test preflight blocks no download when spend disabled."""
    run = _run(
        tmp_path,
        task=_task(context={"file_id": "123456", "size_bytes": 20480, "is_free_for_user": False}),
        account={"Transfer": 1_000_000},
        allow_spend=False,
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    message = run.status.call_args.args[1]
    assert "transfer spending is disabled" in message
    run.client.download_url.assert_not_called()
    run.get.assert_not_called()


def test_preflight_passes_context_and_allows_spending_warning(tmp_path):
    """Test preflight passes context and allows spending warning."""
    run = _run(
        tmp_path,
        task=_task(context={"file_id": "123456", "size_bytes": 20480, "is_free_for_user": False}),
        account={"Transfer": 1_000_000},
    )

    assert run.result is not None
    run.decide.assert_called_once_with(
        file={"size_bytes": 20480, "is_free_for_user": False},
        account={"Transfer": 1_000_000},
        allow_transfer_spend=True,
    )
    run.status.assert_any_call("resolving", "Spending Chomikuj transfer for a paid file")


def test_preflight_unlimited_account_proceeds_without_warning(tmp_path):
    """Test preflight unlimited account proceeds without warning."""
    run = _run(
        tmp_path,
        task=_task(context={"file_id": "123456", "size_bytes": 20480, "is_free_for_user": False}),
        account={"HasUnlimitedTransfer": True},
    )

    assert run.result is not None
    messages = [call.args for call in run.status.call_args_list]
    assert ("resolving", "Spending Chomikuj transfer for a paid file") not in messages


def test_explicit_denial_sets_reason_and_skips_network(tmp_path):
    """Test explicit denial sets reason and skips network."""
    run = _run(
        tmp_path,
        task=_task(context={"file_id": "123456", "size_bytes": 20480, "is_free_for_user": False}),
        decision=QuotaDecision(allowed=False, kind="error", reason="not enough transfer"),
    )

    assert run.result is None
    run.status.assert_called_with("error", "not enough transfer")
    run.client.download_url.assert_not_called()


def test_missing_file_id_returns_error_without_constructing_client(tmp_path):
    """Test missing file id returns error without constructing client."""
    run = _run(tmp_path, task=_task(task_id="chomikuj:", context={}))

    assert run.result is None
    run.status.assert_called_with("error", "Chomikuj: invalid file id")
    run.client_cls.assert_not_called()
    run.get_session.assert_not_called()
    run.client.download_url.assert_not_called()


def test_non_numeric_file_id_returns_clean_error(tmp_path):
    """Test non numeric file id returns clean error."""
    run = _run(tmp_path, task=_task(task_id="chomikuj:abc", context={}))

    assert run.result is None
    run.status.assert_called_with("error", "Chomikuj: invalid file id")
    run.client.download_url.assert_not_called()
    run.get.assert_not_called()


def test_file_id_falls_back_to_task_id_suffix(tmp_path):
    """Test file id falls back to task id suffix."""
    run = _run(tmp_path, task=_task(task_id="chomikuj:987654", context={}))

    assert run.result is not None
    run.client.download_url.assert_called_once_with(API_KEY, "987654")


def test_cancel_before_start_makes_no_network_call(tmp_path):
    """Test cancel before start makes no network call."""
    cancel = threading.Event()
    cancel.set()
    run = _run(tmp_path, cancel=cancel)

    assert run.result is None
    run.status.assert_called_with("cancelled", "Cancelled")
    run.client.account_info.assert_not_called()
    run.client.download_url.assert_not_called()
    run.get.assert_not_called()


# --- Todo 21: one-shot URL resolution and mobile-API streaming ----------------


def test_download_url_called_once_and_headers_are_exact(tmp_path):
    """Test download url called once and headers are exact."""
    run = _run(tmp_path)

    assert run.result == str(tmp_path / "Książka - Autor.pdf")
    run.client.download_url.assert_called_once_with(API_KEY, "123456")

    run.get.assert_called_once()
    assert run.get.call_args.args == (FILE_URL,)
    kwargs = run.get.call_args.kwargs
    assert kwargs["headers"] == {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
    assert "Range" not in kwargs["headers"]
    assert kwargs["stream"] is True
    assert kwargs["timeout"] == (10, 60)
    assert kwargs["proxies"] == {}
    assert kwargs["verify"] is True


def test_stream_ok_writes_body_and_returns_staging_path(tmp_path):
    """Test stream ok writes body and returns staging path."""
    body = bytes(range(256)) * 80  # 20480 bytes
    run = _run(tmp_path, body=body, organization="rename")

    expected = tmp_path / "Autor - Książka (2020).pdf"
    assert run.result == str(expected)
    assert expected.read_bytes() == body
    run.progress.assert_any_call(100.0)


def test_path_escape_sanitized_response_name_stays_in_tmp(tmp_path):
    """Test path escape sanitized response name stays in tmp."""
    run = _run(
        tmp_path,
        payload={"FileUrl": FILE_URL, "Name": "../../evil/book.pdf"},
        organization="none",
    )

    expected = tmp_path / ".._.._evil_book.pdf"
    assert run.result == str(expected)
    assert expected.read_bytes() == b"x" * 20480
    assert expected.resolve().parent == tmp_path.resolve()


def test_path_escape_dotdot_returns_clean_error(tmp_path):
    """Test path escape dotdot returns clean error."""
    run = _run(
        tmp_path,
        payload={"FileUrl": FILE_URL, "Name": ".."},
        organization="none",
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    assert run.status.call_args.args[0] == "error"


def test_numeric_file_id_fallback_builds_staging_name(tmp_path):
    """Test numeric file id fallback builds staging name."""
    run = _run(
        tmp_path,
        payload={"FileUrl": FILE_URL, "Name": None},
        organization="none",
    )

    assert run.result == str(tmp_path / "123456.pdf")


def test_html_rejected_before_writing(tmp_path):
    """Test html rejected before writing."""
    run = _run(
        tmp_path,
        body=b"<html><body>challenge</body></html>",
        content_type="text/html; charset=utf-8",
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj returned an HTML page instead of a book")


def test_html_rejected_for_xhtml_before_writing(tmp_path):
    """Test html rejected for xhtml before writing."""
    run = _run(tmp_path, body=b"<html/>", content_type="application/xhtml+xml")

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj returned an HTML page instead of a book")


def test_html_body_detected_without_html_content_type(tmp_path):
    """Test an HTML body is rejected even when Content-Type is not HTML."""
    run = _run(
        tmp_path,
        body=b"<!DOCTYPE html><html><body>challenge</body></html>",
        content_type="application/octet-stream",
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj returned an HTML page instead of a book")


def test_html_body_with_leading_comment_and_whitespace_is_detected(tmp_path):
    """Test leading whitespace and an HTML comment do not mask an HTML body."""
    run = _run(
        tmp_path,
        body=b"  <!-- challenge -->\n<!DOCTYPE html><html></html>",
        content_type="application/octet-stream",
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj returned an HTML page instead of a book")


def test_plain_text_document_is_not_mistaken_for_html(tmp_path):
    """Test a plain text body is accepted and its bytes are preserved."""
    body = b"Hello, this is a plain text book."
    run = _run(
        tmp_path,
        body=body,
        content_type="text/plain",
        payload={"FileUrl": FILE_URL, "Name": "book.txt"},
        task=_task(fmt="txt"),
    )

    expected = tmp_path / "book.txt"
    assert run.result == str(expected)
    assert expected.read_bytes() == body


@pytest.mark.parametrize(
    ("body", "fmt"),
    [
        (b'<?xml version="1.0"?><FictionBook></FictionBook>', "fb2"),
        (b"%PDF-1.7", "pdf"),
        (b"PK\x03\x04", "epub"),
    ],
)
def test_non_html_document_prefixes_are_accepted(tmp_path, body, fmt):
    """Test XML, PDF, and EPUB prefixes are not mistaken for HTML."""
    run = _run(
        tmp_path,
        body=body,
        content_type="application/octet-stream",
        payload={"FileUrl": FILE_URL, "Name": f"book.{fmt}"},
        task=_task(fmt=fmt),
    )

    expected = tmp_path / f"book.{fmt}"
    assert run.result == str(expected)
    assert expected.read_bytes() == body


def test_five_byte_non_html_body_is_accepted(tmp_path):
    """Test a five-byte body is accepted, proving there is no size floor."""
    run = _run(
        tmp_path,
        body=b"Hello",
        content_type="text/plain",
        payload={"FileUrl": FILE_URL, "Name": "book.txt"},
        task=_task(fmt="txt"),
    )

    expected = tmp_path / "book.txt"
    assert run.result == str(expected)
    assert expected.read_bytes() == b"Hello"


def test_empty_body_rejected_as_invalid_file(tmp_path):
    """Test an empty body is rejected as an invalid file."""
    run = _run(tmp_path, body=b"")

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj returned an invalid file")


def test_small_valid_document_is_accepted(tmp_path):
    """Test a valid document well under ten kilobytes is accepted."""
    body = (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b"<FictionBook><description><title-info>"
        b"<book-title>Tiny</book-title>"
        b"</title-info></description></FictionBook>"
    )
    assert len(body) < 10 * 1024
    run = _run(
        tmp_path,
        body=body,
        content_type="application/xml",
        payload={"FileUrl": FILE_URL, "Name": "tiny.fb2"},
        task=_task(fmt="fb2"),
        organization="none",
    )

    expected = tmp_path / "tiny.fb2"
    assert run.result == str(expected)
    assert expected.read_bytes() == body


def test_progress_uses_content_length_when_present(tmp_path):
    """Test progress uses content length when present."""
    run = _run(tmp_path, body=b"x" * 20480, content_length=True)

    percents = [call.args[0] for call in run.progress.call_args_list]
    assert percents[-1] == 100.0
    assert any(0.0 < percent < 100.0 for percent in percents)


def test_progress_uses_size_bytes_without_content_length(tmp_path):
    """Test progress uses size bytes without content length."""
    body = b"x" * 20480
    run = _run(
        tmp_path,
        body=body,
        content_length=False,
        chunks=[body[:10240], body[10240:]],
    )

    # 10 KiB of 20 KiB from the retry-context size_bytes.
    run.progress.assert_any_call(50.0)


def test_missing_file_url_returns_clean_error(tmp_path):
    """Test missing file url returns clean error."""
    run = _run(tmp_path, payload={"Name": "Książka.pdf"})

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj did not return a download URL")
    run.get.assert_not_called()


def test_downloading_status_clears_resolving_message(tmp_path):
    """Test downloading status clears resolving message."""
    run = _run(tmp_path)

    run.status.assert_any_call("downloading", "")


# --- Todo 22: cancellation, 605, cleanup, safety -----------------------------


def test_cancel_midstream_after_first_chunk_leaves_no_file(tmp_path):
    """Test cancel midstream after first chunk leaves no file."""
    cancel = threading.Event()

    def progress_hook(percent: float) -> None:
        """Cancel the download after the first nonzero progress update."""
        if percent > 0:
            cancel.set()

    run = _run(tmp_path, cancel=cancel, progress_hook=progress_hook)

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("cancelled", "Cancelled")


def test_network_error_request_exception_cleans_up_and_reports_error(tmp_path):
    """Test network error request exception cleans up and reports error."""
    run = _run(
        tmp_path,
        http_get=requests.exceptions.ConnectionError("connection reset"),
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    assert run.status.call_args.args[0] == "error"


def test_os_error_cleanup_removes_partial_and_reports_error(tmp_path):
    """Test os error cleanup removes partial and reports error."""

    def stream_impl(_url, target, **_kwargs):
        """Write a partial file, then raise an OS error."""
        target.write_bytes(b"partial")
        msg = "permission denied"
        raise OSError(msg)

    run = _run(tmp_path, stream_impl=stream_impl)

    assert run.result is None
    assert not list(tmp_path.iterdir())
    assert run.status.call_args.args[0] == "error"


def test_unexpected_exception_never_escapes(tmp_path):
    """Test unexpected exception never escapes."""

    def stream_impl(_url, _target, **_kwargs):
        """Raise an unexpected runtime error."""
        msg = "surprise"
        raise RuntimeError(msg)

    run = _run(tmp_path, stream_impl=stream_impl)

    assert run.result is None
    assert run.status.call_args.args[0] == "error"


def test_605_no_stream_reports_exact_message(tmp_path):
    """Test 605 no stream reports exact message."""
    run = _run(
        tmp_path,
        download_url_error=ChomikujError("not enough transfer", code=605),
    )

    assert run.result is None
    assert not list(tmp_path.iterdir())
    run.status.assert_called_with("error", "Chomikuj: not enough transfer")
    run.get.assert_not_called()


@pytest.mark.parametrize("code", [605, "605"])
def test_605_no_stream_code_variants_map_to_exact_message(tmp_path, code):
    """Test 605 no stream code variants map to exact message."""
    run = _run(
        tmp_path,
        download_url_error=ChomikujError("not enough transfer", code=code),
    )

    assert run.result is None
    run.status.assert_called_with("error", "Chomikuj: not enough transfer")
    run.get.assert_not_called()


def test_error_message_redacts_api_key_and_password(tmp_path):
    """Test error message redacts api key and password."""
    run = _run(
        tmp_path,
        download_url_error=ChomikujError(f"login {API_KEY} and {PASSWORD} failed", code=401),
    )

    assert run.result is None
    message = run.status.call_args.args[1]
    assert API_KEY not in message
    assert PASSWORD not in message


def test_unauthorized_clears_the_cached_session(tmp_path):
    """Test unauthorized clears the cached session."""
    run = _run(
        tmp_path,
        download_url_error=ChomikujAuthError("unauthorized", status=401, code=401),
    )

    assert run.result is None
    run.clear_session.assert_called_once_with(
        username="reader",
        password=PASSWORD,
        base_url="https://mobile.chomikuj.pl/api/v3",
    )


def test_cancel_returns_false():
    """Test cancel returns false."""
    assert ChomikujHandler().cancel("anything") is False

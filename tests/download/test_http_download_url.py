"""Focused tests for download_url() retry, fallback, and resume behavior."""

from unittest.mock import MagicMock

import requests


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict | None = None,
        chunks: list[bytes] | None = None,
        url: str = "",
        iter_error: requests.exceptions.RequestException | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self._chunks = chunks or []
        self._iter_error = iter_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        for chunk in self._chunks:
            yield chunk
        if self._iter_error:
            raise self._iter_error


class _DummyProgressBar:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def update(self, amount: int) -> None:
        del amount

    def close(self) -> None:
        return None


def _prepare_download_test(monkeypatch):
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: False)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(http, "tqdm", _DummyProgressBar)

    return http


def test_download_url_returns_none_on_rate_limit(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    status_updates: list[tuple[str, str | None]] = []

    def fake_get(_url: str, **_kwargs):
        error = requests.exceptions.HTTPError("busy")
        error.response = _FakeResponse(429, url=_url)
        raise error

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url(
        "https://example.com/file.epub",
        status_callback=lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("resolving", "Server busy, trying next")]


def test_download_url_returns_none_on_timeout(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    status_updates: list[tuple[str, str | None]] = []

    def fake_get(_url: str, **_kwargs):
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url(
        "https://example.com/file.epub",
        status_callback=lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("resolving", "Server timed out, trying next")]


def test_download_url_rejects_html_error_pages(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    status_updates: list[tuple[str, str | None]] = []

    def fake_get(url: str, **_kwargs):
        return _FakeResponse(
            200,
            headers={
                "content-length": "100",
                "content-type": "text/html; charset=utf-8",
            },
            chunks=[b"<html>busy</html>"],
            url=url,
        )

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url(
        "https://example.com/file.epub",
        status_callback=lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("downloading", "")]


def test_download_url_resumes_partial_download_after_connection_error(monkeypatch):
    http = _prepare_download_test(monkeypatch)

    calls: list[dict[str, object]] = []
    responses = [
        _FakeResponse(
            200,
            headers={
                "content-length": "8",
                "content-type": "application/octet-stream",
            },
            chunks=[b"abcd"],
            url="https://example.com/file.epub",
            iter_error=requests.exceptions.ConnectionError("socket reset"),
        ),
        _FakeResponse(
            206,
            headers={"content-length": "4"},
            chunks=[b"efgh"],
            url="https://example.com/file.epub",
        ),
    ]

    def fake_get(url: str, **kwargs):
        calls.append({"url": url, "headers": dict(kwargs.get("headers", {}))})
        return responses[len(calls) - 1]

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url("https://example.com/file.epub")

    assert result is not None
    assert result.getvalue() == b"abcdefgh"
    assert len(calls) == 2
    assert "Range" not in calls[0]["headers"]
    assert calls[1]["headers"]["Range"] == "bytes=4-"


def test_download_does_not_log_a_url_bearing_resume_error(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    monkeypatch.setattr(http, "MAX_DOWNLOAD_RETRIES", 1)
    sensitive_url = "https://cdn.example/book.epub?signature=secret"
    initial_response = _FakeResponse(
        200,
        headers={"content-length": "8"},
        chunks=[b"book"],
        iter_error=requests.exceptions.ConnectionError(sensitive_url),
    )
    get = MagicMock(
        side_effect=[
            initial_response,
            requests.exceptions.ConnectionError(sensitive_url),
            requests.exceptions.ConnectionError(sensitive_url),
            requests.exceptions.ConnectionError(sensitive_url),
        ]
    )
    monkeypatch.setattr(http.requests, "get", get)

    assert http.download_url(sensitive_url) is None

    assert sensitive_url not in str(logger.mock_calls)
    logger.debug.assert_any_call("Resume attempt %s failed: %s", 1, "ConnectionError")


def test_download_logs_resume_error_type_without_exception_details(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    monkeypatch.setattr(http, "MAX_DOWNLOAD_RETRIES", 1)
    download_url = "https://cdn.example/file.epub?signature=secret"
    error = requests.exceptions.ConnectionError(download_url)
    initial_response = _FakeResponse(
        200,
        headers={"content-length": "8"},
        chunks=[b"book"],
        iter_error=error,
    )
    get = MagicMock(side_effect=[initial_response, error, error, error])
    monkeypatch.setattr(http.requests, "get", get)

    assert http.download_url(download_url) is None

    logger.debug.assert_any_call("Resume attempt %s failed: %s", 1, "ConnectionError")


def test_download_url_does_not_log_a_sensitive_url(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda url, **_kwargs: _FakeResponse(
            200,
            headers={"content-length": "4"},
            chunks=[b"book"],
            url=url,
        ),
    )
    sensitive_url = "https://cdn.example/book.epub?signature=secret"

    assert http.download_url(sensitive_url) is not None

    assert sensitive_url not in str(logger.mock_calls)


def test_download_does_not_log_a_url_bearing_request_error(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    sensitive_url = "https://cdn.example/book.epub?signature=secret"
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda url, **_kwargs: (_ for _ in ()).throw(requests.exceptions.ConnectionError(url)),
    )

    assert http.download_url(sensitive_url) is None

    assert sensitive_url not in str(logger.mock_calls)
    logger.warning.assert_any_call("Download error: %s", "ConnectionError")


def test_download_logs_request_error_type_without_exception_details(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    download_url = "https://cdn.example/file.epub?signature=secret"
    error = requests.exceptions.ConnectionError(download_url)
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda _url, **_kwargs: (_ for _ in ()).throw(error),
    )

    assert http.download_url(download_url) is None

    logger.warning.assert_any_call("Download error: %s", "ConnectionError")


def test_download_does_not_log_rotated_url_after_retry(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    monkeypatch.setattr(http, "MAX_DOWNLOAD_RETRIES", 2)
    source_url = "https://source.example/file.epub"
    rotated_url = "https://rotated.example/file.epub"
    error = requests.exceptions.ConnectionError("connection reset")
    monkeypatch.setattr(http.requests, "get", MagicMock(side_effect=error))
    monkeypatch.setattr(http, "_try_rotation", MagicMock(side_effect=[rotated_url, None]))

    assert http.download_url(source_url) is None

    assert rotated_url not in str(logger.mock_calls)
    logger.info.assert_any_call("Downloading (attempt %s/%s)", 2, 2)
    logger.error.assert_called_once_with("Download failed after %s attempts", 2)


def test_download_does_not_pass_url_redaction_state_into_rotation(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    monkeypatch.setattr(http, "MAX_DOWNLOAD_RETRIES", 1)
    sensitive_url = "https://cdn.example/book.epub?signature=secret"
    monkeypatch.setattr(
        http.requests,
        "get",
        lambda _url, **_kwargs: (_ for _ in ()).throw(requests.exceptions.ConnectionError("x")),
    )
    rotation = MagicMock(return_value=None)
    monkeypatch.setattr(http, "_try_rotation", rotation)

    assert http.download_url(sensitive_url) is None

    assert rotation.call_args.kwargs == {}


def test_download_does_not_log_a_rotated_url(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    monkeypatch.setattr(http, "MAX_DOWNLOAD_RETRIES", 2)
    sensitive_url = "https://cdn.example/book.epub?signature=secret"
    rotated_url = "https://rotated.example/book.epub?signature=secret"
    error = requests.exceptions.ConnectionError("connection reset")
    monkeypatch.setattr(http.requests, "get", MagicMock(side_effect=error))
    monkeypatch.setattr(http, "_try_rotation", MagicMock(side_effect=[rotated_url, None]))

    assert http.download_url(sensitive_url) is None

    logged = str(logger.mock_calls)
    assert sensitive_url not in logged
    assert rotated_url not in logged
    assert "<redacted>" not in logged


def test_default_download_does_not_log_the_rotated_url(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    logger = MagicMock()
    monkeypatch.setattr(http, "logger", logger)
    monkeypatch.setattr(http, "MAX_DOWNLOAD_RETRIES", 2)
    source_url = "https://source.example/file.epub?apikey=k"
    rotated_url = "https://rotated.example/file.epub?apikey=k"
    error = requests.exceptions.ConnectionError("connection reset")
    monkeypatch.setattr(http.requests, "get", MagicMock(side_effect=error))

    def fake_rotation(original_url, _current, _selector, *, fatal_reason=None):
        return rotated_url

    monkeypatch.setattr(http, "_try_rotation", fake_rotation)

    assert http.download_url(source_url) is None

    assert rotated_url not in str(logger.mock_calls)

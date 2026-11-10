"""Chomikuj mobile API client request core.

Every request carries the mobile app User-Agent and an MD5 request token signed
over the exact path, query string, and body that go on the wire (see
``token.py``). :meth:`ChomikujClient.login` posts the credentials once and
returns the parsed ``ApiKey`` payload; later endpoints send that key as
``Api-Key``.

The private :meth:`ChomikujClient._send` helper performs one request and returns
``(status, text)`` so endpoint methods (account, search, download) can add their
own classification without duplicating transport, proxy, or header handling.

Error messages never include the password, the request body, or an API key.
"""

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urlencode

import requests

from shelfmark.download import network
from shelfmark.release_sources.chomikuj.failures import (
    FailureDecision,
    classify_business,
    classify_http,
    classify_login,
)
from shelfmark.release_sources.chomikuj.token import sign_token

DEFAULT_BASE_URL = "https://mobile.chomikuj.pl/api/v3"
DEFAULT_USER_AGENT = "android/3.8.4 (python; python)"
DEFAULT_TIMEOUT = (10, 30)

RETRY_DELAY_MS = 500.0
MAX_RETRY_AFTER_MS = 2000.0

LOGIN_PATH = "account/login"
ACCOUNT_PATH = "account/info"
SEARCH_PATH = "files/search"
DOWNLOAD_PATH = "files/download"

_REQUEST_TIMEOUT = float | tuple[float, float]

_RETRY_AFTER_SECONDS_RE = re.compile(r"\d+(?:\.\d+)?")


class ChomikujError(Exception):
    """One Chomikuj request failure.

    ``kind`` is one of ``unreachable``, ``unauthorized``, ``throttled``, or
    ``error``. ``status`` is the HTTP status when a response was received, and
    ``code`` is the Chomikuj business code when the payload carried one.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "error",
        retryable: bool = False,
        status: int | None = None,
        code: object = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status = status
        self.code = code


class ChomikujAuthError(ChomikujError):
    """Credentials were rejected."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status: int | None = None,
        code: object = None,
    ) -> None:
        super().__init__(
            message,
            kind="unauthorized",
            retryable=retryable,
            status=status,
            code=code,
        )


def normalize_base_url(value: object) -> str:
    """Return the ``/api/v3`` root for a host root or an already-full URL."""
    if not isinstance(value, str):
        return DEFAULT_BASE_URL
    trimmed = value.strip().rstrip("/")
    if not trimmed:
        return DEFAULT_BASE_URL
    if trimmed.endswith("/api/v3"):
        return trimmed
    return f"{trimmed}/api/v3"


def _error_from(
    decision: FailureDecision,
    *,
    status: int | None = None,
    code: object = None,
) -> ChomikujError:
    """Turn a failure decision into the matching exception instance."""
    if decision.kind == "unauthorized":
        return ChomikujAuthError(
            decision.message,
            retryable=decision.retryable,
            status=status,
            code=code,
        )
    return ChomikujError(
        decision.message,
        kind=decision.kind or "error",
        retryable=decision.retryable,
        status=status,
        code=code,
    )


def _try_parse_json(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError, ValueError:
        return None


def _payload_code(payload: object) -> object:
    return payload.get("Code") if isinstance(payload, dict) else None


def _payload_message(payload: dict[str, object]) -> str:
    value = payload.get("Message") or payload.get("message") or ""
    return value if isinstance(value, str) else ""


def _header_value(headers: object, name: str) -> str | None:
    if not isinstance(headers, Mapping):
        return None
    value = headers.get(name)
    if isinstance(value, str):
        return value
    lower = name.lower()
    for key, candidate in headers.items():
        if isinstance(key, str) and key.lower() == lower and isinstance(candidate, str):
            return candidate
    return None


def _parse_retry_after_ms(value: object) -> float | None:
    """Parse ``Retry-After`` as delta-seconds or an HTTP-date into milliseconds."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    if _RETRY_AFTER_SECONDS_RE.fullmatch(text):
        seconds = float(text)
        return seconds * 1000.0 if math.isfinite(seconds) else None

    try:
        when = parsedate_to_datetime(text)
    except TypeError, ValueError:
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    delta = (when - datetime.now(UTC)).total_seconds()
    return max(0.0, delta * 1000.0)


class ChomikujClient:
    """Request core for Chomikuj's mobile API.

    ``session`` is injectable for offline tests; the production default is a
    real :class:`requests.Session`.
    """

    def __init__(
        self,
        base_url: object | None = None,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        session: requests.Session | None = None,
        timeout: _REQUEST_TIMEOUT = DEFAULT_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._root = normalize_base_url(base_url)
        self._user_agent = user_agent
        self._session = session if session is not None else requests.Session()
        self._timeout = timeout
        self._sleep = sleep

    @property
    def base_url(self) -> str:
        """The normalized ``/api/v3`` root this client sends requests to."""
        return self._root

    def _headers(
        self,
        path: str,
        query: str = "",
        api_key: str | None = None,
        body: str = "",
    ) -> dict[str, str]:
        """Build the signed headers for one request.

        ``Api-Key`` is only sent when a truthy key is supplied, so login never
        carries one.
        """
        headers = {
            "User-Agent": self._user_agent,
            "Token": sign_token(f"api/v3/{path}", query, body or ""),
        }
        if api_key:
            headers["Api-Key"] = api_key
        return headers

    def _send_full(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        api_key: str | None = None,
        body: str = "",
        content_type: str | None = None,
        timeout: _REQUEST_TIMEOUT | None = None,
    ) -> tuple[int, str, object]:
        """Perform exactly one request and return ``(status, text, headers)``.

        No retries and no body parsing happen here: endpoint methods classify the
        status and text themselves. Proxy and TLS-verification settings are
        resolved per URL on every call.
        """
        url = f"{self._root}/{path}"
        if query:
            url = f"{url}?{query}"

        headers = self._headers(path, query, api_key, body)
        if content_type:
            headers["Content-Type"] = content_type

        try:
            response = self._session.request(
                method,
                url,
                headers=headers,
                data=body.encode("utf-8") if body else None,
                timeout=self._timeout if timeout is None else timeout,
                proxies=network.get_proxies(url),
                verify=network.get_ssl_verify(url),
            )
        except requests.exceptions.RequestException as exc:
            # The request may carry credentials: never echo it into the message.
            raise _error_from(classify_http(0, "")) from exc

        return int(response.status_code), response.text, getattr(response, "headers", {})

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: str = "",
        api_key: str | None = None,
        body: str = "",
        content_type: str | None = None,
        timeout: _REQUEST_TIMEOUT | None = None,
    ) -> tuple[int, str]:
        status, text, _headers = self._send_full(
            method,
            path,
            query=query,
            api_key=api_key,
            body=body,
            content_type=content_type,
            timeout=timeout,
        )
        return status, text

    def _retry_delay_ms(
        self,
        status: int,
        decision: FailureDecision,
        headers: object,
        timeout: _REQUEST_TIMEOUT | None,
    ) -> float | None:
        """Return the single retry delay, or ``None`` when no retry is allowed."""
        if decision.kind is None:
            return None
        if decision.message == "cloudflare challenge":
            return None
        if isinstance(timeout, (int, float)) and timeout <= 0:
            return None
        if status != 429 and not 500 <= status < 600:
            return None

        retry_after = _parse_retry_after_ms(_header_value(headers, "Retry-After"))
        if retry_after is not None and retry_after > MAX_RETRY_AFTER_MS:
            return None
        if retry_after is None:
            return RETRY_DELAY_MS
        return max(RETRY_DELAY_MS, retry_after)

    def _get_json(
        self,
        path: str,
        *,
        api_key: str,
        query: str = "",
        retry: bool,
        timeout: _REQUEST_TIMEOUT | None = None,
    ) -> dict[str, object]:
        status, text, headers = self._send_full(
            "GET", path, query=query, api_key=api_key, timeout=timeout
        )
        decision = classify_http(status, text)

        if retry:
            delay_ms = self._retry_delay_ms(status, decision, headers, timeout)
            if delay_ms is not None:
                self._sleep(delay_ms / 1000.0)
                status, text, headers = self._send_full(
                    "GET", path, query=query, api_key=api_key, timeout=timeout
                )
                decision = classify_http(status, text)

        payload = _try_parse_json(text)
        if decision.kind is not None:
            raise _error_from(decision, status=status, code=_payload_code(payload))
        if not isinstance(payload, dict):
            raise ChomikujError("invalid JSON response", status=status)

        business = classify_business(payload.get("Code"), _payload_message(payload))
        if business.kind is not None:
            raise _error_from(business, status=status, code=payload.get("Code"))
        return payload

    def account_info(
        self, api_key: str, *, timeout: _REQUEST_TIMEOUT | None = None
    ) -> dict[str, object]:
        """GET the account payload, retrying once on HTTP 429 or 5xx."""
        return self._get_json(ACCOUNT_PATH, api_key=api_key, retry=True, timeout=timeout)

    def search_page(
        self,
        api_key: str,
        *,
        query: str,
        page: int,
        extension: str | None = None,
        timeout: _REQUEST_TIMEOUT | None = None,
    ) -> dict[str, object]:
        """GET one Documents-only search page, retrying once on 429 or 5xx."""
        params: list[tuple[str, object]] = [
            ("Query", "" if query is None else str(query)),
            ("PageNumber", str(page)),
            ("MediaType", "Documents"),
        ]
        if isinstance(extension, str) and extension.strip():
            params.append(("Extension", extension.strip()))
        query_string = urlencode(params)

        return self._get_json(
            SEARCH_PATH,
            api_key=api_key,
            query=query_string,
            retry=True,
            timeout=timeout,
        )

    def download_url(self, api_key: str, file_id: object) -> dict[str, object]:
        """GET the download payload exactly once; failures are never retried."""
        query_string = f"FileId={quote(str(file_id), safe='')}"
        status, text, _headers = self._send_full(
            "GET", DOWNLOAD_PATH, query=query_string, api_key=api_key
        )

        payload = _try_parse_json(text)
        decision = classify_http(status, text)
        if decision.kind is not None:
            raise _error_from(decision, status=status, code=_payload_code(payload))
        if not isinstance(payload, dict):
            raise ChomikujError("invalid JSON response", status=status)

        business = classify_business(payload.get("Code"), _payload_message(payload))
        if business.kind is not None:
            raise _error_from(business, status=status, code=payload.get("Code"))

        file_url = payload.get("FileUrl")
        if not isinstance(file_url, str) or not file_url.strip():
            raise ChomikujError("missing FileUrl", status=status, code=payload.get("Code"))
        return payload

    def login(self, username: str, password: str) -> dict[str, object]:
        """POST the credentials once and return the parsed login payload."""
        body = json.dumps(
            {"AccountName": username, "Password": password},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        status, text = self._send(
            "POST",
            LOGIN_PATH,
            body=body,
            content_type="application/json; charset=utf-8",
        )

        try:
            payload = json.loads(text)
        except ValueError:
            payload = None

        decision = classify_login(status, text)
        if decision.kind is not None:
            malformed_success = (
                decision.kind == "unauthorized"
                and 200 <= status < 300
                and not isinstance(payload, dict)
            )
            if malformed_success:
                raise ChomikujError("invalid JSON response", status=status)
            raise _error_from(
                decision,
                status=status,
                code=payload.get("Code") if isinstance(payload, dict) else None,
            )

        if not isinstance(payload, dict):
            raise ChomikujError("invalid JSON response", status=status)
        return payload

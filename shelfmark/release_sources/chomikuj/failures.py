"""Chomikuj failure classification.

Maps transport statuses, Chomikuj business codes, and login responses to a
single :class:`FailureDecision`. Every function here is pure: nothing performs
I/O, throws, retries, or logs. Callers turn the decision into a user-visible
error and decide whether a retry is allowed.

Ported from a validated Chomikuj implementation (``src/lib/failures.mjs``);
messages are never built from raw HTML and plain-text excerpts are capped at
160 characters.
"""

import json
import math
import re
from dataclasses import dataclass

SUCCESS_CODES = frozenset({0, 200, 201})

# Lowercase on purpose; the body is lowercased before matching.
CLOUDFLARE_MARKERS = (
    "cf-mitigated: challenge",
    "/cdn-cgi/challenge-platform/",
    "just a moment",
    "challenges.cloudflare.com",
    "cf-chl-",
)

BUSINESS_RESULTS: dict[int, tuple[str, str]] = {
    601: ("error", "file not found"),
    602: ("unauthorized", "access denied"),
    603: ("error", "account blocked"),
    604: ("error", "file invalid"),
    # 605 is a permanent quota error in the official app; never treat it as a
    # throttling signal that a retry could clear.
    605: ("error", "not enough transfer"),
    606: ("error", "download error"),
    610: ("error", "directory invalid"),
    401: ("unauthorized", "unauthorized"),
    402: ("unauthorized", "unauthorized"),
}

_MAX_DETAIL_CHARS = 160
_INVALID_SECRET_KEY_RE = re.compile(r"invalid\s+secret\s+key", re.IGNORECASE)
_INTEGER_RE = re.compile(r"-?\d+")


@dataclass(frozen=True)
class FailureDecision:
    """Outcome of classifying one Chomikuj response.

    ``kind`` is ``None`` for success, otherwise one of ``unreachable``,
    ``unauthorized``, ``throttled``, or ``error``.
    """

    kind: str | None
    message: str
    retryable: bool


def _success() -> FailureDecision:
    return FailureDecision(None, "ok", False)


def _normalize_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _detail_excerpt(value: object) -> str:
    """Return a short plain-text excerpt, refusing anything HTML-shaped."""
    text = _normalize_text(value)
    if not text or "<" in text:
        return ""
    return text[:_MAX_DETAIL_CHARS]


def _with_detail(message: str, detail: str) -> str:
    """Append server detail only when it says something the fallback does not."""
    extra = _detail_excerpt(detail)
    if not extra or extra.lower() == message.lower():
        return message
    return f"{message}: {extra}"


def _payload_message(payload: dict[str, object]) -> str:
    for key in ("Message", "message", "error"):
        text = _detail_excerpt(payload.get(key))
        if text:
            return text
    return ""


def _message_from_text(body_text: str) -> str:
    text = _normalize_text(body_text)
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError, ValueError:
        return _detail_excerpt(text)
    if isinstance(payload, dict):
        return _payload_message(payload)
    return ""


def _to_body_text(body: object) -> str:
    if isinstance(body, str):
        return body
    if body is None:
        return ""
    try:
        return json.dumps(body, ensure_ascii=False)
    except TypeError, ValueError:
        return ""


def _to_body_object(body: object) -> dict[str, object] | None:
    if isinstance(body, dict):
        return body
    if not isinstance(body, str):
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError, ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _body_message(body: object) -> str:
    payload = _to_body_object(body)
    if payload is not None:
        return _payload_message(payload)
    return _detail_excerpt(body) if isinstance(body, str) else ""


def _has_cloudflare_challenge(body_text: str) -> bool:
    text = _normalize_text(body_text).lower()
    if not text:
        return False
    return any(marker in text for marker in CLOUDFLARE_MARKERS)


def _is_json_document(text: str) -> bool:
    """Return True when ``text`` parses as any JSON value."""
    try:
        json.loads(text)
    except json.JSONDecodeError, ValueError:
        return False
    return True


def _normalize_status(status: int | str | None) -> int | None:
    if isinstance(status, bool):
        return int(status)
    if isinstance(status, int):
        return status
    if isinstance(status, float):
        return int(status) if math.isfinite(status) else None
    if isinstance(status, str):
        text = status.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
        return int(value) if math.isfinite(value) else None
    return None


def _normalize_code(code: object) -> int | None:
    if isinstance(code, bool):
        return int(code)
    if isinstance(code, int):
        return code
    if isinstance(code, float):
        return int(code) if math.isfinite(code) else None
    if isinstance(code, str) and _INTEGER_RE.fullmatch(code.strip()):
        return int(code.strip())
    return None


def _is_2xx(status: int | None) -> bool:
    return status is not None and 200 <= status < 300


def classify_http(status: int | str | None, body_text: str = "") -> FailureDecision:
    """Classify an HTTP status plus an optional response body.

    Cloudflare interception wins over the status mapping: a challenge page is an
    unreachable transport, not an auth or quota verdict, even when it arrives
    with a 2xx status. JSON bodies are not scanned for challenge markers, so a
    user-visible value such as ``"Just a Moment"`` cannot turn a valid response
    into a challenge; only HTML and plain-text bodies are checked.
    """
    code = _normalize_status(status)
    text = body_text if isinstance(body_text, str) else _to_body_text(body_text)

    if not _is_json_document(text) and _has_cloudflare_challenge(text):
        return FailureDecision("unreachable", "cloudflare challenge", True)

    detail = _message_from_text(text)

    # Status 0 or a non-numeric status means the request never produced an HTTP
    # response (DNS, socket, TLS, or timeout): a retryable transport failure.
    if code == 0 or code is None:
        return FailureDecision("unreachable", _with_detail("network error", detail), True)

    if _is_2xx(code):
        return _success()

    if code == 401:
        return FailureDecision("unauthorized", _with_detail("unauthorized", detail), False)
    if code == 403:
        return FailureDecision("unauthorized", _with_detail("forbidden", detail), False)
    if code == 429:
        return FailureDecision("throttled", _with_detail("throttled", detail), True)
    if 500 <= code < 600:
        return FailureDecision(
            "unreachable", _with_detail(f"server error (HTTP {code})", detail), True
        )
    if 400 <= code < 500:
        return FailureDecision("error", _with_detail(f"client error (HTTP {code})", detail), False)

    return FailureDecision("error", _with_detail(f"unexpected HTTP {status}", detail), False)


def classify_business(code: object, message: str = "") -> FailureDecision:
    """Classify a Chomikuj business code.

    A missing code is success because several successful payloads omit ``Code``.
    Unknown codes stay ``error`` and keep the numeric code in the message so
    logs remain actionable.
    """
    if code is None:
        return _success()

    normalized = _normalize_code(code)
    if normalized is not None and normalized in SUCCESS_CODES:
        return _success()

    mapped = BUSINESS_RESULTS.get(normalized) if normalized is not None else None
    if mapped is not None:
        kind, mapped_message = mapped
        return FailureDecision(kind, _with_detail(mapped_message, message), False)

    label = normalized if normalized is not None else str(code)
    return FailureDecision("error", _with_detail(f"unknown business code {label}", message), False)


def classify_login(status: int | str | None, body: object = None) -> FailureDecision:
    """Classify a ``POST /account/login`` result.

    The caller decides whether to retry; this module only reports whether the
    failure class makes a retry plausible. Login itself must never be retried by
    the client.
    """
    code = _normalize_status(status)
    payload = _to_body_object(body)
    text = _to_body_text(body)
    detail = _body_message(body)

    api_key = ""
    if payload is not None and payload.get("ApiKey") is not None:
        api_key = str(payload.get("ApiKey")).strip()
    invalid_secret_key = bool(_INVALID_SECRET_KEY_RE.search(text))

    if _is_2xx(code) and api_key:
        return _success()

    if not _is_json_document(text) and _has_cloudflare_challenge(text):
        return FailureDecision("unreachable", "cloudflare challenge", True)

    # Status 0 or a non-numeric status means no HTTP response was received.
    if code == 0 or code is None:
        return FailureDecision("unreachable", _with_detail("network error", detail), True)

    if code in (401, 403):
        return FailureDecision("unauthorized", _with_detail("unauthorized", detail), False)
    if code == 429:
        return FailureDecision("throttled", _with_detail("throttled", detail), True)
    if 500 <= code < 600:
        return FailureDecision(
            "unreachable", _with_detail(f"server error (HTTP {code})", detail), True
        )
    if _is_2xx(code) or invalid_secret_key:
        reason = detail or ("invalid secret key" if invalid_secret_key else "missing ApiKey")
        return FailureDecision("unauthorized", _with_detail("unauthorized", reason), False)
    if 400 <= code < 500:
        return FailureDecision("error", _with_detail(f"client error (HTTP {code})", detail), False)

    return FailureDecision("error", _with_detail(f"unexpected HTTP {status}", detail), False)

"""Failure-classification table tests, ported from the BookOrbit plugin.

Every mapping called out by the plan is pinned here so a future change cannot
silently turn the permanent 605 quota error into a retryable one, let a
Cloudflare challenge page look like an authorization failure, or echo raw HTML
into a user-visible message.
"""

import dataclasses

import pytest

from shelfmark.release_sources.chomikuj.failures import (
    FailureDecision,
    classify_business,
    classify_http,
    classify_login,
)

OK = FailureDecision(None, "ok", False)
ALLOWED_KINDS = {None, "unreachable", "unauthorized", "throttled", "error"}
RETRYABLE_KINDS = {"unreachable", "throttled"}


def _check(actual: FailureDecision, expected: FailureDecision) -> None:
    """Assert the exact decision and the module-wide kind/retryability contract."""
    assert actual == expected
    assert actual.kind in ALLOWED_KINDS
    assert actual.retryable is (actual.kind in RETRYABLE_KINDS)


def test_decision_is_a_frozen_dataclass():
    decision = FailureDecision("error", "boom", False)
    assert decision.kind == "error"
    assert decision.message == "boom"
    assert decision.retryable is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.kind = None  # type: ignore[misc]


# --- HTTP success ---------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 204, 206, 299, "200"])
def test_http_2xx_is_success(status):
    _check(classify_http(status), OK)


def test_http_success_ignores_the_body():
    _check(classify_http(200, '{"Message":"ignored"}'), OK)


# --- HTTP status mapping --------------------------------------------------


@pytest.mark.parametrize(
    ("status", "kind", "message", "retryable"),
    [
        (401, "unauthorized", "unauthorized", False),
        (403, "unauthorized", "forbidden", False),
        (429, "throttled", "throttled", True),
        (500, "unreachable", "server error (HTTP 500)", True),
        (502, "unreachable", "server error (HTTP 502)", True),
        (503, "unreachable", "server error (HTTP 503)", True),
        (504, "unreachable", "server error (HTTP 504)", True),
        (599, "unreachable", "server error (HTTP 599)", True),
        (400, "error", "client error (HTTP 400)", False),
        (404, "error", "client error (HTTP 404)", False),
        (405, "error", "client error (HTTP 405)", False),
        (418, "error", "client error (HTTP 418)", False),
        (422, "error", "client error (HTTP 422)", False),
        (302, "error", "unexpected HTTP 302", False),
    ],
)
def test_http_status_mapping(status, kind, message, retryable):
    _check(classify_http(status), FailureDecision(kind, message, retryable))


def test_http_keeps_a_json_server_message():
    _check(
        classify_http(429, '{"Message":"Too many requests","Code":0}'),
        FailureDecision("throttled", "throttled: Too many requests", True),
    )


def test_http_keeps_a_plain_text_server_message():
    _check(
        classify_http(503, "maintenance window"),
        FailureDecision("unreachable", "server error (HTTP 503): maintenance window", True),
    )


def test_http_dedupes_a_matching_message():
    _check(
        classify_http(401, '{"Message":"Unauthorized"}'),
        FailureDecision("unauthorized", "unauthorized", False),
    )


# --- Cloudflare challenge -------------------------------------------------


CLOUDFLARE_BODIES = [
    "cf-mitigated: challenge",
    '<script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>',
    "<title>Just a moment...</title>",
    '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>',
    '<div id="cf-chl-widget-abc"></div>',
]


@pytest.mark.parametrize("status", [403, 503])
@pytest.mark.parametrize("body", CLOUDFLARE_BODIES)
def test_cloudflare_markers_override_the_status_mapping(status, body):
    _check(
        classify_http(status, body),
        FailureDecision("unreachable", "cloudflare challenge", True),
    )


def test_cloudflare_detection_is_case_insensitive():
    _check(
        classify_http(403, "JUST A MOMENT"),
        FailureDecision("unreachable", "cloudflare challenge", True),
    )


def test_cloudflare_on_a_2xx_response_is_still_unreachable():
    _check(
        classify_http(200, "<title>Just a moment...</title>"),
        FailureDecision("unreachable", "cloudflare challenge", True),
    )


# --- Network failures (no HTTP response) ----------------------------------


@pytest.mark.parametrize("status", [0, None, "", "not-a-number"])
def test_http_missing_response_is_a_network_error(status):
    _check(
        classify_http(status),
        FailureDecision("unreachable", "network error", True),
    )


def test_network_error_keeps_a_server_detail():
    _check(
        classify_http(0, '{"Message":"connection reset"}'),
        FailureDecision("unreachable", "network error: connection reset", True),
    )


# --- Business success -----------------------------------------------------


@pytest.mark.parametrize("code", [0, 200, 201, "0", "200", "201", None])
def test_business_success_codes(code):
    _check(classify_business(code), OK)


def test_business_success_ignores_the_message():
    _check(classify_business(0, "ignored"), OK)


# --- Business code mapping ------------------------------------------------


@pytest.mark.parametrize(
    ("code", "kind", "message", "retryable"),
    [
        (601, "error", "file not found", False),
        (602, "unauthorized", "access denied", False),
        (603, "error", "account blocked", False),
        (604, "error", "file invalid", False),
        (605, "error", "not enough transfer", False),
        (606, "error", "download error", False),
        (610, "error", "directory invalid", False),
        (401, "unauthorized", "unauthorized", False),
        (402, "unauthorized", "unauthorized", False),
    ],
)
def test_business_code_mapping(code, kind, message, retryable):
    _check(classify_business(code), FailureDecision(kind, message, retryable))


def test_business_605_is_a_permanent_error():
    assert classify_business(605).kind == "error"
    assert classify_business(605).retryable is False
    assert classify_business(605).kind != "throttled"
    _check(
        classify_business(605, "Not enough transfer"),
        FailureDecision("error", "not enough transfer", False),
    )


def test_unknown_code_stays_actionable():
    _check(
        classify_business(699),
        FailureDecision("error", "unknown business code 699", False),
    )
    _check(
        classify_business("777"),
        FailureDecision("error", "unknown business code 777", False),
    )
    _check(
        classify_business(699, "server says no"),
        FailureDecision("error", "unknown business code 699: server says no", False),
    )


def test_business_detail_is_appended_only_when_it_adds_information():
    _check(
        classify_business(601, "Custom detail"),
        FailureDecision("error", "file not found: Custom detail", False),
    )
    _check(
        classify_business(602, "Access denied"),
        FailureDecision("unauthorized", "access denied", False),
    )


# --- Login ----------------------------------------------------------------


@pytest.mark.parametrize("status", [200, 201, 204])
def test_login_2xx_with_api_key_is_success(status):
    _check(classify_login(status, {"ApiKey": "test-key"}), OK)


def test_login_accepts_a_raw_json_string_body():
    _check(classify_login(200, '{"ApiKey":"test-key"}'), OK)


def test_login_trims_a_whitespace_api_key():
    _check(classify_login(200, {"ApiKey": "  test-key  "}), OK)


@pytest.mark.parametrize("status", [0, None, ""])
def test_login_missing_response_is_a_network_error(status):
    _check(
        classify_login(status, None),
        FailureDecision("unreachable", "network error", True),
    )


@pytest.mark.parametrize("body", [{}, {"ApiKey": ""}, {"ApiKey": "   "}])
def test_login_2xx_without_api_key_is_unauthorized(body):
    _check(
        classify_login(200, body),
        FailureDecision("unauthorized", "unauthorized: missing ApiKey", False),
    )


def test_login_invalid_secret_key_from_json_is_unauthorized():
    _check(
        classify_login(200, {"Message": "invalid secret key"}),
        FailureDecision("unauthorized", "unauthorized: invalid secret key", False),
    )


def test_login_invalid_secret_key_from_raw_text_is_unauthorized():
    _check(
        classify_login(200, "invalid secret key"),
        FailureDecision("unauthorized", "unauthorized: invalid secret key", False),
    )


def test_login_api_key_wins_over_invalid_secret_text():
    _check(classify_login(200, {"ApiKey": "k", "Message": "invalid secret key"}), OK)


def test_login_401_is_unauthorized_with_detail():
    _check(
        classify_login(401, {"Message": "Bad credentials"}),
        FailureDecision("unauthorized", "unauthorized: Bad credentials", False),
    )


def test_login_403_is_unauthorized():
    _check(classify_login(403), FailureDecision("unauthorized", "unauthorized", False))


def test_login_429_is_throttled():
    _check(
        classify_login(429, {"Message": "slow down"}),
        FailureDecision("throttled", "throttled: slow down", True),
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (500, FailureDecision("unreachable", "server error (HTTP 500)", True)),
        (503, FailureDecision("unreachable", "server error (HTTP 503): down", True)),
    ],
)
def test_login_5xx_is_unreachable(status, expected):
    body = None if status == 500 else "down"
    _check(classify_login(status, body), expected)


@pytest.mark.parametrize("status", [403, 200])
def test_login_cloudflare_is_unreachable(status):
    _check(
        classify_login(status, "<title>Just a moment...</title>"),
        FailureDecision("unreachable", "cloudflare challenge", True),
    )


def test_login_400_is_an_error():
    _check(
        classify_login(400, "bad request"),
        FailureDecision("error", "client error (HTTP 400): bad request", False),
    )


# --- Message hygiene ------------------------------------------------------


def test_html_never_echoed():
    assert classify_http(500, "<html>boom</html>") == FailureDecision(
        "unreachable", "server error (HTTP 500)", True
    )
    assert "<" not in classify_http(500, "<html>boom</html>").message
    assert classify_http(500, '{"Message":"<b>boom</b>"}').message == "server error (HTTP 500)"
    assert classify_business(699, "<html>").message == "unknown business code 699"
    assert classify_login(400, "<html>").message == "client error (HTTP 400)"


def test_long_plain_text_excerpt_is_capped_at_160_chars():
    message = classify_http(503, "x" * 500).message
    assert message.startswith("server error (HTTP 503): ")
    assert len(message.removeprefix("server error (HTTP 503): ")) == 160


# --- Contract sweeps ------------------------------------------------------


def test_kind_and_retryable_contract():
    decisions = [
        classify_http(429),
        classify_http(500),
        classify_http(401),
        classify_http(404),
        classify_business(601),
        classify_business(602),
        classify_business(699),
        classify_login(429, None),
        classify_login(401, None),
        classify_login(200, {}),
    ]
    for decision in decisions:
        assert decision.kind in ALLOWED_KINDS
        assert decision.retryable is (decision.kind in RETRYABLE_KINDS)


def test_success_is_the_only_decision_without_a_kind():
    successful = [
        classify_http(200),
        classify_business(0),
        classify_business(None),
        classify_login(200, {"ApiKey": "k"}),
    ]
    for decision in successful:
        assert decision.kind is None
        assert decision.message == "ok"
        assert decision.retryable is False

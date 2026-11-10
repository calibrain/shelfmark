"""Chomikuj client request-core tests.

All tests inject a fake session: no network call is made. The fake records the
exact method, URL, headers, body, timeout, proxy map, and TLS-verify flag so the
tests pin the wire contract the mobile API expects.
"""

import json
import time
from email.utils import formatdate
from urllib.parse import urlencode

import pytest

from shelfmark.release_sources.chomikuj import client as chomikuj_client
from shelfmark.release_sources.chomikuj.client import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    ChomikujAuthError,
    ChomikujClient,
    ChomikujError,
    normalize_base_url,
)
from shelfmark.release_sources.chomikuj.token import sign_token

PROXY_MAP = {"https": "http://proxy.test:8080"}


class FakeResponse:
    """Minimal HTTP response stand-in."""

    def __init__(
        self,
        status_code: int,
        text: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Initialize the fake response with its status, text, and headers."""
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeSession:
    """Minimal `requests.Session` stand-in that records every request."""

    def __init__(self, *responses: FakeResponse) -> None:
        """Initialize the fake session with its scripted responses."""
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def request(self, method: str, url: str, **kwargs: object) -> FakeResponse:
        """Record the request and return the next scripted response."""
        self.calls.append({"method": method, "url": url, **kwargs})
        if not self._responses:
            raise AssertionError("unexpected extra request")
        return self._responses.pop(0)


@pytest.fixture
def network_spy(monkeypatch):
    """Pin proxy/TLS resolution and record the URLs they were asked about."""
    seen: list[tuple[str, str]] = []

    def fake_get_proxies(url: str) -> dict[str, str]:
        """Record the URL and return the fixed proxy map."""
        seen.append(("proxies", url))
        return PROXY_MAP

    def fake_get_ssl_verify(url: str) -> bool:
        """Record the URL and report verification disabled."""
        seen.append(("verify", url))
        return False

    monkeypatch.setattr(chomikuj_client.network, "get_proxies", fake_get_proxies)
    monkeypatch.setattr(chomikuj_client.network, "get_ssl_verify", fake_get_ssl_verify)
    return seen


# --- base URL -------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", DEFAULT_BASE_URL),
        ("   ", DEFAULT_BASE_URL),
        (None, DEFAULT_BASE_URL),
        (123, DEFAULT_BASE_URL),
        ("https://x.test/", "https://x.test/api/v3"),
        ("  https://x.test  ", "https://x.test/api/v3"),
        ("https://x.test/api/v3/", "https://x.test/api/v3"),
        ("https://x.test/api/v3", "https://x.test/api/v3"),
    ],
)
def test_normalize_base_url(value, expected):
    """Test normalize base url."""
    assert normalize_base_url(value) == expected


def test_normalize_base_url_strips_all_trailing_slashes():
    """Test normalize base url strips all trailing slashes."""
    assert normalize_base_url("https://x.test///") == "https://x.test/api/v3"


# --- headers --------------------------------------------------------------


def test_headers_carry_user_agent_and_signed_token_only():
    """Test headers carry user agent and signed token only."""
    client = ChomikujClient(session=FakeSession())
    body = '{"AccountName":"alice","Password":"p@ss"}'
    headers = client._headers("account/login", "", None, body)

    assert headers == {
        "User-Agent": DEFAULT_USER_AGENT,
        "Token": sign_token("api/v3/account/login", "", body),
    }
    assert "Api-Key" not in headers


def test_headers_sign_the_exact_query_and_include_api_key_only_when_supplied():
    """Test headers sign the exact query and include api key only when supplied."""
    client = ChomikujClient(session=FakeSession())
    query = "Query=test&PageNumber=1&MediaType=Documents"

    headers = client._headers("files/search", query, "KEY", "")

    assert headers["Api-Key"] == "KEY"
    assert headers["Token"] == sign_token("api/v3/files/search", query, "")
    assert "Api-Key" not in client._headers("files/search", query, "", "")


# --- login ----------------------------------------------------------------


def test_login_posts_one_compact_body_without_api_key(network_spy):
    """Test login posts one compact body without api key."""
    session = FakeSession(FakeResponse(200, '{"ApiKey":"K","AccountId":42}'))
    client = ChomikujClient(session=session)

    payload = client.login("alice", "p@ss")

    assert payload == {"ApiKey": "K", "AccountId": 42}
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == f"{DEFAULT_BASE_URL}/account/login"
    body = call["data"].decode("utf-8")
    assert body == '{"AccountName":"alice","Password":"p@ss"}'
    headers = call["headers"]
    assert "Api-Key" not in headers
    assert headers["User-Agent"] == DEFAULT_USER_AGENT
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Token"] == sign_token("api/v3/account/login", "", body)
    assert call["timeout"] == DEFAULT_TIMEOUT
    assert call["proxies"] == PROXY_MAP
    assert call["verify"] is False
    assert network_spy == [
        ("proxies", f"{DEFAULT_BASE_URL}/account/login"),
        ("verify", f"{DEFAULT_BASE_URL}/account/login"),
    ]


def test_login_encodes_unicode_body_unraised(network_spy):
    """Test login encodes unicode body unraised."""
    session = FakeSession(FakeResponse(200, '{"ApiKey":"K"}'))
    client = ChomikujClient(session=session)

    client.login("żółć", "päss")

    body = session.calls[0]["data"].decode("utf-8")
    assert body == '{"AccountName":"żółć","Password":"päss"}'
    assert "\\u" not in body
    assert session.calls[0]["headers"]["Token"] == sign_token("api/v3/account/login", "", body)


def test_login_uses_normalized_custom_base_url_user_agent_and_timeout(network_spy):
    """Test login uses normalized custom base url user agent and timeout."""
    session = FakeSession(FakeResponse(200, '{"ApiKey":"K"}'))
    client = ChomikujClient(
        "https://x.test/",
        user_agent="custom/1",
        session=session,
        timeout=(1, 2),
    )

    client.login("alice", "p@ss")

    call = session.calls[0]
    assert call["url"] == "https://x.test/api/v3/account/login"
    assert call["headers"]["User-Agent"] == "custom/1"
    assert call["timeout"] == (1, 2)


def test_login_401_raises_auth_error_without_leaking_secrets(network_spy):
    """Test login 401 raises auth error without leaking secrets."""
    session = FakeSession(FakeResponse(401, '{"Message":"invalid secret key"}'))
    client = ChomikujClient(session=session)

    with pytest.raises(ChomikujAuthError) as excinfo:
        client.login("alice", "s3cret-pw")

    error = excinfo.value
    assert isinstance(error, ChomikujError)
    assert error.kind == "unauthorized"
    assert error.status == 401
    assert error.retryable is False
    assert "s3cret-pw" not in str(error)
    assert "s3cret-pw" not in repr(error)


def test_login_invalid_json_raises_plain_error(network_spy):
    """Test login invalid json raises plain error."""
    session = FakeSession(FakeResponse(200, "<html>not json</html>"))
    client = ChomikujClient(session=session)

    with pytest.raises(ChomikujError) as excinfo:
        client.login("alice", "p@ss")

    error = excinfo.value
    assert not isinstance(error, ChomikujAuthError)
    assert error.kind == "error"
    assert error.status == 200
    assert "not json" not in str(error)


def test_login_failure_never_contains_the_password_or_body(network_spy):
    """Test login failure never contains the password or body."""
    response_body = '{"Message":"login rejected"}'
    session = FakeSession(FakeResponse(401, response_body))
    client = ChomikujClient(session=session)

    with pytest.raises(ChomikujError) as excinfo:
        client.login("alice", "TopSecret!")

    message = str(excinfo.value)
    assert "TopSecret!" not in message
    assert "Password" not in message
    assert response_body not in message


def test_login_never_retries_on_429(network_spy):
    """Test login never retries on 429."""
    session = FakeSession(
        FakeResponse(429, '{"Message":"throttled"}'),
        FakeResponse(200, '{"ApiKey":"K"}'),
    )
    sleeps: list[float] = []
    client = ChomikujClient(session=session, sleep=sleeps.append)

    with pytest.raises(ChomikujError) as excinfo:
        client.login("alice", "p@ss")

    assert excinfo.value.kind == "throttled"
    assert len(session.calls) == 1
    assert sleeps == []


# --- account info ---------------------------------------------------------


def make_client(*responses: FakeResponse) -> tuple[ChomikujClient, FakeSession, list[float]]:
    """Build a client with a fake session and recorded sleeps."""
    session = FakeSession(*responses)
    sleeps: list[float] = []
    client = ChomikujClient(session=session, sleep=sleeps.append)
    return client, session, sleeps


def test_account_info_gets_exact_url_with_signed_empty_query(network_spy):
    """Test account info gets exact url with signed empty query."""
    payload = {"Code": 0, "HasUnlimitedTransfer": True, "BalanceInfo": "1 GB"}
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":0,"HasUnlimitedTransfer":true,"BalanceInfo":"1 GB"}')
    )

    assert client.account_info("KEY") == payload

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{DEFAULT_BASE_URL}/account/info"
    assert call["data"] is None
    assert call["headers"]["Api-Key"] == "KEY"
    assert call["headers"]["Token"] == sign_token("api/v3/account/info", "", "")
    assert call["timeout"] == DEFAULT_TIMEOUT
    assert sleeps == []


@pytest.mark.parametrize("status", [429, 503])
def test_account_retry_once_on_transient_status(network_spy, status):
    """Test account retry once on transient status."""
    client, session, sleeps = make_client(
        FakeResponse(status, '{"Message":"busy"}'),
        FakeResponse(200, '{"Code":0,"HasUnlimitedTransfer":false}'),
    )

    payload = client.account_info("KEY")

    assert payload == {"Code": 0, "HasUnlimitedTransfer": False}
    assert len(session.calls) == 2
    assert session.calls[0]["url"] == session.calls[1]["url"]
    assert session.calls[0]["headers"] == session.calls[1]["headers"]
    assert sleeps == [0.5]


def test_account_info_makes_at_most_one_retry(network_spy):
    """Test account info makes at most one retry."""
    client, session, sleeps = make_client(
        FakeResponse(429, '{"Message":"throttled"}'),
        FakeResponse(503, '{"Message":"still down"}'),
        FakeResponse(200, '{"Code":0}'),
    )

    with pytest.raises(ChomikujError) as excinfo:
        client.account_info("KEY")

    assert excinfo.value.kind == "unreachable"
    assert excinfo.value.status == 503
    assert len(session.calls) == 2
    assert sleeps == [0.5]


def test_account_info_does_not_retry_401(network_spy):
    """Test account info does not retry 401."""
    client, session, sleeps = make_client(FakeResponse(401, '{"Code":401}'))

    with pytest.raises(ChomikujAuthError) as excinfo:
        client.account_info("KEY")

    assert excinfo.value.kind == "unauthorized"
    assert len(session.calls) == 1
    assert sleeps == []


def test_account_info_honors_retry_after_under_the_cap(network_spy):
    """Test account info honors retry after under the cap."""
    client, session, sleeps = make_client(
        FakeResponse(429, '{"Message":"slow down"}', {"Retry-After": "0.75"}),
        FakeResponse(200, '{"Code":0}'),
    )

    assert client.account_info("KEY") == {"Code": 0}
    assert len(session.calls) == 2
    assert sleeps == [0.75]


def test_retry_after_too_long_suppresses_the_retry(network_spy):
    """Test retry after too long suppresses the retry."""
    client, session, sleeps = make_client(
        FakeResponse(429, '{"Message":"slow down"}', {"Retry-After": "3"}),
        FakeResponse(200, '{"Code":0}'),
    )

    with pytest.raises(ChomikujError) as excinfo:
        client.account_info("KEY")

    assert excinfo.value.kind == "throttled"
    assert len(session.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    ("offset_seconds", "expected_sleeps"),
    [(-10, [0.5]), (10, [])],
)
def test_account_info_parses_http_date_retry_after(network_spy, offset_seconds, expected_sleeps):
    """Test account info parses http date retry after."""
    retry_at = formatdate(time.time() + offset_seconds, usegmt=True)
    responses = [FakeResponse(429, '{"Message":"slow down"}', {"Retry-After": retry_at})]
    responses.append(FakeResponse(200, '{"Code":0}'))
    client, session, sleeps = make_client(*responses)

    if expected_sleeps:
        assert client.account_info("KEY") == {"Code": 0}
    else:
        with pytest.raises(ChomikujError) as excinfo:
            client.account_info("KEY")
        assert excinfo.value.kind == "throttled"

    assert sleeps == expected_sleeps
    assert len(session.calls) == 1 + len(expected_sleeps)


def test_cloudflare_no_retry_on_account_info(network_spy):
    """Test cloudflare no retry on account info."""
    challenge = (
        "<html><head><title>Just a moment...</title></head>"
        '<body><script src="/cdn-cgi/challenge-platform/h/b/orchestrate"></script>'
        "</body></html>"
    )
    client, session, sleeps = make_client(
        FakeResponse(503, challenge),
        FakeResponse(200, '{"Code":0}'),
    )

    with pytest.raises(ChomikujError) as excinfo:
        client.account_info("KEY")

    assert excinfo.value.kind == "unreachable"
    assert "cloudflare challenge" in str(excinfo.value)
    assert len(session.calls) == 1
    assert sleeps == []


def test_account_info_raises_on_non_success_business_code(network_spy):
    """Test account info raises on non success business code."""
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":605,"Message":"Not enough transfer"}')
    )

    with pytest.raises(ChomikujError) as excinfo:
        client.account_info("KEY")

    error = excinfo.value
    assert error.kind == "error"
    assert error.retryable is False
    assert error.status == 200
    assert error.code == 605
    assert "not enough transfer" in str(error).lower()
    assert len(session.calls) == 1
    assert sleeps == []


def test_account_info_invalid_json_raises_plain_error(network_spy):
    """Test account info invalid json raises plain error."""
    client, session, sleeps = make_client(FakeResponse(200, "<html>not json</html>"))

    with pytest.raises(ChomikujError) as excinfo:
        client.account_info("KEY")

    assert excinfo.value.kind == "error"
    assert excinfo.value.status == 200
    assert "not json" not in str(excinfo.value)
    assert len(session.calls) == 1
    assert sleeps == []


# --- search ---------------------------------------------------------------


def test_search_query_builds_ordered_string_and_signs_exact_token(network_spy):
    """Test search query builds ordered string and signs exact token."""
    payload = {"Code": 0, "Results": [{"FileId": "1"}], "IsNextPageAvailable": False}
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":0,"Results":[{"FileId":"1"}],"IsNextPageAvailable":false}')
    )

    assert client.search_page("KEY", query="test", page=1) == payload

    expected_query = "Query=test&PageNumber=1&MediaType=Documents"
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{DEFAULT_BASE_URL}/files/search?{expected_query}"
    assert call["headers"]["Api-Key"] == "KEY"
    assert call["headers"]["Token"] == sign_token("api/v3/files/search", expected_query, "")
    assert sleeps == []


def test_search_page_sends_extension_last_and_omits_empty_extension(network_spy):
    """Test search page sends extension last and omits empty extension."""
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":0,"Results":[]}'),
        FakeResponse(200, '{"Code":0,"Results":[]}'),
    )

    client.search_page("KEY", query="test", page=1, extension="pdf")
    client.search_page("KEY", query="test", page=2, extension="")

    first_query = "Query=test&PageNumber=1&MediaType=Documents&Extension=pdf"
    second_query = "Query=test&PageNumber=2&MediaType=Documents"
    assert session.calls[0]["url"] == f"{DEFAULT_BASE_URL}/files/search?{first_query}"
    assert session.calls[0]["headers"]["Token"] == sign_token(
        "api/v3/files/search", first_query, ""
    )
    assert session.calls[1]["url"] == f"{DEFAULT_BASE_URL}/files/search?{second_query}"
    assert session.calls[1]["headers"]["Token"] == sign_token(
        "api/v3/files/search", second_query, ""
    )
    assert sleeps == []


def test_search_page_signs_the_urlencoded_unicode_query_it_sends(network_spy):
    """Test search page signs the urlencoded unicode query it sends."""
    client, session, sleeps = make_client(FakeResponse(200, '{"Code":0,"Results":[]}'))

    client.search_page("KEY", query="żółć test", page=3)

    expected_query = urlencode(
        [("Query", "żółć test"), ("PageNumber", 3), ("MediaType", "Documents")]
    )
    assert expected_query == "Query=%C5%BC%C3%B3%C5%82%C4%87+test&PageNumber=3&MediaType=Documents"
    assert session.calls[0]["url"] == f"{DEFAULT_BASE_URL}/files/search?{expected_query}"
    assert session.calls[0]["headers"]["Token"] == sign_token(
        "api/v3/files/search", expected_query, ""
    )
    assert sleeps == []


def test_search_page_retries_once_on_503_and_reuses_the_exact_request(network_spy):
    """Test search page retries once on 503 and reuses the exact request."""
    client, session, sleeps = make_client(
        FakeResponse(503, '{"Message":"unavailable"}'),
        FakeResponse(200, '{"Code":0,"Results":[]}'),
    )

    assert client.search_page("KEY", query="test", page=1) == {"Code": 0, "Results": []}
    assert len(session.calls) == 2
    assert session.calls[0]["url"] == session.calls[1]["url"]
    assert session.calls[0]["headers"] == session.calls[1]["headers"]
    assert sleeps == [0.5]


def test_search_page_forwards_the_per_request_timeout_to_every_attempt(network_spy):
    """Test search page forwards the per request timeout to every attempt."""
    client, session, sleeps = make_client(
        FakeResponse(429, '{"Message":"throttled"}'),
        FakeResponse(200, '{"Code":0,"Results":[]}'),
    )

    client.search_page("KEY", query="test", page=1, timeout=0.25)

    assert [call["timeout"] for call in session.calls] == [0.25, 0.25]
    assert sleeps == [0.5]


def test_search_page_raises_on_non_success_business_code(network_spy):
    """Test search page raises on non success business code."""
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":602,"Message":"Access denied"}')
    )

    with pytest.raises(ChomikujAuthError) as excinfo:
        client.search_page("KEY", query="test", page=1)

    assert excinfo.value.kind == "unauthorized"
    assert excinfo.value.code == 602
    assert len(session.calls) == 1
    assert sleeps == []


def test_search_page_invalid_json_raises_plain_error(network_spy):
    """Test search page invalid json raises plain error."""
    client, session, sleeps = make_client(FakeResponse(200, "<html>not json</html>"))

    with pytest.raises(ChomikujError) as excinfo:
        client.search_page("KEY", query="test", page=1)

    assert excinfo.value.kind == "error"
    assert excinfo.value.status == 200
    assert "not json" not in str(excinfo.value)
    assert len(session.calls) == 1
    assert sleeps == []


# --- download -------------------------------------------------------------


def test_download_no_retry_sends_exactly_one_request_even_on_429(network_spy):
    """Test download no retry sends exactly one request even on 429."""
    client, session, sleeps = make_client(
        FakeResponse(429, '{"Message":"throttled"}', {"Retry-After": "0"}),
        FakeResponse(200, '{"Code":0,"FileUrl":"https://cdn.test/file"}'),
    )

    with pytest.raises(ChomikujError) as excinfo:
        client.download_url("KEY", 200000001)

    assert excinfo.value.kind == "throttled"
    assert excinfo.value.status == 429
    assert len(session.calls) == 1
    assert sleeps == []

    expected_query = "FileId=200000001"
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == f"{DEFAULT_BASE_URL}/files/download?{expected_query}"
    assert call["headers"]["Api-Key"] == "KEY"
    assert call["headers"]["Token"] == sign_token("api/v3/files/download", expected_query, "")


def test_download_url_urlencodes_the_file_id(network_spy):
    """Test download url urlencodes the file id."""
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":0,"FileUrl":"https://cdn.test/file"}')
    )

    client.download_url("KEY", "a b/c")

    assert session.calls[0]["url"].endswith("/files/download?FileId=a%20b%2Fc")
    assert session.calls[0]["headers"]["Token"] == sign_token(
        "api/v3/files/download", "FileId=a%20b%2Fc", ""
    )
    assert sleeps == []


def test_download_url_returns_the_payload_with_a_file_url(network_spy):
    """Test download url returns the payload with a file url."""
    payload = {"Code": 0, "FileUrl": "https://cdn.test/file"}
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":0,"FileUrl":"https://cdn.test/file"}')
    )

    assert client.download_url("KEY", 42) == payload
    assert len(session.calls) == 1
    assert sleeps == []


def test_download_url_605_is_a_permanent_quota_error(network_spy):
    """Test download url 605 is a permanent quota error."""
    client, session, sleeps = make_client(
        FakeResponse(200, '{"Code":605,"Message":"Not enough transfer"}')
    )

    with pytest.raises(ChomikujError) as excinfo:
        client.download_url("KEY", 200000001)

    error = excinfo.value
    assert error.kind == "error"
    assert error.retryable is False
    assert error.code == 605
    assert "not enough transfer" in str(error).lower()
    assert len(session.calls) == 1
    assert sleeps == []


@pytest.mark.parametrize(
    "payload",
    [
        {"Code": 0, "Message": "ok"},
        {"Code": 0, "FileUrl": ""},
        {"Code": 0, "FileUrl": "   "},
    ],
)
def test_download_url_missing_file_url_raises(network_spy, payload):
    """Test download url missing file url raises."""
    client, session, sleeps = make_client(FakeResponse(200, json.dumps(payload)))

    with pytest.raises(ChomikujError) as excinfo:
        client.download_url("KEY", 1)

    assert excinfo.value.kind == "error"
    assert "fileurl" in str(excinfo.value).lower()
    assert len(session.calls) == 1
    assert sleeps == []


def test_download_url_invalid_json_raises_plain_error(network_spy):
    """Test download url invalid json raises plain error."""
    client, session, sleeps = make_client(FakeResponse(200, "<html>not json</html>"))

    with pytest.raises(ChomikujError) as excinfo:
        client.download_url("KEY", 1)

    assert excinfo.value.kind == "error"
    assert excinfo.value.status == 200
    assert "not json" not in str(excinfo.value)
    assert len(session.calls) == 1
    assert sleeps == []

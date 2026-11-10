"""NetworkSession applies the Settings → Network proxy and TLS rules to every request."""

import pytest

from shelfmark.download import network

_PROXY = "http://proxy.example.com:8118"


@pytest.fixture
def settings(monkeypatch):
    """Network settings the session reads, editable by each test."""
    values = {"PROXY_MODE": "http", "HTTP_PROXY": _PROXY, "NO_PROXY": ""}

    def fake_get(key, default=""):
        return values.get(key, default)

    monkeypatch.setattr(network.app_config, "get", fake_get)
    return values


def test_a_remote_host_goes_through_the_proxy(settings, sent_requests):
    """A public URL gets the configured proxy."""
    network.NetworkSession().get("https://api.hardcover.app/v1/graphql")

    assert sent_requests.last()["proxies"] == {"http": _PROXY, "https": _PROXY}


def test_a_lan_host_goes_direct(settings, sent_requests):
    """A local URL gets no proxy, with No Proxy empty."""
    network.NetworkSession().get("http://prowlarr:9696/api/v1/system/status")

    assert sent_requests.last()["proxies"] == {}


@pytest.mark.parametrize(
    ("mode", "url", "verify"),
    [
        ("enabled", "https://192.168.1.10", True),
        ("enabled", "https://api.hardcover.app", True),
        ("disabled", "https://192.168.1.10", False),
        ("disabled", "https://api.hardcover.app", False),
        ("disabled_local", "https://192.168.1.10", False),
        ("disabled_local", "https://api.hardcover.app", True),
    ],
)
def test_verify_follows_certificate_validation(settings, sent_requests, mode, url, verify):
    """``verify`` comes from Certificate Validation for each request's own URL."""
    settings["CERTIFICATE_VALIDATION"] = mode

    network.NetworkSession().get(url)

    assert sent_requests.last()["verify"] is verify


def test_explicit_arguments_from_the_caller_win(settings, sent_requests):
    """A caller that passes ``proxies`` or ``verify`` keeps them."""
    network.NetworkSession().get(
        "https://api.hardcover.app", proxies={"https": "http://other:3128"}, verify=False
    )

    assert sent_requests.last()["proxies"] == {"https": "http://other:3128"}
    assert sent_requests.last()["verify"] is False


def test_a_settings_change_applies_to_the_next_request(settings, sent_requests):
    """The rules are read per request, so nothing needs a restart."""
    session = network.NetworkSession()
    session.get("https://api.hardcover.app")
    settings["PROXY_MODE"] = "none"
    session.get("https://api.hardcover.app")

    assert [call["proxies"] for call in sent_requests.calls] == [
        {"http": _PROXY, "https": _PROXY},
        {},
    ]


def _offer_cookie(session):
    """Hand ``session`` a response that sets a cookie, the way requests does after a reply."""
    import requests

    request = requests.Request("GET", "https://api.hardcover.app/").prepare()
    requests.cookies.extract_cookies_to_jar(session.cookies, request, _RawWithCookie())


def test_the_shared_session_is_one_instance_that_keeps_no_cookies(settings):
    """get_session() reuses one session, and it never stores a cookie from a response."""
    shared = network.get_session()
    own = network.NetworkSession()

    _offer_cookie(shared)
    _offer_cookie(own)

    assert network.get_session() is shared
    assert len(shared.cookies) == 0
    assert len(own.cookies) == 1  # the same reply does set a cookie on a session of its own


class _RawWithCookie:
    """Just enough of urllib3's response for requests to read a Set-Cookie header."""

    class _Headers:
        def get_all(self, name, default=None):
            return ["tracker=1; Path=/"] if name.lower() == "set-cookie" else default

    _original_response = None

    def __init__(self):
        self._original_response = self
        self.msg = self._Headers()

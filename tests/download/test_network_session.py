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


def _proxy_after_requests_merges_the_environment(url, proxies):
    """The proxy requests would actually use for ``url``, environment variables included."""
    import requests
    from requests.utils import select_proxy

    merged = requests.Session().merge_environment_settings(url, dict(proxies), None, None, None)
    return select_proxy(url, merged["proxies"])


@pytest.mark.parametrize("environment_proxy", [None, "http://env-proxy.example.com:3128"])
def test_a_lan_host_goes_direct(settings, sent_requests, monkeypatch, environment_proxy):
    """A local URL goes direct, even with a proxy in the container's environment."""
    url = "http://prowlarr:9696/api/v1/system/status"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    if environment_proxy:
        monkeypatch.setenv("HTTP_PROXY", environment_proxy)
        monkeypatch.setenv("ALL_PROXY", environment_proxy)

    network.NetworkSession().get(url)

    assert (
        _proxy_after_requests_merges_the_environment(url, sent_requests.last()["proxies"]) is None
    )


def test_an_environment_proxy_still_applies_when_settings_have_none(
    settings, sent_requests, monkeypatch
):
    """With no proxy in Settings, a public host keeps the proxy from the environment."""
    url = "https://api.hardcover.app/v1/graphql"
    settings["PROXY_MODE"] = "none"
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.example.com:3128")

    network.NetworkSession().get(url)

    proxy = _proxy_after_requests_merges_the_environment(url, sent_requests.last()["proxies"])
    assert proxy == "http://env-proxy.example.com:3128"


@pytest.mark.parametrize(
    ("mode", "url", "verify"),
    [
        ("enabled", "https://192.168.1.10", True),
        ("enabled", "https://api.hardcover.app", True),
        ("disabled", "https://192.168.1.10", False),
        ("disabled", "https://api.hardcover.app", False),
        ("disabled_local", "https://192.168.1.10", False),
        ("disabled_local", "https://api.hardcover.app", True),
        ("disabled_local", "https://[fd12:3456::7]", False),
        ("disabled_local", "https://[2606:4700::1]", True),
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
    ]  # an empty mapping: nothing in Settings, and the host is not one that bypasses


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
            """Return the Set-Cookie value, like urllib3's header map."""
            return ["tracker=1; Path=/"] if name.lower() == "set-cookie" else default

    _original_response = None

    def __init__(self):
        self._original_response = self
        self.msg = self._Headers()


def _redirect_hop(url):
    """A prepared request for a redirect hop to ``url``, as requests builds it."""
    import requests

    return requests.Request("GET", url).prepare()


def test_a_redirect_from_the_lan_to_the_internet_goes_through_the_proxy(settings):
    """A local link that redirects to a public host gets the proxy for that hop."""
    session = network.NetworkSession()

    hop = session.rebuild_proxies(
        _redirect_hop("https://indexer.example.com/getnzb/1"),
        {"http": None, "https": None, "all": None},
    )

    assert hop == {"http": _PROXY, "https": _PROXY}


def test_a_redirect_from_the_internet_to_the_lan_goes_direct(settings):
    """A public link that redirects to a local host goes direct for that hop."""
    hop_request = _redirect_hop("http://192.168.1.20:8080/file")
    hop_request.headers["Proxy-Authorization"] = "Basic x"

    hop = network.NetworkSession().rebuild_proxies(hop_request, {"http": _PROXY, "https": _PROXY})

    assert hop == {}
    assert "Proxy-Authorization" not in hop_request.headers


def test_a_redirect_with_no_proxy_in_settings_keeps_the_environment_behaviour(
    settings, monkeypatch
):
    """With nothing in Settings, a hop still picks up a proxy from the environment."""
    settings["PROXY_MODE"] = "none"
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://env-proxy.example.com:3128")

    hop = network.NetworkSession().rebuild_proxies(_redirect_hop("https://example.org/"), {})

    assert hop.get("https") == "http://env-proxy.example.com:3128"


def test_explicit_proxies_are_kept_across_redirects(settings, monkeypatch):
    """Proxies a caller passed are not replaced on a redirect hop."""
    session = network.NetworkSession()
    hops = []

    def record_hop(prepared_request, proxies):
        hops.append(network.NetworkSession.rebuild_proxies(session, prepared_request, proxies))
        return hops[-1]

    import requests

    def fake_send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        # One redirect hop to a public host, decided while the request is in flight.
        record_hop(_redirect_hop("https://indexer.example.com/getnzb/1"), kwargs["proxies"])
        return response

    monkeypatch.setattr(requests.Session, "send", fake_send)

    session.get("http://192.168.1.20/", proxies={"https": "http://chosen:1"})
    session.get("http://192.168.1.20/")

    assert hops[0].get("https") == "http://chosen:1"
    assert hops[1] == {"http": _PROXY, "https": _PROXY}


@pytest.fixture
def sent_hops(monkeypatch):
    """Record the ``verify`` each hop reaches the transport with."""
    import requests

    # requests swaps verify=True for a bundle named in these, so keep the host's out.
    for name in ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)
    hops = []

    def fake_send(self, request, **kwargs):
        hops.append((request.url, kwargs.get("verify")))
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.request = request
        return response

    monkeypatch.setattr(requests.Session, "send", fake_send)
    return hops


def test_verify_is_chosen_again_for_a_redirect_hop(settings, sent_hops):
    """Under disabled_local, a hop from a local link to a public host is verified."""
    settings["CERTIFICATE_VALIDATION"] = "disabled_local"
    session = network.NetworkSession()

    session.send(_redirect_hop("https://auth.example.com/jwks"), verify=False)
    session.send(_redirect_hop("https://192.168.1.20/jwks"), verify=True)

    assert sent_hops == [
        ("https://auth.example.com/jwks", True),
        ("https://192.168.1.20/jwks", False),
    ]


def test_a_ca_bundle_from_the_environment_is_kept_for_a_verified_hop(
    settings, sent_hops, monkeypatch
):
    """Turning verification back on for a hop uses the CA bundle requests would have used."""
    settings["CERTIFICATE_VALIDATION"] = "disabled_local"
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/etc/ssl/custom.pem")

    network.NetworkSession().send(_redirect_hop("https://auth.example.com/jwks"), verify=False)

    assert sent_hops == [("https://auth.example.com/jwks", "/etc/ssl/custom.pem")]


def test_an_explicit_verify_is_kept_on_every_hop(settings, sent_hops):
    """A caller's own ``verify`` is not replaced, on the first hop or a redirect."""
    settings["CERTIFICATE_VALIDATION"] = "disabled"

    network.NetworkSession().get("https://auth.example.com/", verify=True)

    assert sent_hops == [("https://auth.example.com/", True)]

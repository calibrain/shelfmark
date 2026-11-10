"""Tests for proxy selection and NO_PROXY bypass handling."""


def _set_proxy_config(monkeypatch, **values):
    import shelfmark.download.network as network

    def fake_get(key, default=""):
        return values.get(key, default)

    monkeypatch.setattr(network.app_config, "get", fake_get)
    return network


def test_get_proxies_bypasses_exact_and_wildcard_no_proxy_hosts(monkeypatch):
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="http",
        HTTP_PROXY="http://proxy.local:8080",
        HTTPS_PROXY="https://secure-proxy.local:8443",
        NO_PROXY="LOCALHOST, *.Internal, 10.*",
    )

    assert network.should_bypass_proxy("https://localhost:8080") is True
    assert network.should_bypass_proxy("https://API.Internal/path") is True
    assert network.should_bypass_proxy("https://10.1.2.3/file") is True
    assert network.should_bypass_proxy("https://example.com") is False

    assert network.get_proxies("https://localhost:8080") == {}
    assert network.get_proxies("https://example.com") == {
        "http": "http://proxy.local:8080",
        "https": "https://secure-proxy.local:8443",
    }


def test_get_proxies_falls_back_to_http_proxy_for_https(monkeypatch):
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="http",
        HTTP_PROXY="http://proxy.local:8080",
        HTTPS_PROXY="",
        NO_PROXY="",
    )

    assert network.get_proxies("https://example.com") == {
        "http": "http://proxy.local:8080",
        "https": "http://proxy.local:8080",
    }


def test_get_proxies_returns_socks_proxy_for_both_schemes(monkeypatch):
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="socks5",
        SOCKS5_PROXY="socks5://proxy.local:1080",
        NO_PROXY="",
    )

    assert network.get_proxies("https://example.com") == {
        "http": "socks5://proxy.local:1080",
        "https": "socks5://proxy.local:1080",
    }


def test_a_url_that_fails_to_parse_is_not_written_to_the_log(monkeypatch):
    """The proxy check logs why a URL did not parse, never the URL itself."""
    from unittest.mock import patch

    network = _set_proxy_config(monkeypatch, NO_PROXY="localhost")
    # An unterminated IPv6 literal makes urlparse raise ValueError.
    url = "http://user:s3cret@[::1/path?apikey=s3cret"

    with patch.object(network.logger, "debug") as debug:
        assert network.should_bypass_proxy(url) is False

    logged = " ".join(str(arg) for call in debug.call_args_list for arg in call.args)
    assert "Failed to parse URL" in logged
    assert "s3cret" not in logged


import pytest


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:8080/api",
        "http://10.0.0.5:9696",
        "http://172.16.4.2",
        "http://127.0.0.1:8084",
        "http://[::1]:8080",
        "http://[fd12:3456::7]:8080",
        "http://[fe80::1]/",
        "http://prowlarr:9696/api/v1",
        "http://nas.local/share",
        "https://abs.lan:13378",
        "http://qbittorrent.docker:8080",
    ],
)
def test_a_local_host_always_bypasses_the_proxy(monkeypatch, url):
    """LAN IPs, loopback, Docker service names and internal TLDs go direct with No Proxy empty."""
    network = _set_proxy_config(
        monkeypatch, PROXY_MODE="http", HTTP_PROXY="http://proxy.example.com:8118", NO_PROXY=""
    )

    assert network.should_bypass_proxy(url) is True
    assert network.get_proxies(url) == {}


@pytest.mark.parametrize(
    "url",
    [
        "https://api.hardcover.app/v1/graphql",
        "https://abs.example.com",
        "http://8.8.8.8",
        "http://[2606:4700::1]/",
    ],
)
def test_a_public_host_goes_through_the_proxy(monkeypatch, url):
    """A public name or address is proxied, including a LAN service under a public name."""
    network = _set_proxy_config(
        monkeypatch, PROXY_MODE="http", HTTP_PROXY="http://proxy.example.com:8118", NO_PROXY=""
    )

    assert network.should_bypass_proxy(url) is False
    assert network.get_proxies(url) == {
        "http": "http://proxy.example.com:8118",
        "https": "http://proxy.example.com:8118",
    }


def test_no_proxy_covers_a_lan_service_under_a_public_hostname(monkeypatch):
    """Split DNS: the name looks public, so only a No Proxy pattern sends it direct."""
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="http",
        HTTP_PROXY="http://proxy.example.com:8118",
        NO_PROXY="*.example.com",
    )

    assert network.should_bypass_proxy("https://abs.example.com") is True
    assert network.should_bypass_proxy("https://api.hardcover.app") is False


@pytest.mark.parametrize(
    ("url", "bypassed"),
    [
        ("http://192.168.1.20:8080/", True),
        ("http://10.0.0.5/", True),
        ("http://127.0.0.1:8084/", True),
        ("http://nas.local/", True),
        ("https://ntfy.lan/topic", True),
        ("https://abs.example.com/", True),
        ("https://api.hardcover.app/", False),
        ("http://8.8.8.8/", False),
    ],
)
def test_the_environment_no_proxy_carries_the_local_rule_for_requests(monkeypatch, url, bypassed):
    """Code that reads NO_PROXY instead of calling get_proxies() gets the same local rule.

    Checked with requests' own matcher, the one Apprise uses, including a No Proxy
    pattern written as ``*.example.com``.
    """
    from requests.utils import should_bypass_proxies

    network = _set_proxy_config(monkeypatch, NO_PROXY="*.example.com")

    assert should_bypass_proxies(url, no_proxy=network.no_proxy_for_environment()) is bypassed


@pytest.mark.parametrize("mode", ["USING_TOR", "USING_WIREGUARD"])
def test_a_saved_proxy_is_not_used_when_a_tunnel_routes_the_traffic(monkeypatch, mode):
    """Tor and WireGuard route everything themselves; a LAN proxy would carry it around them."""
    from shelfmark.config import env

    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="http",
        HTTP_PROXY="http://192.168.1.5:3128",
        NO_PROXY="",
        USING_TOR=mode == "USING_TOR",
    )
    monkeypatch.setattr(env, "USING_WIREGUARD", mode == "USING_WIREGUARD")

    assert network.get_proxies("https://example.com") == {}


def test_a_saved_proxy_is_used_without_a_tunnel(monkeypatch):
    """With neither tunnel on, the proxy from Settings applies as before."""
    from shelfmark.config import env

    network = _set_proxy_config(
        monkeypatch, PROXY_MODE="socks5", SOCKS5_PROXY="socks5://proxy.local:1080", NO_PROXY=""
    )
    monkeypatch.setattr(env, "USING_WIREGUARD", False)

    assert network.get_proxies("https://example.com")["https"] == "socks5://proxy.local:1080"


@pytest.mark.parametrize(("mode", "name"), [("USING_TOR", "Tor"), ("USING_WIREGUARD", "WireGuard")])
def test_the_proxy_fields_say_why_they_are_unused_under_a_tunnel(monkeypatch, mode, name):
    """Settings greys out every proxy field and names the tunnel that routes the traffic."""
    from shelfmark.config import env
    from shelfmark.config.settings import network_settings

    monkeypatch.setattr(env, "USING_TOR", False)
    monkeypatch.setattr(env, "USING_WIREGUARD", False)
    monkeypatch.setattr(env, mode, True)
    fields = {field.key: field for field in network_settings()}

    for key in ("PROXY_MODE", "HTTP_PROXY", "HTTPS_PROXY", "SOCKS5_PROXY", "NO_PROXY"):
        assert fields[key].disabled is True
        assert name in fields[key].disabled_reason

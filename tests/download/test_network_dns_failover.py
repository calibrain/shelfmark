"""Tests for DNS failover and rotation behavior."""


def _set_auto_dns_mode(monkeypatch):
    import shelfmark.download.network as network

    def fake_get(key, default=""):
        if key == "CUSTOM_DNS":
            return "auto"
        if key == "USING_TOR":
            return False
        return default

    monkeypatch.setattr(network.app_config, "get", fake_get)
    return network


def test_switch_dns_provider_updates_runtime_state_and_notifies_listeners(monkeypatch):
    network = _set_auto_dns_mode(monkeypatch)
    events: list[tuple] = []

    monkeypatch.setattr(
        network,
        "DNS_PROVIDERS",
        [
            ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
            ("google", ["8.8.8.8", "8.8.4.4"], "https://dns.google/resolve"),
        ],
    )
    monkeypatch.setattr(network, "_current_dns_index", -1)
    monkeypatch.setattr(network, "_dns_exhausted_logged", False)
    monkeypatch.setattr(network, "_save_state", lambda **kwargs: events.append(("save", kwargs)))
    monkeypatch.setattr(network, "init_dns_resolvers", lambda: events.append(("init",)))
    monkeypatch.setattr(
        network,
        "_notify_dns_rotation",
        lambda provider, servers, doh: events.append(("notify", provider, servers, doh)),
    )

    assert network.switch_dns_provider() is True
    assert network._current_dns_index == 0
    assert network.CUSTOM_DNS == ["1.1.1.1", "1.0.0.1"]
    assert network.DOH_SERVER == "https://cloudflare-dns.com/dns-query"
    assert events == [
        ("save", {"dns_provider": "cloudflare"}),
        ("init",),
        ("notify", "cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
    ]


def test_rotate_dns_provider_cycles_back_to_first_provider(monkeypatch):
    network = _set_auto_dns_mode(monkeypatch)
    events: list[tuple] = []

    monkeypatch.setattr(
        network,
        "DNS_PROVIDERS",
        [
            ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
            ("google", ["8.8.8.8", "8.8.4.4"], "https://dns.google/resolve"),
        ],
    )
    monkeypatch.setattr(network, "_current_dns_index", 1)
    monkeypatch.setattr(network, "_dns_exhausted_logged", False)
    monkeypatch.setattr(network, "_save_state", lambda **kwargs: events.append(("save", kwargs)))
    monkeypatch.setattr(network, "init_dns_resolvers", lambda: events.append(("init",)))
    monkeypatch.setattr(
        network,
        "_notify_dns_rotation",
        lambda provider, servers, doh: events.append(("notify", provider, servers, doh)),
    )

    assert network.rotate_dns_provider() is True
    assert network._current_dns_index == 0
    assert network.CUSTOM_DNS == ["1.1.1.1", "1.0.0.1"]
    assert network.DOH_SERVER == "https://cloudflare-dns.com/dns-query"
    assert events == [
        ("save", {"dns_provider": "cloudflare"}),
        ("init",),
        ("notify", "cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
    ]


def test_rotate_dns_and_reset_aa_keeps_aa_unconfigured_without_user_mirrors(monkeypatch):
    network = _set_auto_dns_mode(monkeypatch)
    events: list[tuple] = []

    monkeypatch.setattr(network, "rotate_dns_provider", lambda: True)
    monkeypatch.setattr(network, "_get_configured_aa_url", lambda: "auto")
    monkeypatch.setattr(network, "_aa_urls", [])
    monkeypatch.setattr(network, "_aa_base_url", "https://legacy-aa.example")
    monkeypatch.setattr(network, "_current_aa_url_index", 3)
    monkeypatch.setattr(network, "_save_state", lambda **kwargs: events.append(("save", kwargs)))

    assert network.rotate_dns_and_reset_aa() is True
    assert network._current_aa_url_index == 0
    assert network._aa_base_url == ""
    assert events == []


def test_system_failover_getaddrinfo_retries_after_dns_switch(monkeypatch):
    network = _set_auto_dns_mode(monkeypatch)
    calls: list[tuple] = []

    monkeypatch.setattr(
        network,
        "DNS_PROVIDERS",
        [
            ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
            ("google", ["8.8.8.8", "8.8.4.4"], "https://dns.google/resolve"),
        ],
    )
    monkeypatch.setattr(network, "_current_dns_index", 0)

    def fake_original_getaddrinfo(*args):
        calls.append(("original", args))
        raise OSError("system DNS failed")

    def fake_retry_getaddrinfo(*args):
        calls.append(("retry", args))
        return [(network.socket.AF_INET, network.socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443))]

    monkeypatch.setattr(network, "original_getaddrinfo", fake_original_getaddrinfo)
    monkeypatch.setattr(network.socket, "getaddrinfo", fake_retry_getaddrinfo)
    monkeypatch.setattr(network, "_is_local_address", lambda _host: False)
    monkeypatch.setattr(network, "_is_ip_address", lambda _host: False)
    monkeypatch.setattr(network, "switch_dns_provider", lambda: calls.append(("switch",)) or True)

    resolver = network.create_system_failover_getaddrinfo()
    result = resolver("example.com", "443")

    assert calls == [
        ("original", ("example.com", "443", 0, 0, 0, 0)),
        ("switch",),
        ("retry", ("example.com", "443", 0, 0, 0, 0)),
    ]
    assert result == [
        (network.socket.AF_INET, network.socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443))
    ]


def _addrinfo(ip):
    return [(2, 1, 6, "", (ip, 443))]


class _FakeDoHResolver:
    def __init__(self, ips):
        self._ips = ips

    def resolve(self, _hostname, _record_type):
        return list(self._ips)


def test_build_detection_doh_resolver_uses_selected_provider(monkeypatch):
    import shelfmark.download.network as network

    monkeypatch.setattr(
        network,
        "DNS_PROVIDERS",
        [
            ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
            ("quad9", ["9.9.9.9", "149.112.112.112"], "https://dns.quad9.net/dns-query"),
        ],
    )
    monkeypatch.setattr(network, "_current_dns_index", 1)  # user selected quad9

    resolver = network._build_detection_doh_resolver()

    assert resolver is not None
    assert resolver.base_url == "https://dns.quad9.net/dns-query"
    assert resolver.hostname == "dns.quad9.net"
    assert resolver.ip == "9.9.9.9"


def test_build_detection_doh_resolver_falls_back_to_first_provider(monkeypatch):
    import shelfmark.download.network as network

    monkeypatch.setattr(
        network,
        "DNS_PROVIDERS",
        [
            ("cloudflare", ["1.1.1.1", "1.0.0.1"], "https://cloudflare-dns.com/dns-query"),
            ("quad9", ["9.9.9.9"], "https://dns.quad9.net/dns-query"),
        ],
    )
    monkeypatch.setattr(network, "_current_dns_index", -1)  # system / not yet rotated

    resolver = network._build_detection_doh_resolver()

    assert resolver is not None
    assert resolver.base_url == "https://cloudflare-dns.com/dns-query"
    assert resolver.ip == "1.1.1.1"


def test_detect_dns_interference_flags_divergent_resolvers(monkeypatch):
    import shelfmark.download.network as network

    # System DNS (hijacked) returns an ISP block-page IP; DoH returns the real one.
    monkeypatch.setattr(network, "original_getaddrinfo", lambda *a, **k: _addrinfo("198.51.100.1"))
    monkeypatch.setattr(
        network, "_build_detection_doh_resolver", lambda: _FakeDoHResolver(["203.0.113.7"])
    )

    result = network.detect_dns_interference("annas-archive.pk")

    assert result == {"system_ips": ["198.51.100.1"], "doh_ips": ["203.0.113.7"]}


def test_detect_dns_interference_none_when_resolvers_agree(monkeypatch):
    import shelfmark.download.network as network

    monkeypatch.setattr(network, "original_getaddrinfo", lambda *a, **k: _addrinfo("203.0.113.7"))
    monkeypatch.setattr(
        network, "_build_detection_doh_resolver", lambda: _FakeDoHResolver(["203.0.113.7"])
    )

    assert network.detect_dns_interference("annas-archive.pk") is None


def test_detect_dns_interference_none_when_doh_unavailable(monkeypatch):
    import shelfmark.download.network as network

    monkeypatch.setattr(network, "original_getaddrinfo", lambda *a, **k: _addrinfo("198.51.100.1"))
    monkeypatch.setattr(network, "_build_detection_doh_resolver", lambda: None)

    assert network.detect_dns_interference("annas-archive.pk") is None


def test_detect_dns_interference_skips_ip_and_local(monkeypatch):
    import shelfmark.download.network as network

    def _should_not_run():
        raise AssertionError("resolver should not be built for IP/local hosts")

    monkeypatch.setattr(network, "_build_detection_doh_resolver", _should_not_run)

    assert network.detect_dns_interference("1.2.3.4") is None
    assert network.detect_dns_interference("localhost") is None


def test_note_possible_dns_interference_warns_once_and_sets_flag(monkeypatch):
    import shelfmark.download.network as network

    monkeypatch.setattr(network, "_dns_interference_warned", set())
    monkeypatch.setattr(network, "_dns_interference_active", False)

    calls: list[str] = []
    monkeypatch.setattr(
        network,
        "detect_dns_interference",
        lambda host: (
            calls.append(host) or {"system_ips": ["198.51.100.1"], "doh_ips": ["203.0.113.7"]}
        ),
    )

    assert network.note_possible_dns_interference("annas-archive.pk") is True
    assert network.dns_interference_detected() is True
    # A repeat check for the same host must not re-run the costly detection.
    assert network.note_possible_dns_interference("annas-archive.pk") is True
    assert calls == ["annas-archive.pk"]


def test_note_possible_dns_interference_no_detection_keeps_flag_false(monkeypatch):
    import shelfmark.download.network as network

    monkeypatch.setattr(network, "_dns_interference_warned", set())
    monkeypatch.setattr(network, "_dns_interference_active", False)
    monkeypatch.setattr(network, "detect_dns_interference", lambda _host: None)

    assert network.note_possible_dns_interference("annas-archive.pk") is False
    assert network.dns_interference_detected() is False

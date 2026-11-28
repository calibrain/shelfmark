"""RFC 8484 wireformat codec.

Quad9 and OpenDNS reject the JSON API that Cloudflare and Google popularised, so
these providers only work through wireformat. Quad9 additionally requires HTTP/2
(section 5.2) and answers HTTP/1.1 with 505.
"""

import base64
import struct
import threading

import pytest

from shelfmark.download import doh_wireformat as wf


def _decode_param(param: str) -> bytes:
    padding = "=" * (-len(param) % 4)
    return base64.urlsafe_b64decode(param + padding)


def _build_response(
    *, qname: str = "example.com", answers: list[tuple[int, bytes]], qtype: int = wf.TYPE_A
) -> bytes:
    """Assemble a response whose answer names are compression pointers to the question."""
    question = b""
    for label in qname.split("."):
        question += bytes([len(label)]) + label.encode()
    question += b"\x00" + struct.pack(">HH", qtype, 1)

    body = b""
    for rtype, rdata in answers:
        body += b"\xc0\x0c"  # pointer to offset 12 (the question name)
        body += struct.pack(">HHIH", rtype, 1, 300, len(rdata)) + rdata

    header = struct.pack(">HHHHHH", 0, 0x8180, 1, len(answers), 0, 0)
    return header + question + body


def test_encode_query_is_a_well_formed_dns_message():
    raw = _decode_param(wf.encode_query_param("example.com", wf.TYPE_A))

    msg_id, flags, qdcount, ancount, _ns, _ar = struct.unpack_from(">HHHHHH", raw, 0)
    assert msg_id == 0  # RFC 8484 section 4.1: zero for cacheability
    assert flags == 0x0100  # recursion desired
    assert (qdcount, ancount) == (1, 0)
    assert raw[12:] == b"\x07example\x03com\x00" + struct.pack(">HH", wf.TYPE_A, 1)


def test_encode_query_param_is_unpadded_base64url():
    param = wf.encode_query_param("example.com", wf.TYPE_A)
    assert "=" not in param
    assert "+" not in param and "/" not in param


def test_encode_query_strips_trailing_dot():
    assert _decode_param(wf.encode_query_param("example.com.", wf.TYPE_A)) == _decode_param(
        wf.encode_query_param("example.com", wf.TYPE_A)
    )


def test_encode_query_rejects_empty_hostname():
    with pytest.raises(wf.WireformatError):
        wf.encode_query("", wf.TYPE_A)


def test_encode_query_rejects_oversized_label():
    with pytest.raises(wf.WireformatError):
        wf.encode_query("a" * 64 + ".com", wf.TYPE_A)


def test_decode_a_records():
    response = _build_response(answers=[(wf.TYPE_A, bytes([93, 184, 216, 34]))])
    assert wf.decode_answer(response, wf.TYPE_A) == ["93.184.216.34"]


def test_decode_multiple_a_records_preserves_order():
    response = _build_response(
        answers=[(wf.TYPE_A, bytes([1, 1, 1, 1])), (wf.TYPE_A, bytes([8, 8, 8, 8]))]
    )
    assert wf.decode_answer(response, wf.TYPE_A) == ["1.1.1.1", "8.8.8.8"]


def test_decode_skips_cname_records_in_the_chain():
    """Answers routinely lead with a CNAME; only the requested type is an address."""
    cname = b"\x03www\x07example\x03com\x00"
    response = _build_response(answers=[(5, cname), (wf.TYPE_A, bytes([93, 184, 216, 34]))])
    assert wf.decode_answer(response, wf.TYPE_A) == ["93.184.216.34"]


def test_decode_aaaa_compresses_zero_run():
    # 2606:4700:0:0:0:0:6810:84e5 -> the middle zero run collapses to "::"
    rdata = struct.pack(">8H", 0x2606, 0x4700, 0, 0, 0, 0, 0x6810, 0x84E5)
    response = _build_response(answers=[(wf.TYPE_AAAA, rdata)], qtype=wf.TYPE_AAAA)
    assert wf.decode_answer(response, wf.TYPE_AAAA) == ["2606:4700::6810:84e5"]


def test_decode_aaaa_collapses_only_the_longest_zero_run():
    # 2001:0:0:1:0:0:0:1 - the second, longer run is the one that collapses.
    rdata = struct.pack(">8H", 0x2001, 0, 0, 1, 0, 0, 0, 1)
    response = _build_response(answers=[(wf.TYPE_AAAA, rdata)], qtype=wf.TYPE_AAAA)
    assert wf.decode_answer(response, wf.TYPE_AAAA) == ["2001:0:0:1::1"]


def test_decode_aaaa_without_zero_run():
    rdata = struct.pack(">8H", 0x2001, 0x0DB8, 1, 2, 3, 4, 5, 6)
    response = _build_response(answers=[(wf.TYPE_AAAA, rdata)], qtype=wf.TYPE_AAAA)
    assert wf.decode_answer(response, wf.TYPE_AAAA) == ["2001:db8:1:2:3:4:5:6"]


def test_decode_nxdomain_returns_empty_not_an_error():
    """An empty answer is a valid response; the caller falls back rather than retrying."""
    response = _build_response(answers=[])
    assert wf.decode_answer(response, wf.TYPE_A) == []


def test_decode_rejects_truncated_header():
    with pytest.raises(wf.WireformatError):
        wf.decode_answer(b"\x00\x01", wf.TYPE_A)


def test_decode_rejects_truncated_record():
    response = _build_response(answers=[(wf.TYPE_A, bytes([1, 2, 3, 4]))])
    with pytest.raises(wf.WireformatError):
        wf.decode_answer(response[:-2], wf.TYPE_A)


def test_decode_does_not_hang_on_a_malicious_name():
    """A self-referential name must not loop forever."""
    header = struct.pack(">HHHHHH", 0, 0x8180, 1, 0, 0, 0)
    # A run of maximum-length labels that never terminates.
    body = (b"\x3f" + b"a" * 63) * 8
    with pytest.raises(wf.WireformatError):
        wf.decode_answer(header + body, wf.TYPE_A)


def test_wireformat_providers_are_flagged(monkeypatch):
    """The provider table and the resolver must agree on who needs wireformat."""
    import shelfmark.download.network as network

    for name, servers, url in network.DNS_PROVIDERS:
        resolver = network.DoHResolver(url, "x.invalid", servers[0])
        expected = name in ("quad9", "opendns")
        assert resolver.use_wireformat is expected, f"{name} wireformat flag wrong"


@pytest.mark.parametrize(
    ("url", "host", "ip"),
    [
        ("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9"),
        ("https://doh.opendns.com/dns-query", "doh.opendns.com", "208.67.222.222"),
    ],
)
def test_wireformat_http2_client_supports_socks5_proxy(monkeypatch, url, host, ip):
    """Regression for #1778: httpx must ship with socksio and receive the exact
    authenticated SOCKS5 proxy + HTTP/2 settings for Quad9 and OpenDNS."""
    import shelfmark.download.network as network

    proxy_url = "socks5://user:pass@proxy.local:1080"
    real_client_cls = network.httpx.Client
    captured: list[dict] = []

    def _spy_client(*args, **kwargs):
        captured.append(dict(kwargs))
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(network.httpx, "Client", _spy_client)
    monkeypatch.setattr(
        network,
        "get_proxies",
        lambda _url: {"http": proxy_url, "https": proxy_url},
    )
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: True)

    resolver = network.DoHResolver(url, host, ip)
    with resolver._borrow_http2_client() as client:
        try:
            assert client is not None
            assert len(captured) == 1
            assert captured[0]["http2"] is True
            assert captured[0]["verify"] is True
            assert captured[0]["proxy"] == proxy_url
        finally:
            client.close()


def test_wireformat_http2_client_rebuilds_when_proxy_or_tls_changes(monkeypatch):
    """Changing proxy or TLS settings live must rebuild the cached HTTP/2 client
    and close the previous connection pool instead of reusing stale settings."""
    import shelfmark.download.network as network

    state = {"proxy": {}, "verify": False}
    created: list[tuple[dict, object]] = []
    closed: list[object] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            closed.append(self)

    def _fake_factory(**kwargs):
        inst = _FakeClient(**kwargs)
        created.append((kwargs, inst))
        return inst

    monkeypatch.setattr(network.httpx, "Client", _fake_factory)
    monkeypatch.setattr(network, "get_proxies", lambda _url: dict(state["proxy"]))
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: state["verify"])

    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    with resolver._borrow_http2_client() as first:
        pass
    with resolver._borrow_http2_client() as again:
        assert again is first
    assert closed == []
    assert len(created) == 1
    assert created[0][0]["proxy"] is None
    assert created[0][0]["verify"] is False

    state["proxy"] = {
        "http": "socks5://user:pass@proxy.example.com:1080",
        "https": "socks5://user:pass@proxy.example.com:1080",
    }
    state["verify"] = True

    with resolver._borrow_http2_client() as second:
        assert second is not first
    assert len(created) == 2
    assert created[1][0]["proxy"] == "socks5://user:pass@proxy.example.com:1080"
    assert created[1][0]["verify"] is True
    assert closed == [first]


def test_wireformat_http2_client_stays_open_for_in_flight_request_after_rebuild(monkeypatch):
    """A lookup that changes proxy/TLS settings must not close the client another
    lookup is still sending on; the old client closes once that request returns."""
    import shelfmark.download.network as network

    state = {"verify": False}
    closed: list[object] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            closed.append(self)

    monkeypatch.setattr(network.httpx, "Client", lambda **kwargs: _FakeClient(**kwargs))
    monkeypatch.setattr(network, "get_proxies", lambda _url: {})
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: state["verify"])

    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    with resolver._borrow_http2_client() as first:
        with resolver._borrow_http2_client() as shared:
            assert shared is first

        state["verify"] = True
        with resolver._borrow_http2_client() as second:
            assert second is not first
        # first is still borrowed by the outer lookup, so it must stay open.
        assert closed == []

    assert closed == [first]
    assert resolver._http2_client is second
    assert resolver._http2_borrowers == {}


def test_wireformat_http2_client_failed_close_does_not_strand_new_borrow(monkeypatch):
    """If closing the replaced idle client raises, the new client's borrow must
    still be returned so it can be closed when it is replaced in turn."""
    import shelfmark.download.network as network

    state = {"verify": False}
    closed: list[object] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.fail_close = False

        def close(self):
            closed.append(self)
            if self.fail_close:
                raise RuntimeError("close failed")

    monkeypatch.setattr(network.httpx, "Client", lambda **kwargs: _FakeClient(**kwargs))
    monkeypatch.setattr(network, "get_proxies", lambda _url: {})
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: state["verify"])

    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    with resolver._borrow_http2_client() as first:
        first.fail_close = True

    state["verify"] = True
    with pytest.raises(RuntimeError, match="close failed"):
        with resolver._borrow_http2_client():
            pytest.fail("borrow must not be entered when closing the old client fails")

    second = resolver._http2_client
    assert second is not None
    assert second is not first
    assert closed == [first]
    assert resolver._http2_borrowers == {}

    state["verify"] = False
    with resolver._borrow_http2_client() as third:
        assert third is not second
    assert closed == [first, second]


def test_wireformat_rebuild_waits_for_concurrent_in_flight_requests(monkeypatch):
    """Two lookups block inside .get() on the old client while a third lookup sees
    a TLS change and rebuilds. The old client must stay open while either request
    is in flight (including one that fails) and close exactly once afterwards."""
    import shelfmark.download.network as network

    state = {"verify": False}
    closed: list[object] = []
    entered = {name: threading.Event() for name in ("old-fail", "old-ok")}
    proceed = {name: threading.Event() for name in ("old-fail", "old-ok")}
    answer = _build_response(answers=[(wf.TYPE_A, bytes([192, 0, 2, 1]))])

    class _FakeResponse:
        content = answer

        def raise_for_status(self):
            return None

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def close(self):
            closed.append(self)

        def get(self, *_args, **_kwargs):
            name = threading.current_thread().name
            if name in entered:
                entered[name].set()
                proceed[name].wait()
                if name == "old-fail":
                    raise network.httpx.ConnectError("simulated failure")
            return _FakeResponse()

    monkeypatch.setattr(network.httpx, "Client", lambda **kwargs: _FakeClient(**kwargs))
    monkeypatch.setattr(network, "get_proxies", lambda _url: {})
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: state["verify"])

    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    results: dict[str, object] = {}

    def _lookup():
        name = threading.current_thread().name
        try:
            results[name] = resolver._resolve_wireformat("example.com", "A")
        except network.httpx.HTTPError as exc:
            results[name] = exc

    threads = [threading.Thread(target=_lookup, name=name, daemon=True) for name in entered]
    started_threads: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
        for name in entered:
            assert entered[name].wait(5)

        old = resolver._http2_client
        assert resolver._http2_borrowers == {old: 2}

        state["verify"] = True
        third_result: list[object] = []

        def _third_lookup():
            third_result.append(resolver._resolve_wireformat("example.com", "A"))

        third = threading.Thread(target=_third_lookup, name="new-ok", daemon=True)
        third.start()
        started_threads.append(third)
        third.join(5)
        assert not third.is_alive()
        assert third_result == [["192.0.2.1"]]
        new = resolver._http2_client
        assert new is not old
        assert closed == []

        proceed["old-fail"].set()
        threads[0].join(5)
        assert not threads[0].is_alive()
        assert isinstance(results["old-fail"], network.httpx.ConnectError)
        assert closed == []

        proceed["old-ok"].set()
        threads[1].join(5)
        assert not threads[1].is_alive()
        assert results["old-ok"] == ["192.0.2.1"]
        assert closed == [old]
        assert resolver._http2_borrowers == {}
    finally:
        for event in proceed.values():
            event.set()
        for thread in started_threads:
            thread.join(5)


def test_configured_proxy_hostnames_matches_httpx_idna_and_bypasses_doh(monkeypatch):
    """Non-ASCII proxy hostnames (including IDNA2008 eszett and trailing dots) use
    httpx's own URL encoding so socket lookups bypass DoH, while invalid IDNA labels
    do not add spurious aliases."""
    import socket

    import shelfmark.download.network as network

    monkeypatch.setattr(network, "get_proxies", lambda _url: {})
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)

    # IDNA2008 eszett (differs from stdlib IDNA2003 'fass.example') and trailing dot.
    monkeypatch.setenv("HTTPS_PROXY", "socks5://faß.example:1080")
    monkeypatch.setenv("HTTP_PROXY", "http://faß.example.:8080")
    monkeypatch.setenv("ALL_PROXY", "socks5://ab\u200dcd.example:1080")

    hosts = network._configured_proxy_hostnames()
    assert "xn--fa-hia.example" in hosts
    assert "xn--fa-hia.example." in hosts
    assert "fass.example" not in hosts
    assert "abcd.example" not in hosts

    # Verify custom_getaddrinfo routes httpx's encoded proxy hosts to system DNS, not DoH.
    doh_calls: list[str] = []
    system_calls: list[str] = []

    def _fake_system_getaddrinfo(host, port, *_args, **_kwargs):
        system_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.9", port))]

    monkeypatch.setattr(network, "original_getaddrinfo", _fake_system_getaddrinfo)
    custom = network.create_custom_getaddrinfo(
        lambda host: doh_calls.append(host) or ["198.51.100.1"],
        lambda _host: [],
        lambda host: host.lower() in network._configured_proxy_hostnames(),
    )

    for target in ("xn--fa-hia.example", "xn--fa-hia.example."):
        res = custom(target, 1080, socket.AF_INET, socket.SOCK_STREAM)
        assert res[0][4][0] == "192.0.2.9"
    assert doh_calls == []
    assert system_calls == ["xn--fa-hia.example", "xn--fa-hia.example."]


def test_malformed_proxy_values_do_not_break_unrelated_dns_lookups(monkeypatch):
    """Non-string proxy settings and lone-surrogate URLs must neither raise inside
    skip_doh nor crash Quad9 DoH client creation (falling back cleanly to system DNS
    rather than raising AttributeError or silently connecting direct)."""
    import socket

    import shelfmark.download.network as network

    surrogate_url = "http://\ud800@proxy.example:8080"
    with pytest.raises(UnicodeEncodeError):
        network.httpx.URL(surrogate_url)

    monkeypatch.setattr(
        network,
        "get_proxies",
        lambda _url: {"https": 123, "http": surrogate_url},
    )
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: True)
    for key in (
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(key, raising=False)

    assert network._configured_proxy_hostnames() == {"proxy.example"}

    # Full real-resolver path: non-string proxy 123 must fail as a DoH transport error
    # (never silently direct-connecting) and fall back cleanly to system DNS.
    system_calls: list[str] = []

    def _fake_system_getaddrinfo(host, port, *_args, **_kwargs):
        system_calls.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.42", port))]

    monkeypatch.setattr(network, "original_getaddrinfo", _fake_system_getaddrinfo)
    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    custom = network.create_custom_getaddrinfo(
        lambda host: resolver.resolve(host, "A"),
        lambda host: resolver.resolve(host, "AAAA"),
        lambda host: host.lower() in network._configured_proxy_hostnames(),
    )
    res = custom("unrelated.example", 443, socket.AF_INET, socket.SOCK_STREAM)
    assert res[0][4][0] == "192.0.2.42"
    assert system_calls == ["unrelated.example"]
    assert resolver._http2_client is None


def test_acquire_http2_client_reads_settings_under_lock(monkeypatch):
    """Both get_proxies and get_ssl_verify must be read while holding _http2_lock,
    so a waiting lookup observes the latest settings after taking the lock rather
    than replacing a newer client with a stale pre-lock snapshot."""
    import shelfmark.download.network as network

    state = {"verify": False}
    created: list[dict[str, object]] = []
    lock_waiting = threading.Event()
    finished = threading.Event()
    getter_lock_owners: list[tuple[str, int | None, int]] = []

    class _TrackingLock:
        def __init__(self):
            self._lock = threading.Lock()
            self.owner: int | None = None

        def __enter__(self):
            if threading.current_thread().name == "waiter":
                lock_waiting.set()
            self._lock.acquire()
            self.owner = threading.get_ident()
            return self

        def __exit__(self, *args):
            self.owner = None
            self._lock.release()

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(kwargs)

        def close(self):
            pass

    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    tracking_lock = _TrackingLock()
    resolver._http2_lock = tracking_lock  # type: ignore[assignment]

    def _fake_get_proxies(_url: str) -> dict[str, str]:
        getter_lock_owners.append(("proxies", tracking_lock.owner, threading.get_ident()))
        return {}

    def _fake_get_ssl_verify(_url: str) -> bool:
        getter_lock_owners.append(("verify", tracking_lock.owner, threading.get_ident()))
        return state["verify"]

    monkeypatch.setattr(network.httpx, "Client", lambda **kwargs: _FakeClient(**kwargs))
    monkeypatch.setattr(network, "get_proxies", _fake_get_proxies)
    monkeypatch.setattr(network, "get_ssl_verify", _fake_get_ssl_verify)

    def _borrow_once():
        with resolver._borrow_http2_client():
            pass
        finished.set()

    with resolver._http2_lock:
        worker = threading.Thread(target=_borrow_once, name="waiter", daemon=True)
        worker.start()
        assert lock_waiting.wait(5)
        state["verify"] = True
        newest = _FakeClient(http2=True, timeout=10, verify=True, proxy=None)
        resolver._http2_client = newest
        resolver._http2_client_config = (None, True)

    assert finished.wait(5)
    worker.join(5)
    assert not worker.is_alive()
    assert resolver._http2_client is newest
    assert resolver._http2_client_config == (None, True)
    assert [c["verify"] for c in created] == [True]
    assert len(getter_lock_owners) == 2
    assert all(owner == caller for _, owner, caller in getter_lock_owners)


def test_wireformat_resolve_catches_missing_optional_transport_and_retries(monkeypatch):
    """A missing optional httpx transport (e.g. socksio) degrades cleanly to [],
    leaves no broken client cached, retries on subsequent calls, and does not
    swallow unrelated ImportErrors."""
    import shelfmark.download.network as network

    calls = 0

    def _raise_missing_socksio(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise ImportError(
            "Using SOCKS proxy, but the 'socksio' package is not installed. "
            "Make sure to install httpx using `pip install httpx[socks]`."
        )

    monkeypatch.setattr(network.httpx, "Client", _raise_missing_socksio)

    resolver = network.DoHResolver("https://dns.quad9.net/dns-query", "dns.quad9.net", "9.9.9.9")
    assert resolver.resolve("example.com", "A") == []
    assert resolver.resolve("example.com", "A") == []
    assert calls == 2
    assert resolver._http2_client is None

    # An unrelated ImportError (e.g. broken internal module) must not be swallowed.
    monkeypatch.setattr(
        resolver,
        "_resolve_wireformat",
        lambda *_a, **_kw: (_ for _ in ()).throw(ImportError("broken internal module")),
    )
    with pytest.raises(ImportError, match="broken internal module"):
        resolver.resolve("example.com", "A")


def test_doh_skips_public_socks_proxy_hostname_to_avoid_reentrancy(monkeypatch):
    """When SOCKS5_PROXY uses a public hostname, DoH getaddrinfo must bypass DoH
    for the proxy hostname itself while still resolving target hosts via DoH."""
    import shelfmark.download.network as network

    original_calls: list[str] = []
    doh_calls: list[str] = []

    def _fake_original_getaddrinfo(host, port, *args):
        original_calls.append(str(host))
        return [
            (
                network.socket.AF_INET,
                network.socket.SOCK_STREAM,
                6,
                "",
                ("203.0.113.5", int(port or 0)),
            )
        ]

    monkeypatch.setattr(network, "original_getaddrinfo", _fake_original_getaddrinfo)
    monkeypatch.setattr(network.socket, "gethostbyname", lambda _h: "9.9.9.9")
    monkeypatch.setattr(
        network,
        "get_proxies",
        lambda _url="": {
            "http": "socks5://user:pass@proxy.example.com:1080",
            "https": "socks5://user:pass@proxy.example.com:1080",
        },
    )
    monkeypatch.setattr(
        network.DoHResolver,
        "resolve",
        lambda _self, host, _rtype: doh_calls.append(host) or ["198.51.100.42"],
    )

    prev_getaddrinfo = network.socket.getaddrinfo
    try:
        network._init_doh_resolver_internal("https://dns.quad9.net/dns-query")
        proxy_res = network.socket.getaddrinfo("proxy.example.com", 1080)
        target_res = network.socket.getaddrinfo("books.example.org", 443)
    finally:
        network.socket.getaddrinfo = prev_getaddrinfo

    assert original_calls == ["proxy.example.com"]
    assert doh_calls == ["books.example.org"]
    assert proxy_res[0][4][0] == "203.0.113.5"
    assert target_res[0][4][0] == "198.51.100.42"

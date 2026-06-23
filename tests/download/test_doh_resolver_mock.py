"""DoH resolver integration against the e2e platform's mock DoH responder.

The config-cluster analysis flagged DNS/DoH as a recurring break surface (#1028,
#108). A fully hermetic DoH-over-the-network profile isn't feasible in the HTTP
docker platform (DoH provider URLs are HTTPS + IP-pinned), so we exercise the
*real* ``DoHResolver`` client against the platform's mock ``doh`` role here, over
plain HTTP on localhost. This runs in normal CI (not just the nightly docker
matrix) and guards the DoH JSON-parsing path the app relies on.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

import pytest

MOCK_PATH = Path(__file__).resolve().parents[1] / "e2e" / "platform" / "mocks" / "mock_services.py"


def _load_mock_doh_app(doh_map: str):
    """Import the platform mock_services module wired for the ``doh`` role.

    The module wires its routes at import time from ``MOCK_ROLE``/``DOH_MAP``, so
    those must be set before loading it.
    """
    os.environ["MOCK_ROLE"] = "doh"
    os.environ["DOH_MAP"] = doh_map
    spec = importlib.util.spec_from_file_location("mock_doh_services", MOCK_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.app


@pytest.fixture(scope="module")
def doh_url():
    if not MOCK_PATH.exists():
        pytest.skip(f"platform mock not found at {MOCK_PATH}")
    app = _load_mock_doh_app("aa.mock.test=172.30.0.10,cf.mock.test=172.30.0.11")
    server: WSGIServer = make_server("127.0.0.1", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # Google JSON DoH style uses the /resolve endpoint.
        yield f"http://127.0.0.1:{server.server_port}/resolve"
    finally:
        server.shutdown()


def _resolver(doh_url: str):
    from shelfmark.download.network import DoHResolver

    # hostname/ip args are the DoH server's own identity (used only for recursion
    # avoidance); the localhost values here are irrelevant to the lookups under test.
    return DoHResolver(doh_url, "127.0.0.1", "127.0.0.1")


def test_doh_resolves_mapped_host(doh_url) -> None:
    """The real DoH client parses the mock's JSON answer into an A record."""
    assert _resolver(doh_url).resolve("aa.mock.test", "A") == ["172.30.0.10"]


def test_doh_nxdomain_returns_empty_not_error(doh_url) -> None:
    """An unmapped name yields an empty list (Status 3), not an exception —
    the path that, when mishandled, surfaced as silent download failures."""
    assert _resolver(doh_url).resolve("unmapped.invalid", "A") == []


def test_doh_resolver_caches_within_ttl(doh_url) -> None:
    """A second lookup is served from cache (the resolver's documented behaviour)."""
    resolver = _resolver(doh_url)
    first = resolver.resolve("cf.mock.test", "A")
    assert first == ["172.30.0.11"]
    assert ("cf.mock.test", "A") in resolver._cache
    assert resolver.resolve("cf.mock.test", "A") == first

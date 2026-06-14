"""Tests for shared utility helpers."""

import sys
import types
import xmlrpc.client as stdlib_xmlrpc_client

from shelfmark.core import utils
from shelfmark.core.utils import normalize_http_url


class TestNormalizeHttpUrlQueryStripping:
    """Regression tests for issue #999 — mirror URLs with query params/fragments."""

    def test_strips_query_string_from_configured_url(self) -> None:
        result = normalize_http_url("http://mirror.example.com/search?token=abc123")
        assert result == "http://mirror.example.com/search"

    def test_strips_fragment_from_configured_url(self) -> None:
        result = normalize_http_url("http://mirror.example.com/search#section")
        assert result == "http://mirror.example.com/search"

    def test_strips_both_query_and_fragment(self) -> None:
        result = normalize_http_url("https://mirror.example.com/path?key=val&x=1#top")
        assert result == "https://mirror.example.com/path"

    def test_plain_url_unchanged(self) -> None:
        result = normalize_http_url("http://mirror.example.com/search")
        assert result == "http://mirror.example.com/search"

    def test_trailing_slash_still_stripped_after_query_removal(self) -> None:
        result = normalize_http_url("http://mirror.example.com/?token=x")
        assert result == "http://mirror.example.com"


def test_get_hardened_xmlrpc_client_tolerates_patch_runtime_error(monkeypatch) -> None:
    fake_package = types.ModuleType("defusedxml")
    fake_module = types.ModuleType("defusedxml.xmlrpc")

    def failing_monkey_patch() -> None:
        raise RuntimeError("patch failed")

    fake_module.monkey_patch = failing_monkey_patch
    fake_package.xmlrpc = fake_module

    monkeypatch.setitem(sys.modules, "defusedxml", fake_package)
    monkeypatch.setitem(sys.modules, "defusedxml.xmlrpc", fake_module)
    monkeypatch.setattr(utils, "_xmlrpc_patch_applied", False)

    client_module = utils.get_hardened_xmlrpc_client()

    assert client_module is stdlib_xmlrpc_client
    assert utils._xmlrpc_patch_applied is False

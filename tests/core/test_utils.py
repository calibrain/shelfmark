"""Tests for shared utility helpers."""

import sys
import types
import xmlrpc.client as stdlib_xmlrpc_client

from shelfmark.core import utils


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

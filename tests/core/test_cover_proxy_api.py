"""Cover proxy API security tests."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def test_cover_proxy_requires_authentication(main_module) -> None:
    client = main_module.app.test_client()

    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        response = client.get("/api/covers/test-id")

    assert response.status_code == 401
    assert response.get_json() == {"error": "Unauthorized"}

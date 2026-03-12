"""API tests for the frontend config endpoint."""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _set_session(client, *, user_id: str, db_user_id: int, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["db_user_id"] = db_user_id
        sess["is_admin"] = is_admin


def _create_user(main_module, *, prefix: str, role: str = "user") -> dict:
    username = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return main_module.user_db.create_user(username=username, role=role)


def test_config_includes_release_source_links_toggle(main_module, client):
    user = _create_user(main_module, prefix="reader")
    _set_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

    def fake_get(key, default=None, user_id=None):  # noqa: ANN001
        if key == "SHOW_RELEASE_SOURCE_LINKS":
            return False
        return default

    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        with patch.object(main_module.app_config, "get", side_effect=fake_get):
            with patch("shelfmark.metadata_providers.get_provider_sort_options", return_value=[]):
                with patch("shelfmark.metadata_providers.get_provider_search_fields", return_value=[]):
                    with patch("shelfmark.metadata_providers.get_provider_default_sort", return_value="relevance"):
                        resp = client.get("/api/config")

    assert resp.status_code == 200
    assert resp.json["show_release_source_links"] is False

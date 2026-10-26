"""API tests for the settings endpoints: auth gating, validation and error handling."""

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


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _sign_in(client, *, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = "someone"
        sess["db_user_id"] = 7
        sess["is_admin"] = is_admin


@pytest.fixture
def builtin_auth(main_module):
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        yield


@pytest.fixture
def admin(client, builtin_auth):
    _sign_in(client, is_admin=True)
    return client


_REGISTRY = "shelfmark.core.settings_registry"

# --------------------------------------------------------------------------- #
# GET /api/settings
# --------------------------------------------------------------------------- #


def test_get_all_returns_every_tab_with_values(admin):
    with patch(f"{_REGISTRY}.serialize_all_settings", return_value={"tabs": []}) as serialize:
        resp = admin.get("/api/settings")

    assert resp.status_code == 200
    assert resp.json == {"tabs": []}
    serialize.assert_called_once_with(include_values=True)


def test_get_all_reports_an_operational_error(admin):
    with patch(f"{_REGISTRY}.serialize_all_settings", side_effect=OSError("disk")):
        resp = admin.get("/api/settings")

    assert resp.status_code == 500
    assert resp.json == {"error": "disk"}


# --------------------------------------------------------------------------- #
# GET /api/settings/<tab>
# --------------------------------------------------------------------------- #


def test_get_tab_returns_the_serialized_tab(admin):
    tab = object()
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=tab) as get_tab,
        patch(f"{_REGISTRY}.serialize_tab", return_value={"name": "general"}) as serialize,
    ):
        resp = admin.get("/api/settings/general")

    assert resp.status_code == 200
    assert resp.json == {"name": "general"}
    get_tab.assert_called_once_with("general")
    serialize.assert_called_once_with(tab, include_values=True)


def test_get_tab_is_a_404_for_an_unknown_tab(admin):
    with patch(f"{_REGISTRY}.get_settings_tab", return_value=None):
        resp = admin.get("/api/settings/nope")

    assert resp.status_code == 404
    assert resp.json == {"error": "Unknown settings tab: nope"}


def test_get_tab_reports_an_operational_error(admin):
    with patch(f"{_REGISTRY}.get_settings_tab", side_effect=RuntimeError("boom")):
        resp = admin.get("/api/settings/general")

    assert resp.status_code == 500
    assert resp.json == {"error": "boom"}


# --------------------------------------------------------------------------- #
# PUT /api/settings/<tab>
# --------------------------------------------------------------------------- #


def test_update_tab_saves_the_values_and_returns_the_result(admin):
    result = {"success": True, "message": "Saved", "updated": ["A"]}
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=object()),
        patch(f"{_REGISTRY}.update_settings", return_value=result) as update,
    ):
        resp = admin.put("/api/settings/general", json={"A": 1})

    assert resp.status_code == 200
    assert resp.json == result
    update.assert_called_once_with("general", {"A": 1})


def test_update_tab_returns_400_with_the_result_when_it_fails(admin):
    result = {"success": False, "message": "Invalid", "errors": ["A"]}
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=object()),
        patch(f"{_REGISTRY}.update_settings", return_value=result),
    ):
        resp = admin.put("/api/settings/general", json={"A": 1})

    assert resp.status_code == 400
    assert resp.json == result


def test_update_tab_with_no_values_succeeds_without_saving(admin):
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=object()),
        patch(f"{_REGISTRY}.update_settings") as update,
    ):
        resp = admin.put("/api/settings/general", json={})

    assert resp.status_code == 200
    assert resp.json == {"success": True, "message": "No changes to save", "updated": []}
    update.assert_not_called()


@pytest.mark.parametrize("body", [[1, 2], "text", 5])
def test_update_tab_needs_a_json_object(admin, body):
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=object()),
        patch(f"{_REGISTRY}.update_settings") as update,
    ):
        resp = admin.put("/api/settings/general", json=body)

    assert resp.status_code == 400
    assert resp.json == {"error": "Request body must be a JSON object"}
    update.assert_not_called()


def test_update_tab_without_a_body_is_a_400(admin):
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=object()),
        patch(f"{_REGISTRY}.update_settings") as update,
    ):
        resp = admin.put("/api/settings/general")

    assert resp.status_code == 400
    update.assert_not_called()


def test_update_tab_is_a_404_for_an_unknown_tab_before_it_reads_the_body(admin):
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=None),
        patch(f"{_REGISTRY}.update_settings") as update,
    ):
        resp = admin.put("/api/settings/nope", json={"A": 1})

    assert resp.status_code == 404
    assert resp.json == {"error": "Unknown settings tab: nope"}
    update.assert_not_called()


def test_update_tab_reports_an_operational_error(admin):
    with (
        patch(f"{_REGISTRY}.get_settings_tab", return_value=object()),
        patch(f"{_REGISTRY}.update_settings", side_effect=OSError("disk")),
    ):
        resp = admin.put("/api/settings/general", json={"A": 1})

    assert resp.status_code == 500
    assert resp.json == {"error": "disk"}


# --------------------------------------------------------------------------- #
# POST /api/settings/<tab>/action/<key>
# --------------------------------------------------------------------------- #


def test_action_runs_with_the_unsaved_form_values(admin):
    with patch(f"{_REGISTRY}.execute_action", return_value={"success": True}) as run:
        resp = admin.post("/api/settings/general/action/test_it", json={"A": "typed"})

    assert resp.status_code == 200
    assert resp.json == {"success": True}
    run.assert_called_once_with("general", "test_it", {"A": "typed"})


def test_action_without_a_body_runs_with_no_values(admin):
    with patch(f"{_REGISTRY}.execute_action", return_value={"success": True}) as run:
        resp = admin.post("/api/settings/general/action/test_it")

    assert resp.status_code == 200
    run.assert_called_once_with("general", "test_it", {})


def test_action_returns_400_with_the_result_when_it_fails(admin):
    result = {"success": False, "message": "Could not connect"}
    with patch(f"{_REGISTRY}.execute_action", return_value=result):
        resp = admin.post("/api/settings/general/action/test_it", json={})

    assert resp.status_code == 400
    assert resp.json == result


def test_action_reports_an_operational_error(admin):
    with patch(f"{_REGISTRY}.execute_action", side_effect=RuntimeError("boom")):
        resp = admin.post("/api/settings/general/action/test_it", json={})

    assert resp.status_code == 500
    assert resp.json == {"error": "boom"}


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

_ROUTES = [
    ("get", "/api/settings"),
    ("get", "/api/settings/general"),
    ("put", "/api/settings/general"),
    ("post", "/api/settings/general/action/test_it"),
]


@pytest.mark.parametrize(("method", "path"), _ROUTES)
def test_settings_need_a_session_when_auth_is_on(client, builtin_auth, method, path):
    resp = getattr(client, method)(path, json={"A": 1})

    assert resp.status_code == 401


@pytest.mark.parametrize(("method", "path"), _ROUTES)
def test_settings_are_admin_only_for_a_signed_in_user(client, builtin_auth, method, path):
    _sign_in(client, is_admin=False)
    with (
        patch(f"{_REGISTRY}.serialize_all_settings") as serialize_all,
        patch(f"{_REGISTRY}.get_settings_tab") as get_tab,
        patch(f"{_REGISTRY}.update_settings") as update,
        patch(f"{_REGISTRY}.execute_action") as run,
    ):
        resp = getattr(client, method)(path, json={"A": 1})

    assert resp.status_code == 403
    assert resp.json == {"error": "Admin access required"}
    for mock in (serialize_all, get_tab, update, run):
        mock.assert_not_called()


def test_settings_are_open_when_no_auth_is_configured(main_module, client):
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch(f"{_REGISTRY}.serialize_all_settings", return_value={"tabs": []}),
    ):
        resp = client.get("/api/settings")

    assert resp.status_code == 200

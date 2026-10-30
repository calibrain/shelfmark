"""API tests for the onboarding endpoints: auth gating, validation and error handling."""

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


def test_onboarding_get_returns_the_config(admin):
    with patch(
        "shelfmark.core.onboarding.get_onboarding_config", return_value={"steps": [], "values": {}}
    ):
        resp = admin.get("/api/onboarding")

    assert resp.status_code == 200
    assert resp.json == {"steps": [], "values": {}}


def test_onboarding_get_reports_an_operational_error(admin):
    with patch("shelfmark.core.onboarding.get_onboarding_config", side_effect=RuntimeError("boom")):
        resp = admin.get("/api/onboarding")

    assert resp.status_code == 500
    assert resp.json == {"error": "boom"}


def test_onboarding_save_rejects_an_empty_body(admin):
    with patch("shelfmark.core.onboarding.save_onboarding_settings") as save:
        resp = admin.post("/api/onboarding", json={})

    assert resp.status_code == 400
    assert resp.json == {"success": False, "message": "No data provided"}
    save.assert_not_called()


def test_onboarding_save_passes_the_body_on_and_returns_the_result(admin):
    result = {"success": True, "message": "Saved"}
    with patch("shelfmark.core.onboarding.save_onboarding_settings", return_value=result) as save:
        resp = admin.post("/api/onboarding", json={"SEARCH_MODE": "universal"})

    assert resp.status_code == 200
    assert resp.json == result
    save.assert_called_once_with({"SEARCH_MODE": "universal"})


def test_onboarding_save_returns_400_with_the_result_when_it_fails(admin):
    result = {"success": False, "message": "Invalid value", "errors": ["x"]}
    with patch("shelfmark.core.onboarding.save_onboarding_settings", return_value=result):
        resp = admin.post("/api/onboarding", json={"SEARCH_MODE": "nope"})

    assert resp.status_code == 400
    assert resp.json == result


def test_onboarding_save_reports_an_operational_error(admin):
    with patch("shelfmark.core.onboarding.save_onboarding_settings", side_effect=OSError("disk")):
        resp = admin.post("/api/onboarding", json={"a": 1})

    assert resp.status_code == 500
    assert resp.json == {"error": "disk"}


def test_onboarding_skip_marks_it_complete(admin):
    with patch("shelfmark.core.onboarding.mark_onboarding_complete") as mark:
        resp = admin.post("/api/onboarding/skip")

    assert resp.status_code == 200
    assert resp.json == {"success": True, "message": "Onboarding skipped"}
    mark.assert_called_once_with()


def test_onboarding_skip_reports_an_operational_error(admin):
    with patch("shelfmark.core.onboarding.mark_onboarding_complete", side_effect=OSError("disk")):
        resp = admin.post("/api/onboarding/skip")

    assert resp.status_code == 500
    assert resp.json == {"error": "disk"}


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/onboarding"), ("post", "/api/onboarding"), ("post", "/api/onboarding/skip")],
)
def test_onboarding_needs_a_session_when_auth_is_on(client, builtin_auth, method, path):
    resp = getattr(client, method)(path)

    assert resp.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/onboarding"), ("post", "/api/onboarding"), ("post", "/api/onboarding/skip")],
)
def test_onboarding_is_admin_only_for_a_signed_in_user(client, builtin_auth, method, path):
    _sign_in(client, is_admin=False)
    with (
        patch("shelfmark.core.onboarding.get_onboarding_config") as get_config,
        patch("shelfmark.core.onboarding.save_onboarding_settings") as save,
        patch("shelfmark.core.onboarding.mark_onboarding_complete") as mark,
    ):
        resp = getattr(client, method)(path, json={"a": 1})

    assert resp.status_code == 403
    assert resp.json == {"error": "Admin access required"}
    get_config.assert_not_called()
    save.assert_not_called()
    mark.assert_not_called()


def test_onboarding_is_open_when_no_auth_is_configured(main_module, client):
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.core.onboarding.get_onboarding_config", return_value={"steps": []}),
    ):
        resp = client.get("/api/onboarding")

    assert resp.status_code == 200

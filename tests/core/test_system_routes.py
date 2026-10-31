"""Tests for the frontend-serving routes and the gaps in /api/config coverage."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

_INDEX = '<html><head><base href="/" data-shelfmark-base /></head><body>app</body></html>'


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


@pytest.fixture
def dist(main_module, tmp_path: Path):
    """A throwaway frontend build in place of the real `frontend-dist`."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "index.html").write_text(_INDEX)
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")
    (tmp_path / "favicon.ico").write_bytes(b"ico")
    (tmp_path / "theme-init.js").write_text("// theme")
    with (
        patch.object(main_module, "FRONTEND_DIST", tmp_path),
        patch.object(main_module, "BASE_PATH", ""),
    ):
        yield tmp_path


def test_index_serves_the_app_with_the_default_base_tag(client, dist):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    assert resp.get_data(as_text=True) == _INDEX


def test_index_rewrites_the_base_tag_for_a_subpath(main_module, client, dist):
    with patch.object(main_module, "BASE_PATH", "/books"):
        resp = client.get("/")

    assert '<base href="/books/" data-shelfmark-base />' in resp.get_data(as_text=True)


def test_index_without_a_frontend_build_is_a_404(client, dist):
    (dist / "index.html").unlink()

    assert client.get("/").status_code == 404


def test_catch_all_serves_the_app_for_client_side_routes(client, dist):
    resp = client.get("/settings/general")

    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == _INDEX


@pytest.mark.parametrize("path", ["/api/no-such-route", "/api/deeper/no-such-route"])
def test_catch_all_keeps_unknown_api_paths_a_404(client, dist, path):
    resp = client.get(path)

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Resource not found"}


def test_assets_are_served_from_the_build(client, dist):
    resp = client.get("/assets/app.js")

    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "console.log(1)"


def test_a_missing_asset_is_a_404_not_the_app(client, dist):
    assert client.get("/assets/missing.js").status_code == 404


@pytest.mark.parametrize(
    ("path", "mimetype"),
    [
        ("/logo.png", "image/png"),
        ("/favicon.ico", "image/vnd.microsoft.icon"),
        ("/favicon-32.png", "image/vnd.microsoft.icon"),
        ("/theme-init.js", "application/javascript"),
    ],
)
def test_root_files_are_served_with_their_mimetype(client, dist, path, mimetype):
    resp = client.get(path)

    assert resp.status_code == 200
    assert resp.mimetype == mimetype


def test_config_requires_a_session_when_auth_is_on(main_module, client):
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        resp = client.get("/api/config")

    assert resp.status_code == 401


def test_config_reports_an_operational_error(client):
    with patch("shelfmark.core.onboarding.is_onboarding_complete", side_effect=RuntimeError("x")):
        resp = client.get("/api/config")

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Could not load the configuration"}

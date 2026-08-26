"""API tests for POST /api/releases/inspect."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.download.postprocess.packs import PackFile


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    client = main_module.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "tester"
        sess["is_admin"] = False
    return client


class _Handler:
    def __init__(self, files):
        self._files = files

    def list_files(self, release_data):
        if isinstance(self._files, Exception):
            raise self._files
        return self._files


def _inspect(client, handler, payload=None):
    body = {
        "source": "audiobookbay",
        "source_id": "abc",
        "title": "Drive",
        "content_type": "audiobook",
        "series_name": "The Expanse",
        **(payload or {}),
    }
    with patch("shelfmark.core.release_inspect_routes.get_handler", return_value=handler):
        return client.post("/api/releases/inspect", json=body)


def test_pack_release_returns_a_plan(client):
    files = [
        PackFile("The Expanse 1.0 - Leviathan Wakes (2011).m4b", 100),
        PackFile("The Expanse 1.0 - Leviathan Wakes (2011).txt", 1),
        PackFile("The Expanse 2.0 - Caliban's War (2012).m4b", 100),
    ]
    resp = _inspect(client, _Handler(files))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["inspected"] is True
    assert data["reason"] is None
    assert data["files"] == [
        {"path": "The Expanse 1.0 - Leviathan Wakes (2011).m4b", "size": 100},
        {"path": "The Expanse 1.0 - Leviathan Wakes (2011).txt", "size": 1},
        {"path": "The Expanse 2.0 - Caliban's War (2012).m4b", "size": 100},
    ]
    assert data["plan"]["is_pack"] is True
    assert data["plan"]["ignored"] == ["The Expanse 1.0 - Leviathan Wakes (2011).txt"]
    assert data["plan"]["books"] == [
        {
            "title": "Leviathan Wakes",
            "series_position": 1.0,
            "year": 2011,
            "files": ["The Expanse 1.0 - Leviathan Wakes (2011).m4b"],
        },
        {
            "title": "Caliban's War",
            "series_position": 2.0,
            "year": 2012,
            "files": ["The Expanse 2.0 - Caliban's War (2012).m4b"],
        },
    ]


def test_single_book_release_is_not_a_pack(client):
    resp = _inspect(client, _Handler([PackFile("Drive.m4b", 5)]))
    data = resp.get_json()
    assert data["inspected"] is True
    assert data["plan"]["is_pack"] is False
    assert len(data["plan"]["books"]) == 1


def test_handler_without_file_list_reports_not_inspected(client):
    resp = _inspect(client, _Handler(None))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["inspected"] is False
    assert data["reason"]
    assert data["plan"] is None


def test_handler_failure_reports_not_inspected_without_500(client):
    resp = _inspect(client, _Handler(RuntimeError("boom")))
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["inspected"] is False
    assert "boom" in data["reason"]


def test_unknown_source_is_a_client_error(client):
    with patch(
        "shelfmark.core.release_inspect_routes.get_handler", side_effect=ValueError("no source")
    ):
        resp = client.post(
            "/api/releases/inspect", json={"source": "nope", "source_id": "x", "title": "t"}
        )
    assert resp.status_code == 400


def test_missing_source_id_is_a_client_error(client):
    resp = client.post("/api/releases/inspect", json={"source": "audiobookbay"})
    assert resp.status_code == 400


def test_requires_login(main_module):
    anonymous = main_module.app.test_client()
    with patch.object(main_module, "load_active_auth_mode", return_value="builtin"):
        resp = anonymous.post(
            "/api/releases/inspect", json={"source": "audiobookbay", "source_id": "a"}
        )
    assert resp.status_code == 401

"""Tests for the admin series-gap routes: the gate, the report and the request action."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core import library_index, series_gap
from shelfmark.core.series_gap_routes import register_series_gap_routes
from shelfmark.core.user_db import UserDB

REPORT = {
    "formats": ["ebook"],
    "series": [],
    "pending": 0,
    "generated_at": "2026-10-11T00:00:00+00:00",
}


@pytest.fixture
def user_db(tmp_path) -> UserDB:
    db = UserDB(str(tmp_path / "shelfmark.db"))
    db.initialize()
    db.create_user(username="admin", role="admin")
    return db


@pytest.fixture
def app(user_db: UserDB) -> Flask:
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    register_series_gap_routes(test_app, user_db)
    return test_app


@pytest.fixture
def admin_client(app: Flask):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin"
        sess["is_admin"] = True
    return client


@pytest.fixture
def regular_client(app: Flask):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user"
        sess["is_admin"] = False
    with patch("shelfmark.core.series_gap_routes.load_active_auth_mode", return_value="builtin"):
        yield client


@pytest.fixture
def no_session_auth_client(app: Flask):
    client = app.test_client()
    with patch("shelfmark.core.series_gap_routes.load_active_auth_mode", return_value="builtin"):
        yield client


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> object:
    stub = object()
    monkeypatch.setattr(series_gap, "hardcover_provider", lambda: stub)
    return stub


class TestGate:
    def test_a_non_admin_is_refused(self, regular_client) -> None:
        assert regular_client.get("/api/admin/series-gaps").status_code == 403
        assert regular_client.post("/api/admin/series-gaps/requests", json={}).status_code == 403

    def test_no_session_is_refused_when_auth_is_on(self, no_session_auth_client) -> None:
        assert no_session_auth_client.get("/api/admin/series-gaps").status_code == 401
        assert (
            no_session_auth_client.post("/api/admin/series-gaps/requests", json={}).status_code
            == 401
        )


class TestReport:
    def test_returns_the_report(self, admin_client, provider, user_db, monkeypatch) -> None:
        seen = {}

        def _build(db, prov):
            seen["args"] = (db, prov)
            return REPORT

        monkeypatch.setattr(series_gap, "build_report", _build)

        resp = admin_client.get("/api/admin/series-gaps")

        assert resp.status_code == 200
        assert resp.json == REPORT
        assert seen["args"] == (user_db, provider)

    def test_refresh_forgets_the_cached_answers_first(
        self, admin_client, provider, monkeypatch
    ) -> None:
        calls: list[str] = []
        monkeypatch.setattr(series_gap, "clear_cache", lambda: calls.append("clear") or 0)
        monkeypatch.setattr(series_gap, "build_report", lambda *_a: calls.append("build") or REPORT)

        admin_client.get("/api/admin/series-gaps?refresh=1")
        admin_client.get("/api/admin/series-gaps")

        assert calls == ["clear", "build", "build"]

    def test_without_a_hardcover_token_there_is_no_report(self, admin_client, monkeypatch) -> None:
        monkeypatch.setattr(series_gap, "hardcover_provider", lambda: None)

        resp = admin_client.get("/api/admin/series-gaps")

        assert resp.status_code == 503
        assert "Hardcover" in resp.json["error"]

    def test_an_unreadable_library_is_a_503_with_the_reason(
        self, admin_client, provider, monkeypatch
    ) -> None:
        def _down(*_args, **_kwargs):
            raise library_index.LibraryUnavailableError("Calibre library at /x is unavailable")

        monkeypatch.setattr(series_gap, "build_report", _down)

        resp = admin_client.get("/api/admin/series-gaps")

        assert resp.status_code == 503
        assert resp.json == {"error": "Calibre library at /x is unavailable"}


class TestRequests:
    def test_requests_the_chosen_volumes(self, admin_client, provider, monkeypatch) -> None:
        seen = {}

        def _request(_db, _prov, items):
            seen["items"] = items
            return {"created": 1, "skipped": 0, "errors": 0, "items": []}

        monkeypatch.setattr(series_gap, "request_volumes", _request)
        items = [{"series_id": 77, "provider_id": "3", "content_type": "ebook"}]

        resp = admin_client.post("/api/admin/series-gaps/requests", json={"items": items})

        assert resp.status_code == 200
        assert resp.json["created"] == 1
        assert seen["items"] == items

    def test_a_body_that_is_not_an_object_is_a_400(self, admin_client, provider) -> None:
        resp = admin_client.post(
            "/api/admin/series-gaps/requests", data="nope", content_type="text/plain"
        )

        assert resp.status_code == 400

    def test_a_malformed_item_is_a_400_with_the_reason(self, admin_client, provider) -> None:
        resp = admin_client.post("/api/admin/series-gaps/requests", json={"items": ["x"]})

        assert resp.status_code == 400
        assert resp.json == {"error": "Each item must be an object"}

    def test_a_service_refusal_keeps_its_status(self, admin_client, provider, monkeypatch) -> None:
        def _refuse(*_args, **_kwargs):
            raise series_gap.SeriesGapError("No admin user exists", status_code=503)

        monkeypatch.setattr(series_gap, "request_volumes", _refuse)

        resp = admin_client.post("/api/admin/series-gaps/requests", json={"items": [{}]})

        assert resp.status_code == 503
        assert resp.json == {"error": "No admin user exists"}

    def test_without_a_hardcover_token_nothing_is_requested(
        self, admin_client, monkeypatch
    ) -> None:
        monkeypatch.setattr(series_gap, "hardcover_provider", lambda: None)

        resp = admin_client.post("/api/admin/series-gaps/requests", json={"items": [{}]})

        assert resp.status_code == 503

"""Tests for the cross-format admin endpoints."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core import cross_format
from shelfmark.core.library_index import LibraryUnavailableError
from shelfmark.core.user_db import UserDB

PAIRS = "/api/admin/library/cross-format"
TAGS = "/api/admin/library/cross-format/audiobookshelf-tags"


@pytest.fixture
def app(tmp_path) -> Flask:
    from shelfmark.core.admin_routes import register_admin_routes

    user_db = UserDB(str(tmp_path / "users.db"))
    user_db.initialize()
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    register_admin_routes(test_app, user_db)
    return test_app


@pytest.fixture
def admin(app: Flask):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "admin"
        sess["is_admin"] = True
    return client


@pytest.fixture
def user(app: Flask):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user"
        sess["is_admin"] = False
    with patch("shelfmark.core.admin_routes.load_active_auth_mode", return_value="builtin"):
        yield client


def _summary(**overrides: Any) -> dict[str, Any]:
    summary = {
        "dry_run": True,
        "tag": "ebook-in-library",
        "pairs": 1,
        "ebooks_indexed": 2,
        "audiobooks_indexed": 3,
        "added": [{"id": "li_1", "title": "Dune"}],
        "removed": [],
        "already_current": 0,
        "errors": 0,
        "forbidden": False,
    }
    summary.update(overrides)
    return summary


def test_pairs_need_an_admin(user) -> None:
    assert user.get(PAIRS).status_code == 403
    assert user.post(TAGS, json={"dry_run": True}).status_code == 403


def test_pairs_are_reported(monkeypatch: pytest.MonkeyPatch, admin) -> None:
    report = {"pair_count": 1, "pairs": [], "by_calibre_id": {}, "by_audiobookshelf_id": {}}
    monkeypatch.setattr(cross_format, "pairs_report", lambda overrides=None: report)

    resp = admin.get(PAIRS)

    assert resp.status_code == 200
    assert resp.get_json() == report


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (cross_format.CrossFormatNotConfiguredError("enable both"), 400),
        (LibraryUnavailableError("Calibre is unavailable"), 503),
    ],
)
def test_pairs_report_errors_by_cause(
    monkeypatch: pytest.MonkeyPatch, admin, error: Exception, status: int
) -> None:
    def _raise(overrides=None):
        raise error

    monkeypatch.setattr(cross_format, "pairs_report", _raise)

    resp = admin.get(PAIRS)

    assert resp.status_code == status
    assert resp.get_json() == {"error": str(error)}


def test_tag_sync_defaults_to_a_dry_run(monkeypatch: pytest.MonkeyPatch, admin) -> None:
    seen: list[bool] = []

    def _sync(*, dry_run: bool, **_kwargs: Any) -> dict[str, Any]:
        seen.append(dry_run)
        return _summary(dry_run=dry_run)

    monkeypatch.setattr(cross_format, "sync_audiobookshelf_tags", _sync)

    assert admin.post(TAGS).status_code == 200
    assert admin.post(TAGS, json={}).status_code == 200
    assert admin.post(TAGS, json={"dry_run": False}).status_code == 200
    assert seen == [True, True, False]


def test_tag_sync_rejects_a_non_boolean_dry_run(admin) -> None:
    resp = admin.post(TAGS, json={"dry_run": "no"})

    assert resp.status_code == 400


def test_tag_sync_reports_the_summary_with_a_message(
    monkeypatch: pytest.MonkeyPatch, admin
) -> None:
    monkeypatch.setattr(
        cross_format, "sync_audiobookshelf_tags", lambda **_kwargs: _summary(dry_run=False)
    )

    resp = admin.post(TAGS, json={"dry_run": False})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["added"] == [{"id": "li_1", "title": "Dune"}]
    assert body["message"].startswith("1 pair(s) across 2 ebook(s) and 3 audiobook(s)")


def test_tag_sync_reports_an_audiobookshelf_refusal_as_upstream(
    monkeypatch: pytest.MonkeyPatch, admin
) -> None:
    monkeypatch.setattr(
        cross_format,
        "sync_audiobookshelf_tags",
        lambda **_kwargs: _summary(
            dry_run=False, added=[], forbidden=True, errors=1, error="refused (403)"
        ),
    )

    resp = admin.post(TAGS, json={"dry_run": False})

    assert resp.status_code == 502
    assert resp.get_json()["forbidden"] is True
    assert "refused (403)" in resp.get_json()["message"]


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (cross_format.CrossFormatDisabledError("tagging is off"), 409),
        (cross_format.CrossFormatNotConfiguredError("enable both"), 400),
        (LibraryUnavailableError("Audiobookshelf is unavailable"), 503),
    ],
)
def test_tag_sync_errors_by_cause(
    monkeypatch: pytest.MonkeyPatch, admin, error: Exception, status: int
) -> None:
    def _raise(**_kwargs: Any) -> dict[str, Any]:
        raise error

    monkeypatch.setattr(cross_format, "sync_audiobookshelf_tags", _raise)

    resp = admin.post(TAGS, json={"dry_run": False})

    assert resp.status_code == status
    assert resp.get_json() == {"error": str(error)}

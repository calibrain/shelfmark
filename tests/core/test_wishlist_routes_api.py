"""API tests for wishlist routes (/api/wishlist)."""

import os
import tempfile
from typing import Any
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.wishlist_routes import register_wishlist_routes
from shelfmark.core.user_db import UserDB


SAMPLE_BOOK = {"id": "book-1", "title": "Dune", "author": "Frank Herbert", "year": "1965"}
SAMPLE_BOOK_2 = {"id": "book-2", "title": "Foundation", "author": "Isaac Asimov", "year": "1951"}


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def app(user_db):
    test_app = Flask(__name__)
    test_app.config["SECRET_KEY"] = "test-secret"
    test_app.config["TESTING"] = True
    register_wishlist_routes(test_app, user_db)
    return test_app


def _authed_client(app: Flask, user: dict) -> Any:
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = False
    return client


def _patch_auth_mode(mode: str):
    return patch("shelfmark.core.wishlist_routes.load_active_auth_mode", return_value=mode)


class TestNoauthMode:
    """When auth_mode == 'none', any client gets a system user provisioned automatically."""

    def test_get_returns_200_and_empty_list(self, app):
        with _patch_auth_mode("none"):
            client = app.test_client()
            resp = client.get("/api/wishlist")
        assert resp.status_code == 200
        assert resp.json == []

    def test_post_adds_item(self, app):
        with _patch_auth_mode("none"):
            client = app.test_client()
            resp = client.post(
                "/api/wishlist",
                json={"book_id": "book-1", "book_data": SAMPLE_BOOK},
            )
        assert resp.status_code == 201
        assert resp.json["book_id"] == "book-1"
        assert resp.json["book_data"]["title"] == "Dune"

    def test_delete_removes_item(self, app, user_db):
        system_user = user_db.get_or_create_noauth_system_user()
        user_db.add_wishlist_item(system_user["id"], "book-1", SAMPLE_BOOK)

        with _patch_auth_mode("none"):
            client = app.test_client()
            resp = client.delete("/api/wishlist/book-1")
        assert resp.status_code == 200
        assert resp.json["ok"] is True

    def test_session_persists_system_user_across_requests(self, app):
        with _patch_auth_mode("none"):
            client = app.test_client()
            client.post(
                "/api/wishlist",
                json={"book_id": "book-1", "book_data": SAMPLE_BOOK},
            )
            resp = client.get("/api/wishlist")
        assert resp.status_code == 200
        assert len(resp.json) == 1
        assert resp.json[0]["book_id"] == "book-1"

    def test_system_user_is_idempotent_across_sessions(self, app, user_db):
        with _patch_auth_mode("none"):
            client_a = app.test_client()
            client_a.post(
                "/api/wishlist",
                json={"book_id": "book-1", "book_data": SAMPLE_BOOK},
            )
            # New session — same underlying system user
            client_b = app.test_client()
            resp = client_b.get("/api/wishlist")
        assert resp.status_code == 200
        assert len(resp.json) == 1


class TestBuiltinAuthMode:
    """When auth_mode == 'builtin', requests require a valid session."""

    def test_get_requires_authentication(self, app):
        with _patch_auth_mode("builtin"):
            client = app.test_client()
            resp = client.get("/api/wishlist")
        assert resp.status_code == 401

    def test_get_without_db_user_id_returns_403(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = app.test_client()
            with client.session_transaction() as sess:
                sess["user_id"] = user["username"]
                # Deliberately omit db_user_id
            resp = client.get("/api/wishlist")
        assert resp.status_code == 403

    def test_get_returns_empty_list_for_new_user(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.get("/api/wishlist")
        assert resp.status_code == 200
        assert resp.json == []

    def test_post_adds_item_for_authenticated_user(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.post(
                "/api/wishlist",
                json={"book_id": "book-1", "book_data": SAMPLE_BOOK},
            )
        assert resp.status_code == 201
        assert resp.json["book_id"] == "book-1"

    def test_post_returns_400_for_empty_book_id(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.post(
                "/api/wishlist",
                json={"book_id": "", "book_data": SAMPLE_BOOK},
            )
        assert resp.status_code == 400

    def test_post_returns_400_for_missing_book_data(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.post(
                "/api/wishlist",
                json={"book_id": "book-1", "book_data": "not-an-object"},
            )
        assert resp.status_code == 400

    def test_post_returns_400_for_non_json_body(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.post(
                "/api/wishlist",
                data="not json",
                content_type="text/plain",
            )
        assert resp.status_code == 400

    def test_delete_removes_item(self, app, user_db):
        user = user_db.create_user(username="alice")
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.delete("/api/wishlist/book-1")
        assert resp.status_code == 200
        assert resp.json["ok"] is True

    def test_delete_returns_404_for_nonexistent_item(self, app, user_db):
        user = user_db.create_user(username="alice")
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.delete("/api/wishlist/nonexistent")
        assert resp.status_code == 404

    def test_get_returns_items_for_authenticated_user(self, app, user_db):
        user = user_db.create_user(username="alice")
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        user_db.add_wishlist_item(user["id"], "book-2", SAMPLE_BOOK_2)
        with _patch_auth_mode("builtin"):
            client = _authed_client(app, user)
            resp = client.get("/api/wishlist")
        assert resp.status_code == 200
        book_ids = {item["book_id"] for item in resp.json}
        assert book_ids == {"book-1", "book-2"}

    def test_wishlist_is_per_user(self, app, user_db):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")
        user_db.add_wishlist_item(alice["id"], "book-1", SAMPLE_BOOK)

        with _patch_auth_mode("builtin"):
            bob_client = _authed_client(app, bob)
            resp = bob_client.get("/api/wishlist")
        assert resp.status_code == 200
        assert resp.json == []

    def test_delete_does_not_affect_other_users_items(self, app, user_db):
        alice = user_db.create_user(username="alice")
        bob = user_db.create_user(username="bob")
        user_db.add_wishlist_item(alice["id"], "book-1", SAMPLE_BOOK)
        user_db.add_wishlist_item(bob["id"], "book-1", SAMPLE_BOOK)

        with _patch_auth_mode("builtin"):
            alice_client = _authed_client(app, alice)
            alice_client.delete("/api/wishlist/book-1")

        assert user_db.get_wishlist_item(bob["id"], "book-1") is not None

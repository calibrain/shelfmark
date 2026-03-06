"""Unit tests for wishlist-related UserDB methods."""

import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    from shelfmark.core.user_db import UserDB

    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def user(user_db):
    return user_db.create_user(username="alice")


@pytest.fixture
def other_user(user_db):
    return user_db.create_user(username="bob")


SAMPLE_BOOK = {"id": "book-1", "title": "Dune", "author": "Frank Herbert", "year": "1965"}


class TestWishlistSchema:
    def test_initialize_creates_user_wishlist_table(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_wishlist'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_initialize_creates_user_wishlist_index(self, user_db, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_user_wishlist_user'"
        ).fetchone()
        conn.close()
        assert row is not None


class TestAddWishlistItem:
    def test_add_returns_item_with_parsed_book_data(self, user_db, user):
        item = user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        assert item["book_id"] == "book-1"
        assert item["book_data"] == SAMPLE_BOOK
        assert item["added_at"] is not None

    def test_add_upserts_when_book_already_exists(self, user_db, user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        updated_book = {**SAMPLE_BOOK, "title": "Dune Messiah"}
        item = user_db.add_wishlist_item(user["id"], "book-1", updated_book)
        assert item["book_data"]["title"] == "Dune Messiah"

    def test_add_trims_book_id_whitespace(self, user_db, user):
        item = user_db.add_wishlist_item(user["id"], "  book-1  ", SAMPLE_BOOK)
        assert item["book_id"] == "book-1"

    def test_add_raises_for_empty_book_id(self, user_db, user):
        with pytest.raises(ValueError, match="book_id"):
            user_db.add_wishlist_item(user["id"], "", SAMPLE_BOOK)

    def test_add_raises_for_whitespace_only_book_id(self, user_db, user):
        with pytest.raises(ValueError, match="book_id"):
            user_db.add_wishlist_item(user["id"], "   ", SAMPLE_BOOK)

    def test_add_raises_for_non_dict_book_data(self, user_db, user):
        with pytest.raises(ValueError, match="book_data"):
            user_db.add_wishlist_item(user["id"], "book-1", ["not", "a", "dict"])


class TestRemoveWishlistItem:
    def test_remove_returns_true_when_item_exists(self, user_db, user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        assert user_db.remove_wishlist_item(user["id"], "book-1") is True

    def test_remove_returns_false_when_item_not_found(self, user_db, user):
        assert user_db.remove_wishlist_item(user["id"], "nonexistent") is False

    def test_remove_deletes_item_from_db(self, user_db, user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        user_db.remove_wishlist_item(user["id"], "book-1")
        assert user_db.get_wishlist_item(user["id"], "book-1") is None

    def test_remove_only_affects_specified_user(self, user_db, user, other_user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        user_db.add_wishlist_item(other_user["id"], "book-1", SAMPLE_BOOK)
        user_db.remove_wishlist_item(user["id"], "book-1")
        assert user_db.get_wishlist_item(other_user["id"], "book-1") is not None


class TestListWishlistItems:
    def test_list_returns_empty_for_new_user(self, user_db, user):
        assert user_db.list_wishlist_items(user["id"]) == []

    def test_list_returns_added_items(self, user_db, user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        items = user_db.list_wishlist_items(user["id"])
        assert len(items) == 1
        assert items[0]["book_id"] == "book-1"

    def test_list_returns_newest_first(self, user_db, user):
        user_db.add_wishlist_item(user["id"], "book-1", {**SAMPLE_BOOK, "id": "book-1"})
        user_db.add_wishlist_item(user["id"], "book-2", {**SAMPLE_BOOK, "id": "book-2"})
        items = user_db.list_wishlist_items(user["id"])
        assert items[0]["book_id"] == "book-2"
        assert items[1]["book_id"] == "book-1"

    def test_list_is_scoped_to_user(self, user_db, user, other_user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        assert user_db.list_wishlist_items(other_user["id"]) == []


class TestGetWishlistItem:
    def test_get_returns_item_by_book_id(self, user_db, user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        item = user_db.get_wishlist_item(user["id"], "book-1")
        assert item is not None
        assert item["book_id"] == "book-1"
        assert item["book_data"] == SAMPLE_BOOK

    def test_get_returns_none_for_missing_item(self, user_db, user):
        assert user_db.get_wishlist_item(user["id"], "nonexistent") is None

    def test_get_is_scoped_to_user(self, user_db, user, other_user):
        user_db.add_wishlist_item(user["id"], "book-1", SAMPLE_BOOK)
        assert user_db.get_wishlist_item(other_user["id"], "book-1") is None


class TestGetOrCreateNoauthSystemUser:
    def test_creates_system_user_on_first_call(self, user_db):
        system_user = user_db.get_or_create_noauth_system_user()
        assert system_user is not None
        assert system_user["username"] == "__noauth__"
        assert system_user["role"] == "admin"

    def test_returns_same_user_on_repeated_calls(self, user_db):
        first = user_db.get_or_create_noauth_system_user()
        second = user_db.get_or_create_noauth_system_user()
        assert first["id"] == second["id"]

    def test_system_user_is_retrievable_by_username(self, user_db):
        user_db.get_or_create_noauth_system_user()
        found = user_db.get_user(username="__noauth__")
        assert found is not None

    def test_system_user_can_hold_wishlist_items(self, user_db):
        system_user = user_db.get_or_create_noauth_system_user()
        user_db.add_wishlist_item(system_user["id"], "book-1", SAMPLE_BOOK)
        items = user_db.list_wishlist_items(system_user["id"])
        assert len(items) == 1
        assert items[0]["book_id"] == "book-1"

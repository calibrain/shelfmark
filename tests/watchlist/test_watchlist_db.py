"""Tests for WatchlistDB — schema creation, CRUD, and idempotency."""

import json
import os
import sqlite3
import tempfile

import pytest


@pytest.fixture
def db_path():
    """Temporary SQLite database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "test.db")


@pytest.fixture
def raw_conn(db_path):
    """Raw sqlite3 connection for schema inspection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def watchlist_db(db_path):
    """Initialized WatchlistDB instance."""
    from shelfmark.watchlist.db import WatchlistDB

    db = WatchlistDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def user_db(db_path):
    """Initialized UserDB instance sharing the same DB file."""
    from shelfmark.core.user_db import UserDB

    db = UserDB(db_path)
    db.initialize()
    return db


@pytest.fixture
def full_db(db_path):
    """Both UserDB and WatchlistDB initialized on the same file."""
    from shelfmark.core.user_db import UserDB
    from shelfmark.watchlist.db import WatchlistDB

    udb = UserDB(db_path)
    udb.initialize()
    wdb = WatchlistDB(db_path)
    wdb.initialize()
    return udb, wdb


@pytest.fixture
def test_user(full_db):
    """A real user row for FK tests."""
    udb, wdb = full_db
    user = udb.create_user(username="testuser", password_hash="hash")
    return user, wdb


# ------------------------------------------------------------------
# Schema creation
# ------------------------------------------------------------------

class TestSchema:
    def test_creates_watchlist_authors_table(self, watchlist_db, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watchlist_authors'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_creates_watchlist_releases_table(self, watchlist_db, db_path):
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watchlist_releases'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_initialize_is_idempotent(self, db_path):
        from shelfmark.watchlist.db import WatchlistDB

        db = WatchlistDB(db_path)
        db.initialize()
        db.initialize()  # should not raise

    def test_authors_table_has_expected_columns(self, watchlist_db, db_path):
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist_authors)")}
        conn.close()
        expected = {
            "id", "user_id", "author_name", "hardcover_author_id", "ol_author_key",
            "watch_content_types", "is_active", "deleted_at", "created_at", "updated_at",
        }
        assert expected <= cols

    def test_releases_table_has_expected_columns(self, watchlist_db, db_path):
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(watchlist_releases)")}
        conn.close()
        expected = {
            "id", "watch_id", "user_id", "provider", "provider_book_id",
            "book_data", "publish_date", "content_type", "action_status",
            "request_id", "detected_at", "actioned_at",
        }
        assert expected <= cols


# ------------------------------------------------------------------
# add_author
# ------------------------------------------------------------------

class TestAddAuthor:
    def test_add_author_hardcover(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"],
            author_name="Brandon Sanderson",
            hardcover_author_id="12345",
        )
        assert entry["author_name"] == "Brandon Sanderson"
        assert entry["hardcover_author_id"] == "12345"
        assert entry["is_active"] == 1
        assert entry["deleted_at"] is None

    def test_add_author_ol(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"],
            author_name="Ursula K. Le Guin",
            ol_author_key="/authors/OL18211A",
        )
        assert entry["ol_author_key"] == "/authors/OL18211A"

    def test_add_author_both_providers(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"],
            author_name="Terry Pratchett",
            hardcover_author_id="99",
            ol_author_key="/authors/OL1A",
        )
        assert entry["hardcover_author_id"] == "99"
        assert entry["ol_author_key"] == "/authors/OL1A"

    def test_add_author_default_content_types(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"],
            author_name="N.K. Jemisin",
            hardcover_author_id="777",
        )
        assert set(entry["watch_content_types"]) == {"ebook", "audiobook"}

    def test_add_author_custom_content_types(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"],
            author_name="N.K. Jemisin",
            hardcover_author_id="777",
            watch_content_types=["ebook"],
        )
        assert entry["watch_content_types"] == ["ebook"]

    def test_add_author_requires_name(self, test_user):
        user, wdb = test_user
        with pytest.raises(ValueError, match="author_name"):
            wdb.add_author(user_id=user["id"], author_name="", hardcover_author_id="1")

    def test_add_author_requires_at_least_one_provider_key(self, test_user):
        user, wdb = test_user
        with pytest.raises(ValueError, match="hardcover_author_id or ol_author_key"):
            wdb.add_author(user_id=user["id"], author_name="Nobody")

    def test_add_author_rejects_invalid_content_type(self, test_user):
        user, wdb = test_user
        with pytest.raises(ValueError, match="Invalid content types"):
            wdb.add_author(
                user_id=user["id"],
                author_name="Bad",
                hardcover_author_id="1",
                watch_content_types=["magazine"],
            )

    def test_duplicate_hardcover_id_raises(self, test_user):
        user, wdb = test_user
        wdb.add_author(
            user_id=user["id"], author_name="Author A", hardcover_author_id="42"
        )
        with pytest.raises(ValueError, match="already exists"):
            wdb.add_author(
                user_id=user["id"], author_name="Author A Again", hardcover_author_id="42"
            )

    def test_duplicate_ol_key_raises(self, test_user):
        user, wdb = test_user
        wdb.add_author(
            user_id=user["id"], author_name="Author B", ol_author_key="/authors/OL1A"
        )
        with pytest.raises(ValueError, match="already exists"):
            wdb.add_author(
                user_id=user["id"], author_name="Author B Again", ol_author_key="/authors/OL1A"
            )


# ------------------------------------------------------------------
# get_author / list_authors
# ------------------------------------------------------------------

class TestGetListAuthors:
    def test_get_author_returns_entry(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Ann Leckie", hardcover_author_id="55"
        )
        fetched = wdb.get_author(entry["id"])
        assert fetched is not None
        assert fetched["id"] == entry["id"]

    def test_get_author_returns_none_for_missing(self, watchlist_db):
        assert watchlist_db.get_author(99999) is None

    def test_list_authors_returns_active_only_by_default(self, test_user):
        user, wdb = test_user
        e1 = wdb.add_author(
            user_id=user["id"], author_name="Active Author", hardcover_author_id="1"
        )
        e2 = wdb.add_author(
            user_id=user["id"], author_name="Inactive Author", hardcover_author_id="2"
        )
        wdb.update_author(e2["id"], is_active=False)
        authors = wdb.list_authors(user["id"])
        ids = [a["id"] for a in authors]
        assert e1["id"] in ids
        assert e2["id"] not in ids

    def test_list_authors_include_inactive(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Inactive", hardcover_author_id="3"
        )
        wdb.update_author(e["id"], is_active=False)
        authors = wdb.list_authors(user["id"], include_inactive=True)
        assert any(a["id"] == e["id"] for a in authors)

    def test_list_authors_excludes_deleted(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Deleted Author", hardcover_author_id="4"
        )
        wdb.remove_author(e["id"])
        authors = wdb.list_authors(user["id"], include_inactive=True)
        assert not any(a["id"] == e["id"] for a in authors)


# ------------------------------------------------------------------
# update_author / remove_author
# ------------------------------------------------------------------

class TestUpdateRemoveAuthor:
    def test_toggle_inactive(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Toggle Me", hardcover_author_id="10"
        )
        updated = wdb.update_author(e["id"], is_active=False)
        assert updated is not None
        assert updated["is_active"] == 0

    def test_update_content_types(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Update Types", hardcover_author_id="11"
        )
        updated = wdb.update_author(e["id"], watch_content_types=["audiobook"])
        assert updated is not None
        assert updated["watch_content_types"] == ["audiobook"]

    def test_update_rejects_invalid_content_type(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Bad Update", hardcover_author_id="12"
        )
        with pytest.raises(ValueError, match="Invalid content types"):
            wdb.update_author(e["id"], watch_content_types=["comic"])

    def test_remove_author_soft_deletes(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Remove Me", hardcover_author_id="20"
        )
        result = wdb.remove_author(e["id"])
        assert result is True
        assert wdb.get_author(e["id"]) is None

    def test_remove_author_returns_false_when_not_found(self, watchlist_db):
        assert watchlist_db.remove_author(99999) is False

    def test_remove_author_is_idempotent(self, test_user):
        user, wdb = test_user
        e = wdb.add_author(
            user_id=user["id"], author_name="Remove Twice", hardcover_author_id="21"
        )
        wdb.remove_author(e["id"])
        result = wdb.remove_author(e["id"])
        assert result is False


# ------------------------------------------------------------------
# upsert_release
# ------------------------------------------------------------------

class TestUpsertRelease:
    def _sample_book_data(self) -> dict:
        return {
            "provider": "hardcover",
            "provider_id": "hc-book-1",
            "title": "The Way of Kings",
            "authors": ["Brandon Sanderson"],
            "publish_year": 2010,
        }

    def test_upsert_creates_release(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Sanderson", hardcover_author_id="99"
        )
        release = wdb.upsert_release(
            watch_id=entry["id"],
            user_id=user["id"],
            provider="hardcover",
            provider_book_id="hc-book-1",
            book_data=self._sample_book_data(),
            content_type="ebook",
            publish_date="2010-08-31",
        )
        assert release["provider_book_id"] == "hc-book-1"
        assert release["action_status"] == "detected"
        assert release["book_data"]["title"] == "The Way of Kings"

    def test_upsert_is_idempotent(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Sanderson", hardcover_author_id="99"
        )
        r1 = wdb.upsert_release(
            watch_id=entry["id"],
            user_id=user["id"],
            provider="hardcover",
            provider_book_id="hc-book-1",
            book_data=self._sample_book_data(),
            content_type="ebook",
        )
        r2 = wdb.upsert_release(
            watch_id=entry["id"],
            user_id=user["id"],
            provider="hardcover",
            provider_book_id="hc-book-1",
            book_data=self._sample_book_data(),
            content_type="ebook",
        )
        assert r1["id"] == r2["id"]

    def test_upsert_rejects_invalid_content_type(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Author", hardcover_author_id="100"
        )
        with pytest.raises(ValueError, match="content_type"):
            wdb.upsert_release(
                watch_id=entry["id"],
                user_id=user["id"],
                provider="hardcover",
                provider_book_id="bad-1",
                book_data=self._sample_book_data(),
                content_type="magazine",
            )

    def test_upsert_rejects_empty_book_data(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Author", hardcover_author_id="101"
        )
        with pytest.raises(ValueError, match="book_data"):
            wdb.upsert_release(
                watch_id=entry["id"],
                user_id=user["id"],
                provider="hardcover",
                provider_book_id="bad-2",
                book_data={},
                content_type="ebook",
            )


# ------------------------------------------------------------------
# list_releases / update_release_action
# ------------------------------------------------------------------

class TestReleasesQueryAndUpdate:
    def _add_release(self, wdb, watch_id, user_id, book_id, content_type="ebook"):
        return wdb.upsert_release(
            watch_id=watch_id,
            user_id=user_id,
            provider="hardcover",
            provider_book_id=book_id,
            book_data={"title": f"Book {book_id}", "provider": "hardcover", "provider_id": book_id},
            content_type=content_type,
        )

    def test_list_releases_returns_user_releases(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Author", hardcover_author_id="200"
        )
        self._add_release(wdb, entry["id"], user["id"], "book-a")
        self._add_release(wdb, entry["id"], user["id"], "book-b")
        releases = wdb.list_releases(user["id"])
        assert len(releases) == 2

    def test_list_releases_filter_by_status(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Author", hardcover_author_id="201"
        )
        r = self._add_release(wdb, entry["id"], user["id"], "book-c")
        wdb.update_release_action(r["id"], action_status="queued")
        detected = wdb.list_releases(user["id"], action_status="detected")
        queued = wdb.list_releases(user["id"], action_status="queued")
        assert len(detected) == 0
        assert len(queued) == 1

    def test_update_release_action_sets_status(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Author", hardcover_author_id="202"
        )
        r = self._add_release(wdb, entry["id"], user["id"], "book-d")
        updated = wdb.update_release_action(r["id"], action_status="skipped")
        assert updated is not None
        assert updated["action_status"] == "skipped"
        assert updated["actioned_at"] is not None

    def test_update_release_action_rejects_invalid_status(self, test_user):
        user, wdb = test_user
        entry = wdb.add_author(
            user_id=user["id"], author_name="Author", hardcover_author_id="203"
        )
        r = self._add_release(wdb, entry["id"], user["id"], "book-e")
        with pytest.raises(ValueError, match="action_status"):
            wdb.update_release_action(r["id"], action_status="purchased")


# ------------------------------------------------------------------
# Cascade on user delete
# ------------------------------------------------------------------

class TestCascade:
    def test_delete_user_cascades_to_authors(self, full_db, db_path):
        udb, wdb = full_db
        user = udb.create_user(username="cascade_user", password_hash="x")
        wdb.add_author(
            user_id=user["id"], author_name="Cascade Author", hardcover_author_id="999"
        )
        udb.delete_user(user["id"])
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        rows = conn.execute(
            "SELECT * FROM watchlist_authors WHERE user_id = ?", (user["id"],)
        ).fetchall()
        conn.close()
        assert len(rows) == 0

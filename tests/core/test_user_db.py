"""Tests for SQLite user and canonical Book-request persistence."""

import os
import sqlite3
import tempfile

import pytest

from shelfmark.core.user_db import UserDB


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


def add_book(user_db, user):
    conn = user_db._connect()
    try:
        cursor = conn.execute(
            "INSERT INTO books (metadata_provider, provider_book_id, title) VALUES (?, ?, ?)",
            ("test", f"book-{user['id']}", "Test Book"),
        )
        book_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO user_library (user_id, book_id) VALUES (?, ?)", (user["id"], book_id)
        )
        conn.commit()
        return book_id
    finally:
        conn.close()


def test_initialize_creates_canonical_request_schema(user_db, db_path):
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(download_requests)")}
    conn.close()
    assert columns == {
        "id",
        "user_id",
        "book_id",
        "status",
        "note",
        "admin_note",
        "reviewed_by",
        "created_at",
        "reviewed_at",
    }


def test_initialize_replaces_legacy_requests_without_affecting_other_tables(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            oidc_subject TEXT UNIQUE,
            role TEXT NOT NULL DEFAULT 'user'
        );
        CREATE TABLE download_requests (id INTEGER PRIMARY KEY, user_id INTEGER, content_type TEXT, book_data TEXT);
    """)
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'legacy')")
    conn.execute("INSERT INTO download_requests VALUES (1, 1, 'ebook', '{}')")
    conn.commit()
    conn.close()

    UserDB(db_path).initialize()

    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM download_requests").fetchone()[0] == 0
    assert conn.execute("SELECT username FROM users WHERE id = 1").fetchone() == ("legacy",)
    assert {row[1] for row in conn.execute("PRAGMA table_info(download_requests)")} == {
        "id",
        "user_id",
        "book_id",
        "status",
        "note",
        "admin_note",
        "reviewed_by",
        "created_at",
        "reviewed_at",
    }
    conn.close()


def test_initialize_migrates_legacy_email_notification_to_canonical_user_email(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            oidc_subject TEXT,
            auth_source TEXT NOT NULL DEFAULT 'builtin'
        );
        CREATE TABLE user_preferences (
            user_id INTEGER PRIMARY KEY,
            kindle_address TEXT,
            notifications_enabled INTEGER NOT NULL DEFAULT 0,
            notification_transport TEXT,
            notification_destination TEXT
        );
    """)
    conn.execute(
        "INSERT INTO users (id, username, email, auth_source) VALUES (1, 'reader', 'source@example.com', 'oidc')"
    )
    conn.execute("INSERT INTO user_preferences VALUES (1, NULL, 1, 'email', 'notify@example.com')")
    conn.commit()
    conn.close()

    user_db = UserDB(db_path)
    user_db.initialize()

    user = user_db.get_user(user_id=1)
    assert user is not None
    assert user["email"] == "notify@example.com"
    assert user["identity_email"] == "source@example.com"
    assert user_db.get_personal_preferences(1)["notification_transport"] is None
    assert user_db.get_personal_preferences(1)["notification_destination"] is None


def test_initialize_disables_malformed_and_absent_legacy_email_notifications(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            oidc_subject TEXT
        );
        CREATE TABLE user_preferences (
            user_id INTEGER PRIMARY KEY,
            kindle_address TEXT,
            notifications_enabled INTEGER NOT NULL DEFAULT 0,
            notification_transport TEXT,
            notification_destination TEXT
        );
    """)
    conn.execute(
        "INSERT INTO users (id, username, email) VALUES (1, 'reader', 'reader@example.com')"
    )
    conn.execute(
        "INSERT INTO users (id, username, email) VALUES (2, 'missing', 'missing@example.com')"
    )
    conn.execute("INSERT INTO user_preferences VALUES (1, NULL, 1, 'email', 'not an email')")
    conn.execute("INSERT INTO user_preferences VALUES (2, NULL, 1, 'email', NULL)")
    conn.commit()
    conn.close()

    user_db = UserDB(db_path)
    user_db.initialize()

    preferences = user_db.get_personal_preferences(1)
    assert preferences["notifications_enabled"] is False
    assert preferences["notification_transport"] is None
    assert preferences["notification_destination"] is None
    assert user_db.get_personal_preferences(2)["notifications_enabled"] is False


def test_initialize_preserves_duplicate_legacy_email_notification_destinations(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            email TEXT,
            oidc_subject TEXT
        );
        CREATE TABLE user_preferences (
            user_id INTEGER PRIMARY KEY,
            kindle_address TEXT,
            notifications_enabled INTEGER NOT NULL DEFAULT 0,
            notification_transport TEXT,
            notification_destination TEXT
        );
    """)
    conn.execute("INSERT INTO users (id, username) VALUES (1, 'first')")
    conn.execute("INSERT INTO users (id, username) VALUES (2, 'second')")
    conn.execute("INSERT INTO user_preferences VALUES (1, NULL, 1, 'email', 'shared@example.com')")
    conn.execute("INSERT INTO user_preferences VALUES (2, NULL, 1, 'email', 'shared@example.com')")
    conn.commit()
    conn.close()

    user_db = UserDB(db_path)
    user_db.initialize()

    assert user_db.get_user(user_id=1)["email"] == "shared@example.com"
    assert user_db.get_user(user_id=2)["email"] == "shared@example.com"


def test_book_request_lifecycle(user_db):
    member = user_db.create_user(username="member", library_capability="request-only")
    admin = user_db.create_user(username="admin", role="admin")
    book_id = add_book(user_db, member)

    created = user_db.create_library_request(
        user_id=member["id"], book_id=book_id, note="Please add"
    )
    assert created["book_id"] == book_id
    assert created["status"] == "pending"
    assert user_db.list_pending_book_requests(book_id) == [created]
    assert user_db.list_requests(user_id=member["id"], status="pending") == [created]

    rejected = user_db.update_request(
        created["id"],
        expected_current_status="pending",
        status="rejected",
        admin_note="No",
        reviewed_by=admin["id"],
    )
    assert rejected["status"] == "rejected"
    assert rejected["admin_note"] == "No"
    assert user_db.get_request(created["id"]) == rejected


def test_fulfil_pending_book_requests_grants_completed_files(user_db):
    member = user_db.create_user(username="member")
    admin = user_db.create_user(username="admin")
    book_id = add_book(user_db, member)
    request = user_db.create_library_request(user_id=member["id"], book_id=book_id)
    conn = user_db._connect()
    try:
        conn.execute(
            "INSERT INTO download_history (task_id, source, title, final_status, download_path, book_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("task", "test", "Test Book", "complete", "/books/test.epub", book_id),
        )
        conn.commit()
    finally:
        conn.close()

    fulfilled = user_db.fulfil_pending_book_requests(book_id=book_id, reviewed_by=admin["id"])
    assert fulfilled[0]["id"] == request["id"]
    assert fulfilled[0]["status"] == "fulfilled"


def test_delete_user_cascades_requests_and_clears_reviewer(user_db):
    member = user_db.create_user(username="member")
    admin = user_db.create_user(username="admin")
    book_id = add_book(user_db, member)
    request = user_db.create_library_request(user_id=member["id"], book_id=book_id)
    user_db.update_request(request["id"], reviewed_by=admin["id"])
    user_db.delete_user(admin["id"])
    assert user_db.get_request(request["id"])["reviewed_by"] is None
    user_db.delete_user(member["id"])
    assert user_db.get_request(request["id"]) is None

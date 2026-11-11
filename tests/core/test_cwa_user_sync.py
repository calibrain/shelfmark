"""Tests for CWA user linking/provisioning helpers."""

import os
import sqlite3
import tempfile

import pytest

from shelfmark.core.cwa_user_sync import (
    connect_cwa_db,
    sync_cwa_users_from_rows,
    upsert_cwa_user,
)
from shelfmark.core.user_db import UserDB


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "users.db"))
        db.initialize()
        yield db


def test_upsert_links_existing_user_by_unique_email(user_db):
    existing = user_db.create_user(
        username="local_admin",
        email="admin@example.com",
        role="admin",
        auth_source="builtin",
    )

    user, action = upsert_cwa_user(
        user_db,
        cwa_username="admin",
        cwa_email="admin@example.com",
        role="user",
    )

    assert action == "updated"
    assert user["id"] == existing["id"]
    assert user["username"] == "local_admin"
    assert user["auth_source"] == "cwa"
    assert user["role"] == "user"


def test_upsert_creates_alias_when_username_taken_by_non_cwa(user_db):
    local_user = user_db.create_user(
        username="admin",
        email="local@example.com",
        role="admin",
        auth_source="builtin",
    )

    user, action = upsert_cwa_user(
        user_db,
        cwa_username="admin",
        cwa_email="cwa@example.com",
        role="admin",
    )

    assert action == "created"
    assert user["username"].startswith("admin__cwa")
    assert user["auth_source"] == "cwa"
    assert user["email"] == "cwa@example.com"

    local_after = user_db.get_user(user_id=local_user["id"])
    assert local_after is not None
    assert local_after["username"] == "admin"
    assert local_after["auth_source"] == "builtin"
    assert local_after["email"] == "local@example.com"


def test_upsert_updates_existing_cwa_user_by_username_before_email(user_db):
    cwa_user = user_db.create_user(
        username="reader",
        email="old@example.com",
        role="user",
        auth_source="cwa",
    )
    user_db.create_user(
        username="other",
        email="new@example.com",
        role="user",
        auth_source="builtin",
    )

    user, action = upsert_cwa_user(
        user_db,
        cwa_username="reader",
        cwa_email="new@example.com",
        role="admin",
    )

    assert action == "updated"
    assert user["id"] == cwa_user["id"]
    assert user["username"] == "reader"
    assert user["auth_source"] == "cwa"
    assert user["email"] == "new@example.com"
    assert user["role"] == "admin"


def test_upsert_renames_existing_cwa_user_matched_by_email(user_db):
    cwa_user = user_db.create_user(
        username="old_reader",
        email="reader@example.com",
        role="user",
        auth_source="cwa",
    )

    user, action = upsert_cwa_user(
        user_db,
        cwa_username="renamed_reader",
        cwa_email="reader@example.com",
        role="user",
    )

    assert action == "updated"
    assert user["id"] == cwa_user["id"]
    assert user["username"] == "renamed_reader"
    assert user_db.get_user(username="old_reader") is None


def test_upsert_uses_stable_alias_when_renamed_cwa_username_is_taken(user_db):
    cwa_user = user_db.create_user(
        username="old_reader",
        email="reader@example.com",
        role="user",
        auth_source="cwa",
    )
    local_user = user_db.create_user(
        username="renamed_reader",
        email="local@example.com",
        role="user",
        auth_source="builtin",
    )

    first, first_action = upsert_cwa_user(
        user_db,
        cwa_username="renamed_reader",
        cwa_email="reader@example.com",
        role="admin",
    )
    second, second_action = upsert_cwa_user(
        user_db,
        cwa_username="renamed_reader",
        cwa_email="reader@example.com",
        role="admin",
    )

    assert first_action == second_action == "updated"
    assert first["id"] == second["id"] == cwa_user["id"]
    assert first["username"] == second["username"] == "renamed_reader__cwa"
    assert first["role"] == second["role"] == "admin"
    local_after = user_db.get_user(user_id=local_user["id"])
    assert local_after is not None
    assert local_after["username"] == "renamed_reader"
    assert local_after["email"] == "local@example.com"


def test_sync_prunes_cwa_users_missing_from_source(user_db):
    active_cwa = user_db.create_user(
        username="active_cwa",
        email="active@example.com",
        role="user",
        auth_source="cwa",
    )
    stale_cwa = user_db.create_user(
        username="stale_cwa",
        email="stale@example.com",
        role="admin",
        auth_source="cwa",
    )
    local_builtin = user_db.create_user(
        username="local_user",
        email="local@example.com",
        role="admin",
        auth_source="builtin",
    )

    summary = sync_cwa_users_from_rows(
        user_db,
        rows=[("active_cwa", 1, "active@example.com")],
    )

    assert summary["created"] == 0
    assert summary["updated"] == 1
    assert summary["deleted"] == 1
    assert summary["total"] == 1

    active_after = user_db.get_user(user_id=active_cwa["id"])
    assert active_after is not None
    assert active_after["role"] == "admin"

    assert user_db.get_user(user_id=stale_cwa["id"]) is None
    assert user_db.get_user(user_id=local_builtin["id"]) is not None


def _make_wal_app_db(path):
    """Mimic Calibre-Web's app.db: WAL mode, one user, one write left in the WAL."""
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.execute("CREATE TABLE user (name TEXT, password TEXT, role INTEGER, email TEXT)")
    writer.execute("INSERT INTO user VALUES ('admin', 'old-hash', 1, NULL)")
    writer.commit()
    writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    # Not checkpointed: only visible to readers that read the WAL.
    writer.execute("UPDATE user SET name = 'renamed', password = 'new-hash'")
    writer.commit()
    return writer


def test_connect_cwa_db_sees_writes_still_in_the_wal(tmp_path):
    db_path = tmp_path / "app.db"
    writer = _make_wal_app_db(db_path)
    try:
        conn = connect_cwa_db(db_path)
        try:
            rows = conn.execute("SELECT name, password FROM user").fetchall()
        finally:
            conn.close()
    finally:
        writer.close()

    assert rows == [("renamed", "new-hash")]


def test_connect_cwa_db_is_read_only(tmp_path):
    db_path = tmp_path / "app.db"
    _make_wal_app_db(db_path).close()

    conn = connect_cwa_db(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DELETE FROM user")
    finally:
        conn.close()


def test_connect_cwa_db_falls_back_to_immutable_when_the_plain_open_fails(tmp_path, monkeypatch):
    db_path = tmp_path / "app.db"
    writer = _make_wal_app_db(db_path)

    real_connect = sqlite3.connect
    uris = []

    def fake_connect(database, **kwargs):
        uris.append(database)
        if "immutable=1" not in database:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(database, **kwargs)

    monkeypatch.setattr("shelfmark.core.cwa_user_sync.sqlite3.connect", fake_connect)
    try:
        conn = connect_cwa_db(db_path)
        try:
            rows = conn.execute("SELECT name, password FROM user").fetchall()
        finally:
            conn.close()
    finally:
        writer.close()

    assert uris[0].endswith("?mode=ro")
    assert uris[1].endswith("?mode=ro&immutable=1")
    # The fallback skips the WAL, so it only sees the last checkpoint.
    assert rows == [("admin", "old-hash")]

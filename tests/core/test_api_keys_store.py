"""Tests for the api_keys table and UserDB key methods."""

import os
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime, timedelta

import pytest

from shelfmark.core.user_db import ApiKeyLimitReachedError


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
def alice(user_db):
    return user_db.create_user(username="alice")


def test_initialize_creates_api_keys_table(db_path, user_db):
    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(api_keys)").fetchall()}
    finally:
        conn.close()
    assert columns == {
        "id",
        "user_id",
        "name",
        "key_prefix",
        "key_hash",
        "created_at",
        "expires_at",
        "last_used_at",
        "revoked_at",
    }


def test_initialize_is_idempotent_on_existing_db(db_path):
    from shelfmark.core.user_db import UserDB

    first = UserDB(db_path)
    first.initialize()
    user = first.create_user(username="bob")
    first.create_api_key(user["id"], "laptop", "smk_abcdefgh", "hash-1", None)

    second = UserDB(db_path)
    second.initialize()
    assert len(second.list_api_keys(user["id"])) == 1


def test_create_and_list_api_key(user_db, alice):
    row = user_db.create_api_key(alice["id"], "laptop", "smk_abcdefgh", "hash-1", None)

    assert row["id"] > 0
    assert row["user_id"] == alice["id"]
    assert row["name"] == "laptop"
    assert row["key_prefix"] == "smk_abcdefgh"
    assert row["key_hash"] == "hash-1"
    assert row["created_at"]
    assert row["expires_at"] is None
    assert row["last_used_at"] is None
    assert row["revoked_at"] is None

    listed = user_db.list_api_keys(alice["id"])
    assert [k["id"] for k in listed] == [row["id"]]


def test_list_orders_newest_first(user_db, alice):
    first = user_db.create_api_key(alice["id"], "one", "smk_aaaaaaaa", "hash-a", None)
    second = user_db.create_api_key(alice["id"], "two", "smk_bbbbbbbb", "hash-b", None)

    assert [k["id"] for k in user_db.list_api_keys(alice["id"])] == [second["id"], first["id"]]


def test_get_api_keys_by_prefix_returns_all_candidates(user_db, alice):
    bob = user_db.create_user(username="bob")
    user_db.create_api_key(alice["id"], "a", "smk_sameprfx", "hash-a", None)
    user_db.create_api_key(bob["id"], "b", "smk_sameprfx", "hash-b", None)
    user_db.create_api_key(bob["id"], "c", "smk_otherpfx", "hash-c", None)

    hashes = {row["key_hash"] for row in user_db.get_api_keys_by_prefix("smk_sameprfx")}
    assert hashes == {"hash-a", "hash-b"}


def test_get_api_key_is_scoped_to_user(user_db, alice):
    bob = user_db.create_user(username="bob")
    row = user_db.create_api_key(alice["id"], "a", "smk_aaaaaaaa", "hash-a", None)

    assert user_db.get_api_key(row["id"], alice["id"]) is not None
    assert user_db.get_api_key(row["id"], bob["id"]) is None


def test_revoke_sets_revoked_at_and_is_scoped(user_db, alice):
    bob = user_db.create_user(username="bob")
    row = user_db.create_api_key(alice["id"], "a", "smk_aaaaaaaa", "hash-a", None)

    assert user_db.revoke_api_key(row["id"], bob["id"]) is False
    assert user_db.revoke_api_key(row["id"], alice["id"]) is True
    assert user_db.get_api_key(row["id"], alice["id"])["revoked_at"] is not None
    # Revoking again reports the key still exists for this user.
    assert user_db.revoke_api_key(row["id"], alice["id"]) is True


def test_count_active_excludes_revoked(user_db, alice):
    a = user_db.create_api_key(alice["id"], "a", "smk_aaaaaaaa", "hash-a", None)
    user_db.create_api_key(alice["id"], "b", "smk_bbbbbbbb", "hash-b", None)
    user_db.revoke_api_key(a["id"], alice["id"])

    assert user_db.count_active_api_keys(alice["id"]) == 1


def test_count_active_excludes_expired(user_db, alice):
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    active = user_db.create_api_key(alice["id"], "active", "smk_aaaaaaaa", "hash-a", None)
    revoked = user_db.create_api_key(alice["id"], "revoked", "smk_bbbbbbbb", "hash-b", None)
    user_db.create_api_key(alice["id"], "expired", "smk_cccccccc", "hash-c", past)
    user_db.revoke_api_key(revoked["id"], alice["id"])

    assert user_db.count_active_api_keys(alice["id"]) == 1
    assert user_db.get_api_key(active["id"], alice["id"])["revoked_at"] is None


def test_touch_last_used(user_db, alice):
    row = user_db.create_api_key(alice["id"], "a", "smk_aaaaaaaa", "hash-a", None)
    user_db.touch_api_key_last_used(row["id"])

    assert user_db.get_api_key(row["id"], alice["id"])["last_used_at"] is not None


def test_deleting_user_cascades_to_keys(db_path, user_db, alice):
    user_db.create_api_key(alice["id"], "a", "smk_aaaaaaaa", "hash-a", None)
    user_db.delete_user(alice["id"])

    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM api_keys").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_key_hash_is_unique(user_db, alice):
    user_db.create_api_key(alice["id"], "a", "smk_aaaaaaaa", "same-hash", None)
    with pytest.raises(sqlite3.IntegrityError):
        user_db.create_api_key(alice["id"], "b", "smk_bbbbbbbb", "same-hash", None)


def test_create_api_key_respects_max_active(user_db, alice):
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")

    first = user_db.create_api_key(alice["id"], "one", "smk_aaaaaaaa", "hash-a", None, max_active=2)
    second = user_db.create_api_key(
        alice["id"], "two", "smk_bbbbbbbb", "hash-b", None, max_active=2
    )

    with pytest.raises(ApiKeyLimitReachedError):
        user_db.create_api_key(alice["id"], "three", "smk_cccccccc", "hash-c", None, max_active=2)

    # Revoking one of the two active keys frees a slot for a new one.
    user_db.revoke_api_key(first["id"], alice["id"])
    fourth = user_db.create_api_key(
        alice["id"], "four", "smk_dddddddd", "hash-d", None, max_active=2
    )
    assert user_db.count_active_api_keys(alice["id"]) == 2

    # An already-expired key does not count toward the cap, so it neither
    # gets blocked by a full cap once a slot is free, nor blocks the next one.
    user_db.revoke_api_key(second["id"], alice["id"])
    user_db.create_api_key(alice["id"], "five", "smk_eeeeeeee", "hash-e", past, max_active=2)
    assert user_db.count_active_api_keys(alice["id"]) == 1

    sixth = user_db.create_api_key(alice["id"], "six", "smk_ffffffff", "hash-f", None, max_active=2)
    assert user_db.count_active_api_keys(alice["id"]) == 2
    assert {
        row["id"] for row in user_db.list_api_keys(alice["id"]) if row["revoked_at"] is None
    } & {
        fourth["id"],
        sixth["id"],
    } == {fourth["id"], sixth["id"]}


def test_create_api_key_max_active_is_atomic_under_threads(user_db, alice):
    successes: list[dict] = []
    failures: list[BaseException] = []
    lock = threading.Lock()

    def _create(index: int) -> None:
        try:
            row = user_db.create_api_key(
                alice["id"],
                f"key-{index}",
                f"smk_{index:08d}",
                f"hash-{index}",
                None,
                max_active=3,
            )
        except ApiKeyLimitReachedError as exc:
            with lock:
                failures.append(exc)
        else:
            with lock:
                successes.append(row)

    threads = [threading.Thread(target=_create, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 3
    assert len(failures) == 5
    assert user_db.count_active_api_keys(alice["id"]) == 3

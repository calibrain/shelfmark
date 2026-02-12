"""Tests for CWA user linking/provisioning helpers."""

import os
import tempfile

import pytest

from shelfmark.core.cwa_user_sync import upsert_cwa_user
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

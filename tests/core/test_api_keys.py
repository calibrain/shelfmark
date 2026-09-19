"""Tests for the API key service: generation, hashing, parsing and authentication."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from shelfmark.core import api_keys


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
    return user_db.create_user(username="alice", auth_source="builtin")


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: True)
    api_keys.reset_last_used_throttle()


def _issue(user_db, user, name="test", expires_at=None):
    raw = api_keys.generate_key()
    row = user_db.create_api_key(
        user["id"], name, api_keys.key_prefix(raw), api_keys.hash_key(raw), expires_at
    )
    return raw, row


class TestKeyFormat:
    def test_generate_key_has_prefix_and_entropy(self):
        key = api_keys.generate_key()
        assert key.startswith("smk_")
        assert len(key) >= 4 + 43
        assert api_keys.generate_key() != key

    def test_key_prefix_is_twelve_chars(self):
        assert api_keys.key_prefix("smk_abcdefghijklmnop") == "smk_abcdefgh"

    def test_hash_key_is_sha256_hex(self):
        assert len(api_keys.hash_key("smk_x")) == 64
        assert api_keys.hash_key("smk_x") == api_keys.hash_key("smk_x")
        assert api_keys.hash_key("smk_x") != api_keys.hash_key("smk_y")


class TestExtractApiKey:
    def test_bearer_header(self):
        assert api_keys.extract_api_key("Bearer smk_abc", None) == "smk_abc"

    def test_bearer_scheme_is_case_insensitive(self):
        assert api_keys.extract_api_key("bearer smk_abc", None) == "smk_abc"

    def test_non_bearer_scheme_ignored(self):
        assert api_keys.extract_api_key("Basic dXNlcjpwYXNz", None) is None

    def test_x_api_key_header(self):
        assert api_keys.extract_api_key(None, " smk_abc ") == "smk_abc"

    def test_bearer_wins_over_x_api_key(self):
        assert api_keys.extract_api_key("Bearer smk_a", "smk_b") == "smk_a"

    def test_no_headers(self):
        assert api_keys.extract_api_key(None, None) is None
        assert api_keys.extract_api_key("Bearer ", "") is None


class TestParseCreateRequest:
    def test_valid_without_expiry(self):
        assert api_keys.parse_create_request({"name": " laptop "}) == ("laptop", None)

    def test_valid_with_expiry_returns_sqlite_timestamp(self):
        name, expires_at = api_keys.parse_create_request({"name": "x", "expires_in_days": 30})
        assert name == "x"
        assert expires_at is not None
        parsed = datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        delta = parsed - datetime.now(UTC)
        assert timedelta(days=29, hours=23) < delta <= timedelta(days=30)

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            [],
            {},
            {"name": ""},
            {"name": "   "},
            {"name": "x" * 65},
            {"name": "x", "expires_in_days": 0},
            {"name": "x", "expires_in_days": 3651},
            {"name": "x", "expires_in_days": "30"},
            {"name": "x", "expires_in_days": True},
            {"name": "x", "expires_in_days": 1.5},
        ],
    )
    def test_invalid_payloads_raise(self, payload):
        with pytest.raises(ValueError):
            api_keys.parse_create_request(payload)


class TestSerialize:
    def test_serialize_omits_hash(self, user_db, alice):
        _raw, row = _issue(user_db, alice, name="laptop")
        public = api_keys.serialize_api_key(row)
        assert set(public) == {
            "id",
            "name",
            "key_prefix",
            "created_at",
            "expires_at",
            "last_used_at",
            "revoked_at",
        }
        assert public["name"] == "laptop"


class TestAuthenticate:
    def test_valid_key_returns_user_and_key_id(self, user_db, alice):
        raw, row = _issue(user_db, alice)
        result = api_keys.authenticate(user_db, raw, "builtin")
        assert result is not None
        assert result.user["id"] == alice["id"]
        assert result.key_id == row["id"]

    def test_unknown_key(self, user_db, alice):
        _issue(user_db, alice)
        assert api_keys.authenticate(user_db, "smk_" + "z" * 43, "builtin") is None

    def test_wrong_prefix_format(self, user_db, alice):
        assert api_keys.authenticate(user_db, "notakey", "builtin") is None

    def test_same_prefix_different_key(self, user_db, alice):
        raw, _row = _issue(user_db, alice)
        forged = raw[:12] + "A" * (len(raw) - 12)
        assert api_keys.authenticate(user_db, forged, "builtin") is None

    def test_revoked_key(self, user_db, alice):
        raw, row = _issue(user_db, alice)
        user_db.revoke_api_key(row["id"], alice["id"])
        assert api_keys.authenticate(user_db, raw, "builtin") is None

    def test_expired_key(self, user_db, alice):
        past = (datetime.now(UTC) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        raw, _row = _issue(user_db, alice, expires_at=past)
        assert api_keys.authenticate(user_db, raw, "builtin") is None

    def test_future_expiry_still_valid(self, user_db, alice):
        future = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        raw, _row = _issue(user_db, alice, expires_at=future)
        assert api_keys.authenticate(user_db, raw, "builtin") is not None

    def test_disabled_globally(self, user_db, alice, monkeypatch):
        raw, _row = _issue(user_db, alice)
        monkeypatch.setattr(api_keys, "is_api_keys_enabled", lambda: False)
        assert api_keys.authenticate(user_db, raw, "builtin") is None

    def test_user_inactive_for_auth_mode(self, user_db, alice):
        # A builtin user cannot authenticate when the instance runs proxy auth.
        raw, _row = _issue(user_db, alice)
        assert api_keys.authenticate(user_db, raw, "proxy") is None

    def test_deleted_user(self, user_db, alice):
        raw, _row = _issue(user_db, alice)
        user_db.delete_user(alice["id"])
        assert api_keys.authenticate(user_db, raw, "builtin") is None

    def test_last_used_written_once_per_interval(self, user_db, alice):
        raw, row = _issue(user_db, alice)
        with patch.object(user_db, "touch_api_key_last_used") as touch:
            api_keys.authenticate(user_db, raw, "builtin")
            api_keys.authenticate(user_db, raw, "builtin")
        assert touch.call_count == 1
        touch.assert_called_with(row["id"])

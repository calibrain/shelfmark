"""A session cookie stops granting what users.db no longer allows (GHSA-hr9m-646v-8g6w,
GHSA-9gqw-mmp2-pjm8).

Sign-in copies the user and their admin flag into the signed cookie. These tests sign in
through the real login route, change the user from a second admin's session, and replay
the first cookie: a demotion, a deletion or a password change must apply to it at once.
"""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from contextlib import ExitStack
from unittest.mock import patch

import pytest
from werkzeug.security import generate_password_hash

from shelfmark.core.session_auth import SESSION_NONCE_KEY, revalidate_session
from shelfmark.core.user_db import UserDB
from tests.core.sessions import forge_session

PASSWORD = "fake-password-1"


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def auth_mode(main_module):
    """Run the app in builtin auth mode; set ``.mode`` to switch it mid-test."""

    class _Mode:
        mode = "builtin"

    current = _Mode()
    with ExitStack() as stack:
        for target in (
            "shelfmark.core.admin_routes.load_active_auth_mode",
            "shelfmark.core.self_user_routes.load_active_auth_mode",
        ):
            stack.enter_context(patch(target, side_effect=lambda: current.mode))
        stack.enter_context(
            patch.object(main_module, "get_auth_mode", side_effect=lambda: current.mode)
        )
        main_module.failed_login_attempts.clear()
        yield current
        main_module.failed_login_attempts.clear()


def _user(main_module, *, prefix: str, role: str = "user") -> dict:
    return main_module.user_db.create_user(
        username=f"{prefix}-{uuid.uuid4().hex[:8]}",
        password_hash=generate_password_hash(PASSWORD),
        role=role,
    )


def _signed_in(main_module, user: dict, password: str = PASSWORD):
    client = main_module.app.test_client()
    resp = client.post("/api/auth/login", json={"username": user["username"], "password": password})
    assert resp.status_code == 200, resp.get_json()
    return client


def _check(client) -> dict:
    return client.get("/api/auth/check").get_json()


class TestDemotedAdmin:
    def test_old_session_loses_admin_access_at_once(self, main_module, auth_mode):
        admin_a = _user(main_module, prefix="admin-a", role="admin")
        admin_b = _user(main_module, prefix="admin-b", role="admin")
        a = _signed_in(main_module, admin_a)
        b = _signed_in(main_module, admin_b)
        assert a.get("/api/admin/users").status_code == 200

        resp = b.put(f"/api/admin/users/{admin_a['id']}", json={"role": "user"})
        assert resp.status_code == 200

        assert a.get("/api/admin/users").status_code == 403
        assert _check(a)["is_admin"] is False
        assert _check(a)["authenticated"] is True

    def test_old_session_cannot_restore_its_role_or_mint_an_admin(self, main_module, auth_mode):
        admin_a = _user(main_module, prefix="admin-a", role="admin")
        admin_b = _user(main_module, prefix="admin-b", role="admin")
        a = _signed_in(main_module, admin_a)
        b = _signed_in(main_module, admin_b)
        b.put(f"/api/admin/users/{admin_a['id']}", json={"role": "user"})

        restore = a.put(f"/api/admin/users/{admin_a['id']}", json={"role": "admin"})
        backdoor = a.post(
            "/api/admin/users",
            json={"username": f"backdoor-{uuid.uuid4().hex[:8]}", "password": PASSWORD},
        )

        assert restore.status_code == 403
        assert backdoor.status_code == 403
        assert main_module.user_db.get_user(user_id=admin_a["id"])["role"] == "user"

    def test_admin_only_settings_close_too(self, main_module, auth_mode):
        admin_a = _user(main_module, prefix="admin-a", role="admin")
        admin_b = _user(main_module, prefix="admin-b", role="admin")
        a = _signed_in(main_module, admin_a)
        b = _signed_in(main_module, admin_b)
        b.put(f"/api/admin/users/{admin_a['id']}", json={"role": "user"})

        assert a.get("/api/settings/security").status_code == 403

    def test_a_promotion_applies_without_signing_in_again(self, main_module, auth_mode):
        user = _user(main_module, prefix="reader")
        admin = _user(main_module, prefix="admin", role="admin")
        u = _signed_in(main_module, user)
        assert u.get("/api/admin/users").status_code == 403

        _signed_in(main_module, admin).put(f"/api/admin/users/{user['id']}", json={"role": "admin"})

        assert u.get("/api/admin/users").status_code == 200


class TestDeletedUser:
    def test_old_session_is_signed_out(self, main_module, auth_mode):
        victim = _user(main_module, prefix="victim")
        admin = _user(main_module, prefix="admin", role="admin")
        v = _signed_in(main_module, victim)
        assert _check(v)["authenticated"] is True

        assert (
            _signed_in(main_module, admin).delete(f"/api/admin/users/{victim['id']}").status_code
            == 200
        )

        assert _check(v)["authenticated"] is False
        assert v.get("/api/status").status_code == 401
        with v.session_transaction() as sess:
            assert "user_id" not in sess

    def test_a_deleted_admin_cannot_use_the_admin_api(self, main_module, auth_mode):
        admin_a = _user(main_module, prefix="admin-a", role="admin")
        admin_b = _user(main_module, prefix="admin-b", role="admin")
        a = _signed_in(main_module, admin_a)
        _signed_in(main_module, admin_b).delete(f"/api/admin/users/{admin_a['id']}")

        assert a.get("/api/admin/users").status_code == 401
        assert (
            a.post("/api/admin/users", json={"username": "x", "password": PASSWORD}).status_code
            == 401
        )


class TestPasswordChange:
    def test_an_admin_reset_signs_out_existing_sessions(self, main_module, auth_mode):
        victim = _user(main_module, prefix="victim")
        admin = _user(main_module, prefix="admin", role="admin")
        stolen = _signed_in(main_module, victim)

        resp = _signed_in(main_module, admin).put(
            f"/api/admin/users/{victim['id']}", json={"password": "new-fake-password-2"}
        )
        assert resp.status_code == 200

        assert _check(stolen)["authenticated"] is False
        fresh = _signed_in(main_module, victim, password="new-fake-password-2")
        assert _check(fresh)["authenticated"] is True

    def test_changing_your_own_password_keeps_this_session_and_ends_the_others(
        self, main_module, auth_mode
    ):
        user = _user(main_module, prefix="reader")
        here = _signed_in(main_module, user)
        elsewhere = _signed_in(main_module, user)

        resp = here.put(
            "/api/users/me",
            json={"password": "new-fake-password-2", "current_password": PASSWORD},
        )
        assert resp.status_code == 200

        assert _check(here)["authenticated"] is True
        assert _check(elsewhere)["authenticated"] is False

    def test_an_admin_setting_their_own_password_stays_signed_in(self, main_module, auth_mode):
        admin = _user(main_module, prefix="admin", role="admin")
        other_admin = _user(main_module, prefix="admin-2", role="admin")  # keeps a local admin
        here = _signed_in(main_module, admin)
        elsewhere = _signed_in(main_module, admin)
        assert other_admin

        resp = here.put(f"/api/admin/users/{admin['id']}", json={"password": "new-fake-password-2"})
        assert resp.status_code == 200

        assert here.get("/api/admin/users").status_code == 200
        assert _check(elsewhere)["authenticated"] is False

    def test_user_payloads_never_carry_the_session_nonce(self, main_module, auth_mode):
        admin = _user(main_module, prefix="admin", role="admin")
        client = _signed_in(main_module, admin)

        listed = client.get("/api/admin/users").get_json()
        one = client.get(f"/api/admin/users/{admin['id']}").get_json()
        me = client.get("/api/users/me/edit-context").get_json()

        assert all(SESSION_NONCE_KEY not in row for row in listed)
        assert SESSION_NONCE_KEY not in one
        assert SESSION_NONCE_KEY not in me["user"]


class TestStaleCookies:
    def test_a_cookie_from_before_session_nonces_is_signed_out(self, main_module, auth_mode):
        user = _user(main_module, prefix="reader")
        client = main_module.app.test_client()
        forge_session(client, user_id=user["username"], db_user_id=user["id"], is_admin=False)

        assert _check(client)["authenticated"] is False

    def test_a_session_without_a_linked_user_is_signed_out(self, main_module, auth_mode):
        client = main_module.app.test_client()
        forge_session(client, user_id="someone", is_admin=True)

        assert client.get("/api/status").status_code == 401

    def test_a_builtin_session_ends_when_auth_moves_to_another_source(self, main_module, auth_mode):
        user = _user(main_module, prefix="reader")
        client = _signed_in(main_module, user)

        auth_mode.mode = "cwa"

        assert _check(client)["authenticated"] is False

    def test_a_database_error_fails_closed(self, main_module, auth_mode):
        user = _user(main_module, prefix="reader")
        client = _signed_in(main_module, user)

        with patch.object(
            main_module.user_db, "get_user_cached", side_effect=sqlite3.OperationalError("locked")
        ):
            resp = client.get("/api/status")

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Authentication error"}


class TestSocketIO:
    @staticmethod
    def _connect(main_module, client):
        socket = main_module.socketio.test_client(main_module.app, flask_test_client=client)
        events = [(item["name"], item["args"][0]) for item in socket.get_received()]
        return socket, events

    def test_a_demoted_admins_cookie_joins_only_their_own_room(self, main_module, auth_mode):
        admin_a = _user(main_module, prefix="admin-a", role="admin")
        admin_b = _user(main_module, prefix="admin-b", role="admin")
        a = _signed_in(main_module, admin_a)
        _signed_in(main_module, admin_b).put(
            f"/api/admin/users/{admin_a['id']}", json={"role": "user"}
        )

        with patch.object(main_module.ws_manager, "join_user_room") as join:
            socket, _ = self._connect(main_module, a)
            socket.disconnect()

        assert join.call_args.kwargs == {"is_admin": False, "db_user_id": admin_a["id"]}

    def test_a_deleted_users_cookie_gets_no_room_and_no_status(self, main_module, auth_mode):
        victim = _user(main_module, prefix="victim")
        admin = _user(main_module, prefix="admin", role="admin")
        v = _signed_in(main_module, victim)
        _signed_in(main_module, admin).delete(f"/api/admin/users/{victim['id']}")

        with patch.object(main_module.ws_manager, "join_user_room") as join:
            socket, events = self._connect(main_module, v)
            socket.disconnect()

        assert join.call_args.kwargs == {"is_admin": False, "db_user_id": None}
        assert events == [("status_update", {})]

    def test_a_demotion_disconnects_the_users_open_sockets(self, main_module, auth_mode):
        admin_a = _user(main_module, prefix="admin-a", role="admin")
        admin_b = _user(main_module, prefix="admin-b", role="admin")
        a = _signed_in(main_module, admin_a)
        b = _signed_in(main_module, admin_b)
        socket, _ = self._connect(main_module, a)
        assert socket.is_connected()

        b.put(f"/api/admin/users/{admin_a['id']}", json={"role": "user"})

        assert not socket.is_connected()

    def test_a_deletion_disconnects_the_users_open_sockets(self, main_module, auth_mode):
        victim = _user(main_module, prefix="victim")
        admin = _user(main_module, prefix="admin", role="admin")
        socket, _ = self._connect(main_module, _signed_in(main_module, victim))
        assert socket.is_connected()

        _signed_in(main_module, admin).delete(f"/api/admin/users/{victim['id']}")

        assert not socket.is_connected()


class TestRevalidateSession:
    @pytest.fixture
    def user_db(self, tmp_path):
        db = UserDB(str(tmp_path / "users.db"))
        db.initialize()
        return db

    def test_proxy_and_no_auth_sessions_are_left_alone(self, user_db):
        for mode in ("none", "proxy"):
            sess = {"user_id": "ghost", "is_admin": True}
            assert revalidate_session(sess, user_db=user_db, auth_mode=mode) is True
            assert sess == {"user_id": "ghost", "is_admin": True}

    def test_cwa_without_a_user_database_keeps_its_session(self):
        sess = {"user_id": "cwa-user", "is_admin": False}
        assert revalidate_session(sess, user_db=None, auth_mode="cwa") is True

    def test_builtin_without_a_user_database_signs_out(self):
        sess = {"user_id": "someone", "db_user_id": 1, "is_admin": True}
        assert revalidate_session(sess, user_db=None, auth_mode="builtin") is False
        assert sess == {}

    def test_an_unrecognized_auth_mode_signs_out(self, user_db):
        user = user_db.create_user(username="alice", role="admin")
        sess = {
            "user_id": "alice",
            "db_user_id": user["id"],
            "is_admin": True,
            SESSION_NONCE_KEY: user["session_nonce"],
        }
        assert revalidate_session(sess, user_db=user_db, auth_mode="unavailable") is False

    def test_a_rebuilt_database_does_not_honor_old_cookies(self, tmp_path):
        """A fresh users.db hands out id 1 again; the old cookie's nonce cannot match it."""
        old_db = UserDB(str(tmp_path / "old.db"))
        old_db.initialize()
        old_user = old_db.create_user(username="alice", role="user")
        new_db = UserDB(str(tmp_path / "new.db"))
        new_db.initialize()
        new_admin = new_db.create_user(username="root", role="admin")
        assert new_admin["id"] == old_user["id"]

        sess = {
            "user_id": "alice",
            "db_user_id": old_user["id"],
            "is_admin": False,
            SESSION_NONCE_KEY: old_user["session_nonce"],
        }
        assert revalidate_session(sess, user_db=new_db, auth_mode="builtin") is False


class TestSessionNonceColumn:
    def test_every_user_gets_a_distinct_nonce(self, tmp_path):
        db = UserDB(str(tmp_path / "users.db"))
        db.initialize()
        a = db.create_user(username="a")
        b = db.create_user(username="b")
        assert a["session_nonce"]
        assert b["session_nonce"]
        assert a["session_nonce"] != b["session_nonce"]

    def test_a_password_change_rotates_it_and_other_edits_do_not(self, tmp_path):
        db = UserDB(str(tmp_path / "users.db"))
        db.initialize()
        user = db.create_user(username="a", password_hash="old")
        nonce = user["session_nonce"]

        db.update_user(user["id"], role="admin", email="a@example.invalid", display_name="A")
        assert db.get_user(user_id=user["id"])["session_nonce"] == nonce

        db.update_user(user["id"], password_hash="new")
        assert db.get_user(user_id=user["id"])["session_nonce"] != nonce

    def test_an_existing_database_is_migrated_and_backfilled(self, tmp_path):
        path = tmp_path / "users.db"
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,"
            " email TEXT, display_name TEXT, password_hash TEXT, oidc_subject TEXT UNIQUE,"
            " role TEXT NOT NULL DEFAULT 'user', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("INSERT INTO users (username, role) VALUES ('legacy-a', 'admin')")
        conn.execute("INSERT INTO users (username, role) VALUES ('legacy-b', 'user')")
        conn.commit()
        conn.close()

        db = UserDB(str(path))
        db.initialize()

        nonces = [row["session_nonce"] for row in db.list_users()]
        assert all(nonces)
        assert len(set(nonces)) == 2

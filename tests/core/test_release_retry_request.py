"""Retry on a failed release of a reopened request goes through that request (#1681).

/api/releases/download takes the request's id, and the release is queued through request
fulfilment, so the download is linked to the request and closes it when the book arrives.
An admin may retry any pending request, its owner only their own and only when their policy
lets them download the release directly.
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest

from shelfmark.core.request_policy import PolicyMode
from shelfmark.download import orchestrator


@pytest.fixture(scope="module")
def main_module():
    """The app, without starting the download coordinator."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    """A test client for the app."""
    return main_module.app.test_client()


@pytest.fixture
def queued(monkeypatch):
    """Record what reaches the download queue instead of queueing it."""
    calls: list[dict] = []

    def fake_queue_release(release_data, priority, *, user_id=None, username=None):
        """Note the release and who it was queued for, and report it queued."""
        calls.append({"release": dict(release_data), "user_id": user_id, "username": username})
        return True, None

    monkeypatch.setattr(orchestrator, "queue_release", fake_queue_release)
    return calls


def _user(main_module, *, is_admin: bool = False) -> dict:
    """A new user with a unique name."""
    return main_module.user_db.create_user(
        username=f"reader-{uuid.uuid4().hex[:8]}", role="admin" if is_admin else "user"
    )


def _login(client, user: dict) -> None:
    """Log the client in as the user."""
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["is_admin"] = user["role"] == "admin"
        sess["db_user_id"] = user["id"]


def _reopened_request(main_module, owner: dict, *, status: str = "pending") -> dict:
    """A request for a unique book, as it stands after a failed download reopened it."""
    return main_module.user_db.create_request(
        user_id=owner["id"],
        content_type="ebook",
        request_level="book",
        policy_mode="request_book",
        book_data={"title": f"Book {uuid.uuid4().hex[:8]}", "author": "An Author"},
        status=status,
    )


def _policy(default_ebook: str) -> dict:
    """Request policy settings with this default for ebooks."""
    return {
        "REQUESTS_ENABLED": True,
        "REQUEST_POLICY_DEFAULT_EBOOK": default_ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "download",
        "MAX_PENDING_REQUESTS_PER_USER": 20,
        "REQUESTS_ALLOW_NOTES": True,
        "REQUEST_POLICY_RULES": [],
    }


def _retry(client, main_module, request_id, *, policy="download", auth_mode="builtin", **extra):
    """Press Retry on the failed release of a request."""
    body = {
        "source": "direct_download",
        "source_id": "md5-failed",
        "title": "The Book",
        "content_type": "ebook",
        "request_id": request_id,
        **extra,
    }
    with (
        patch.object(main_module, "get_auth_mode", return_value=auth_mode),
        patch.object(
            main_module, "load_users_request_policy_settings", return_value=_policy(policy)
        ),
    ):
        return client.post("/api/releases/download", json=body)


def test_an_admin_retry_fulfils_the_users_request(client, main_module, queued):
    """The download is queued for the owner, linked to the request, which is fulfilled.

    Request views hear about it the way they hear about any other request change.
    """
    owner, admin = _user(main_module), _user(main_module, is_admin=True)
    reopened = _reopened_request(main_module, owner)
    _login(client, admin)
    emitted: list[list[dict]] = []

    with patch.object(main_module, "_emit_request_update_events", emitted.append):
        response = _retry(client, main_module, reopened["id"])

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["request_id"] == reopened["id"]
    assert [[row["id"] for row in rows] for rows in emitted] == [[reopened["id"]]]
    [call] = queued
    assert call["release"]["_request_id"] == reopened["id"]
    assert call["release"]["source_id"] == "md5-failed"
    assert "request_id" not in call["release"]
    assert call["user_id"] == owner["id"]
    updated = main_module.user_db.get_request(reopened["id"])
    assert updated["status"] == "fulfilled"
    assert updated["reviewed_by"] == admin["id"]


def test_the_owner_who_may_download_directly_retries_their_own_request(client, main_module, queued):
    """A Hardcover-synced request of a user with direct downloads closes on their Retry."""
    owner = _user(main_module)
    reopened = _reopened_request(main_module, owner)
    _login(client, owner)

    response = _retry(client, main_module, reopened["id"], policy=PolicyMode.DOWNLOAD.value)

    assert response.status_code == 200, response.get_json()
    assert queued[0]["release"]["_request_id"] == reopened["id"]
    updated = main_module.user_db.get_request(reopened["id"])
    assert updated["status"] == "fulfilled"
    assert updated["reviewed_by"] == owner["id"]


def test_an_owner_who_may_only_request_is_still_refused(client, main_module, queued):
    """The policy check comes first, so request_id never lets a user skip it."""
    owner = _user(main_module)
    reopened = _reopened_request(main_module, owner)
    _login(client, owner)

    response = _retry(client, main_module, reopened["id"], policy="request_release")

    assert response.status_code == 403
    assert queued == []
    assert main_module.user_db.get_request(reopened["id"])["status"] == "pending"


def test_another_user_cannot_retry_someone_elses_request(client, main_module, queued):
    """A user who may download still can't fulfil a request that isn't theirs."""
    owner, other = _user(main_module), _user(main_module)
    reopened = _reopened_request(main_module, owner)
    _login(client, other)

    response = _retry(client, main_module, reopened["id"])

    assert response.status_code == 403
    assert queued == []
    assert main_module.user_db.get_request(reopened["id"])["status"] == "pending"


def test_a_request_that_is_no_longer_pending_is_reported_stale(client, main_module, queued):
    """A request closed since the release failed answers stale_transition, queueing nothing.

    The answer is a fixed message, never the service's error text.
    """
    owner, admin = _user(main_module), _user(main_module, is_admin=True)
    closed = _reopened_request(main_module, owner, status="cancelled")
    _login(client, admin)

    response = _retry(client, main_module, closed["id"])

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "This request is no longer pending",
        "code": "stale_transition",
    }
    assert queued == []


def test_request_id_and_on_behalf_of_together_are_refused(client, main_module, queued):
    """The request already names who the download is for."""
    owner, admin = _user(main_module), _user(main_module, is_admin=True)
    reopened = _reopened_request(main_module, owner)
    _login(client, admin)

    response = _retry(client, main_module, reopened["id"], on_behalf_of_user_id=admin["id"])

    assert response.status_code == 400
    assert queued == []


def test_without_login_the_admin_that_owns_synced_requests_fulfils_it(client, main_module, queued):
    """With no session user, the retry is recorded against the first admin, as auto-download."""
    owner = _user(main_module)
    reopened = _reopened_request(main_module, owner)
    first_admin = main_module.user_db.get_first_admin()
    if first_admin is None:
        first_admin = _user(main_module, is_admin=True)
    with client.session_transaction() as sess:
        sess.clear()

    response = _retry(client, main_module, reopened["id"], auth_mode="none")

    assert response.status_code == 200, response.get_json()
    updated = main_module.user_db.get_request(reopened["id"])
    assert updated["status"] == "fulfilled"
    assert updated["reviewed_by"] == first_admin["id"]


def test_a_large_pack_plan_is_queued_but_not_stored_on_the_request(client, main_module, queued):
    """A plan too big for a request's release data still reaches the download queue."""
    owner, admin = _user(main_module), _user(main_module, is_admin=True)
    reopened = _reopened_request(main_module, owner)
    _login(client, admin)
    book_plan = [{"file": f"{n:03}-" + "x" * 80, "title": f"Book {n}"} for n in range(200)]
    ignored_files = [f"extra-{n:03}-" + "y" * 80 for n in range(200)]

    response = _retry(
        client, main_module, reopened["id"], book_plan=book_plan, ignored_files=ignored_files
    )

    assert response.status_code == 200, response.get_json()
    [call] = queued
    assert call["release"]["book_plan"] == book_plan
    assert call["release"]["ignored_files"] == ignored_files
    updated = main_module.user_db.get_request(reopened["id"])
    assert updated["status"] == "fulfilled"
    assert "book_plan" not in (updated["release_data"] or {})
    assert "ignored_files" not in (updated["release_data"] or {})

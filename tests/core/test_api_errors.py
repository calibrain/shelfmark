"""The helper that turns an exception into an API response masks what it quotes (#1688)."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.api_errors import error_response, exception_response
from shelfmark.core.release_search import search_source_releases
from shelfmark.metadata_providers import BookMetadata
from tests.core.sessions import sign_in

_SECRET_URL = "http://tracker.example/dl?apikey=hunter2&id=7"


def _answer(helper, *args, **kwargs):
    """The JSON body and status a helper answers with, called inside an app context."""
    with Flask(__name__).app_context():
        response, status = helper(*args, **kwargs)
        return response.get_json(), status


def test_error_response_keeps_the_message_and_masks_a_credential():
    """A message the code wrote is returned as it is, minus anything secret in it."""
    body, status = _answer(error_response, f"Could not fetch {_SECRET_URL}", 503)

    assert status == 503
    assert "hunter2" not in body["error"]
    assert body["error"].startswith("Could not fetch http://tracker.example/dl?apikey=")
    assert "id=7" in body["error"]


def test_error_response_leaves_a_plain_message_alone():
    """A message with nothing secret in it is unchanged."""
    assert _answer(error_response, "Unknown release source: foo", 400) == (
        {"error": "Unknown release source: foo"},
        400,
    )


def test_exception_response_hides_the_text_unless_asked():
    """The client gets the fixed message, not what the exception says."""
    body, status = _answer(
        exception_response, OSError(f"/config/x {_SECRET_URL}"), "Could not save"
    )

    assert (body, status) == ({"error": "Could not save"}, 500)


def test_exception_response_with_detail_masks_what_it_shows():
    """For an admin-only route the detail is shown, with credentials masked."""
    body, status = _answer(
        exception_response,
        OSError(f"cannot open /config/x via {_SECRET_URL}"),
        "Could not save",
        show_detail=True,
    )

    assert status == 500
    assert body["error"].startswith("cannot open /config/x via http://tracker.example/dl")
    assert "hunter2" not in body["error"]


@pytest.fixture(scope="module")
def main_module():
    """The app, without starting the download coordinator."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


def test_a_settings_error_toast_keeps_its_detail_but_not_a_credential(main_module):
    """An admin still reads what failed, with a key in it masked."""
    client = main_module.app.test_client()
    sign_in(client, user_id="someone", is_admin=True)
    with (
        patch.object(main_module, "get_auth_mode", return_value="builtin"),
        patch(
            "shelfmark.core.settings_registry.serialize_all_settings",
            side_effect=OSError(f"cannot read {_SECRET_URL}"),
        ),
    ):
        response = client.get("/api/settings")

    assert response.status_code == 500
    assert response.json["error"].startswith("cannot read http://tracker.example/dl")
    assert "hunter2" not in response.json["error"]


def test_a_source_error_in_the_release_search_masks_a_credential(monkeypatch):
    """The detail an admin gets for an unexpected source error has its secrets masked."""
    import shelfmark.release_sources as sources

    class _Failing:
        """A source whose search fails with a message that quotes a credential."""

        def search(self, *args, **kwargs):
            """Fail the way a client library does."""
            raise OSError(f"Cannot reach {_SECRET_URL}")

    monkeypatch.setattr(sources, "get_source", lambda _name: _Failing())

    _, releases, error = search_source_releases(
        "direct_download",
        BookMetadata(
            provider="hardcover", provider_id="1", title="Dune", authors=["Frank Herbert"]
        ),
    )

    assert releases == []
    assert error is not None
    assert "hunter2" not in error
    assert error.startswith("direct_download: Cannot reach http://tracker.example/dl")

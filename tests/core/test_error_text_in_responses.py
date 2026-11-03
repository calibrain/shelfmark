"""An unexpected failure in a route returns a fixed message, never the exception text.

The exception can carry file paths, URLs with tokens or driver messages. The full error
stays in the log with its trace; the caller gets one sentence per route. Messages that
were written for the user (a provider's "API key is invalid", a source being down) are
a different thing and are covered by the tests of those routes.
"""

from __future__ import annotations

import base64
import importlib
from unittest.mock import patch

import pytest

_LEAK = "/srv/shelfmark/secret/path token=abc123"


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _boom(*_args, **_kwargs):
    raise OSError(_LEAK)


def _admin_session(client) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = True
        sess["db_user_id"] = 7


class _Source:
    def get_record(self, record_id):
        raise OSError(_LEAK)


def _cover_url() -> str:
    return "/api/covers/c1?url=" + base64.urlsafe_b64encode(b"https://example.com/c.jpg").decode()


def _patches(main_module, name: str):
    """The patches that make route `name` fail with an OSError carrying _LEAK."""
    backend = main_module.backend
    table = {
        "cancel": [patch.object(backend.book_queue, "get_task", side_effect=_boom)],
        "retry": [patch.object(backend.book_queue, "get_task", side_effect=_boom)],
        "priority": [patch.object(backend.book_queue, "get_task", side_effect=_boom)],
        "reorder": [patch.object(backend, "reorder_queue", side_effect=_boom)],
        "order": [patch.object(backend, "get_queue_order", side_effect=_boom)],
        "active": [patch.object(backend, "get_active_downloads", side_effect=_boom)],
        "cover": [
            patch("shelfmark.config.env.is_covers_cache_enabled", return_value=True),
            patch("shelfmark.core.image_cache.get_image_cache", side_effect=_boom),
        ],
        "release_download": [patch.object(backend, "queue_release", side_effect=_boom)],
        "release_search": [
            patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
            patch("shelfmark.metadata_providers.get_provider_kwargs", side_effect=_boom),
            patch(
                "shelfmark.release_sources.list_available_sources",
                return_value=[{"name": "direct_download", "enabled": True}],
            ),
            patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
        ],
        "release_sources": [
            patch("shelfmark.release_sources.list_available_sources", side_effect=_boom)
        ],
        "release_record": [
            patch("shelfmark.release_sources.get_source", return_value=_Source()),
        ],
        "metadata_providers": [patch("shelfmark.core.metadata_routes.app_config.refresh", _boom)],
        "metadata_config": [patch("shelfmark.core.metadata_routes.app_config.refresh", _boom)],
        "metadata_search": [
            patch("shelfmark.metadata_providers.get_configured_provider", side_effect=_boom)
        ],
        "metadata_book": [
            patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
            patch("shelfmark.metadata_providers.get_provider_kwargs", side_effect=_boom),
        ],
    }
    return table[name]


CASES = [
    ("cancel", "delete", "/api/download/t1/cancel", None, "Could not cancel the download"),
    ("retry", "post", "/api/download/t1/retry", {}, "Could not retry the download"),
    ("priority", "put", "/api/queue/t1/priority", {"priority": 1}, "Could not set the priority"),
    (
        "reorder",
        "post",
        "/api/queue/reorder",
        {"book_priorities": {"a": 0}},
        "Could not reorder the queue",
    ),
    ("order", "get", "/api/queue/order", None, "Could not read the queue order"),
    ("active", "get", "/api/downloads/active", None, "Could not read the active downloads"),
    ("cover", "get", _cover_url(), None, "Could not fetch the cover image"),
    (
        "release_download",
        "post",
        "/api/releases/download",
        {"source": "direct_download", "source_id": "a", "title": "Dune"},
        "Could not queue the release",
    ),
    (
        "release_search",
        "get",
        "/api/releases?provider=hardcover&book_id=1",
        None,
        "Could not search for releases",
    ),
    ("release_sources", "get", "/api/release-sources", None, "Could not list the release sources"),
    (
        "release_record",
        "get",
        "/api/release-sources/direct_download/records/r1",
        None,
        "Could not resolve the record",
    ),
    (
        "metadata_providers",
        "get",
        "/api/metadata/providers",
        None,
        "Could not load the metadata providers",
    ),
    (
        "metadata_config",
        "get",
        "/api/metadata/config",
        None,
        "Could not load the metadata configuration",
    ),
    ("metadata_search", "get", "/api/metadata/search?query=dune", None, "Metadata search failed"),
    ("metadata_book", "get", "/api/metadata/book/hardcover/1", None, "Could not load the book"),
]


@pytest.mark.parametrize(
    ("name", "method", "path", "body", "message"), CASES, ids=[c[0] for c in CASES]
)
def test_failure_returns_a_fixed_message(main_module, client, name, method, path, body, message):
    _admin_session(client)
    patches = _patches(main_module, name)
    kwargs = {"json": body} if body is not None else {}

    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        for p in patches:
            p.start()
        try:
            resp = getattr(client, method)(path, **kwargs)
        finally:
            for p in reversed(patches):
                p.stop()

    assert resp.status_code == 500
    assert resp.get_json() == {"error": message}
    assert _LEAK not in resp.get_data(as_text=True)

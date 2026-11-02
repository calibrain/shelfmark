"""Behaviour of the release routes: download, listing, source list and source records.

Pins the parts the policy, budget, direct-provider and title-override tests do not
reach: content-type inference on download, policy refusal on download, the error
statuses of /api/releases, the source list and the source record route.
"""

from __future__ import annotations

import importlib
import uuid
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import BrowseRecord, SourceUnavailableError


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _no_auth(main_module):
    return patch.object(main_module, "get_auth_mode", return_value="none")


def _policy(default_ebook: str = "download", default_audiobook: str = "download") -> dict:
    return {
        "REQUESTS_ENABLED": True,
        "REQUEST_POLICY_DEFAULT_EBOOK": default_ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": default_audiobook,
        "MAX_PENDING_REQUESTS_PER_USER": 20,
        "REQUESTS_ALLOW_NOTES": True,
        "REQUEST_POLICY_RULES": [],
    }


def _login_user(client, main_module, *, is_admin: bool = False) -> dict:
    user = main_module.user_db.create_user(
        username=f"reader-{uuid.uuid4().hex[:8]}", role="admin" if is_admin else "user"
    )
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["is_admin"] = is_admin
        sess["db_user_id"] = user["id"]
    return user


def _download(client, main_module, payload: dict) -> tuple[object, dict]:
    """POST a release to the download route; returns the response and the queued payload."""
    captured: dict = {}

    def fake_queue_release(release_data, priority, user_id=None, username=None):
        captured["release_data"] = release_data
        return True, None

    with (
        _no_auth(main_module),
        patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release),
    ):
        resp = client.post("/api/releases/download", json=payload)
    return resp, captured


class TestDownloadContentType:
    def test_audiobook_torznab_category_marks_the_release_as_audiobook(self, client, main_module):
        payload = {
            "source": "prowlarr",
            "source_id": "a",
            "title": "Some Book",
            "extra": {"categories": [3035]},
        }

        resp, captured = _download(client, main_module, payload)

        assert resp.status_code == 200
        assert captured["release_data"] == {**payload, "content_type": "audiobook"}

    def test_a_category_outside_the_audiobook_range_stays_ebook(self, client, main_module):
        payload = {
            "source": "prowlarr",
            "source_id": "a",
            "title": "Some Book",
            "extra": {"categories": [7020, "not-a-number"]},
        }

        resp, captured = _download(client, main_module, payload)

        assert resp.status_code == 200
        assert captured["release_data"] == payload

    def test_audiobook_format_in_the_formats_list_is_enough(self, client, main_module):
        payload = {
            "source": "prowlarr",
            "source_id": "a",
            "title": "Some Book",
            "extra": {"formats": ["epub", "m4b"]},
        }

        _resp, captured = _download(client, main_module, payload)

        assert captured["release_data"]["content_type"] == "audiobook"

    def test_an_explicit_content_type_is_forwarded_untouched(self, client, main_module):
        payload = {
            "source": "prowlarr",
            "source_id": "a",
            "title": "Some Book m4b",
            "content_type": "ebook",
        }

        resp, captured = _download(client, main_module, payload)

        assert resp.status_code == 200
        assert captured["release_data"] == payload

    def test_a_content_type_inside_extra_counts_as_explicit(self, client, main_module):
        payload = {
            "source": "prowlarr",
            "source_id": "a",
            "title": "Some Book m4b",
            "extra": {"content_type": "ebook"},
        }

        resp, captured = _download(client, main_module, payload)

        assert resp.status_code == 200
        assert captured["release_data"] == payload

    def test_a_source_that_only_serves_audiobooks_decides_the_type(self, client, main_module):
        payload = {"source": "audio_only", "source_id": "a", "title": "Some Book"}

        with patch(
            "shelfmark.core.release_routes.get_source_content_type_capabilities",
            return_value={"audio_only": {"audiobook"}},
        ):
            resp, captured = _download(client, main_module, payload)

        assert resp.status_code == 200
        assert captured["release_data"] == {**payload, "content_type": "audiobook"}


class TestDownloadPolicyAndFailures:
    def _post_with_policy(self, client, main_module, policy: dict):
        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            patch.object(main_module, "load_users_request_policy_settings", return_value=policy),
            patch.object(main_module.backend, "queue_release") as queue_release,
        ):
            resp = client.post(
                "/api/releases/download",
                json={"source": "direct_download", "source_id": "a", "title": "Dune"},
            )
        return resp, queue_release

    def test_request_book_user_is_told_to_request_the_release_instead(self, client, main_module):
        _login_user(client, main_module)

        resp, queue_release = self._post_with_policy(
            client, main_module, _policy(default_ebook="request_book")
        )

        assert resp.status_code == 403
        assert resp.get_json() == {
            "error": "Download not allowed by policy",
            "code": "policy_requires_request",
            "required_mode": "request_release",
        }
        queue_release.assert_not_called()

    def test_blocked_user_gets_policy_blocked(self, client, main_module):
        _login_user(client, main_module)

        resp, queue_release = self._post_with_policy(
            client, main_module, _policy(default_ebook="blocked")
        )

        assert resp.status_code == 403
        assert resp.get_json()["code"] == "policy_blocked"
        assert resp.get_json()["required_mode"] == "blocked"
        queue_release.assert_not_called()

    def test_admin_downloads_regardless_of_policy(self, client, main_module):
        _login_user(client, main_module, is_admin=True)

        with (
            patch.object(main_module, "get_auth_mode", return_value="builtin"),
            patch.object(
                main_module,
                "load_users_request_policy_settings",
                return_value=_policy(default_ebook="blocked"),
            ),
            patch.object(main_module.backend, "queue_release", return_value=(True, None)),
        ):
            resp = client.post(
                "/api/releases/download",
                json={"source": "direct_download", "source_id": "a", "title": "Dune"},
            )

        assert resp.status_code == 200

    def test_a_failed_queue_returns_the_reason_with_500(self, client, main_module):
        with (
            _no_auth(main_module),
            patch.object(
                main_module.backend, "queue_release", return_value=(False, "No free slot")
            ),
        ):
            resp = client.post(
                "/api/releases/download",
                json={"source": "direct_download", "source_id": "a", "title": "Dune"},
            )

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "No free slot"}

    def test_a_failed_queue_without_a_reason_uses_a_default_message(self, client, main_module):
        with (
            _no_auth(main_module),
            patch.object(main_module.backend, "queue_release", return_value=(False, None)),
        ):
            resp = client.post(
                "/api/releases/download",
                json={"source": "direct_download", "source_id": "a", "title": "Dune"},
            )

        assert resp.status_code == 500
        assert resp.get_json() == {"error": "Failed to queue release"}

    def test_an_operational_error_while_queueing_returns_500(self, client, main_module):
        with (
            _no_auth(main_module),
            patch.object(main_module.backend, "queue_release", side_effect=RuntimeError("boom")),
        ):
            resp = client.post(
                "/api/releases/download",
                json={"source": "direct_download", "source_id": "a", "title": "Dune"},
            )

        assert resp.status_code == 500


class _Provider:
    def __init__(self, book: BookMetadata | None) -> None:
        self._book = book

    def get_book(self, book_id: str) -> BookMetadata | None:
        return self._book


class _Source:
    last_search_type = "title"
    total_results = 1

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        return []

    def get_column_config(self):
        from shelfmark.release_sources import _default_column_config

        return _default_column_config()


def _book(**overrides) -> BookMetadata:
    fields = {
        "provider": "hardcover",
        "provider_id": "42",
        "provider_display_name": "Hardcover",
        "title": "Dune",
        "authors": ["Frank Herbert"],
    }
    fields.update(overrides)
    return BookMetadata(**fields)


def _list_releases(client, main_module, query: dict, *, book: BookMetadata | None = None, **extra):
    """GET /api/releases with the provider and one release source stubbed."""
    with (
        _no_auth(main_module),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch("shelfmark.metadata_providers.get_provider", return_value=_Provider(book)),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "direct_download", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=_Source()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        return client.get("/api/releases", query_string=query)


class TestReleaseListing:
    def test_provider_and_book_id_are_required(self, client, main_module):
        resp = _list_releases(client, main_module, {})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Parameters 'provider' and 'book_id' are required"}

    def test_a_source_without_browse_filters_is_not_enough(self, client, main_module):
        resp = _list_releases(client, main_module, {"source": "direct_download"})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Parameters 'provider' and 'book_id' are required"}

    def test_a_source_that_does_not_browse_is_refused(self, client, main_module):
        resp = _list_releases(client, main_module, {"source": "direct_download", "query": "dune"})

        assert resp.status_code == 400
        assert resp.get_json() == {
            "error": "Source does not support browse release search: direct_download"
        }

    def test_an_unregistered_provider_is_refused(self, client, main_module):
        with (
            _no_auth(main_module),
            patch("shelfmark.metadata_providers.is_provider_registered", return_value=False),
            patch(
                "shelfmark.release_sources.list_available_sources",
                return_value=[{"name": "direct_download", "enabled": True}],
            ),
            patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
        ):
            resp = client.get("/api/releases", query_string={"provider": "nope", "book_id": "1"})

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Unknown metadata provider: nope"}

    def test_a_book_the_provider_does_not_know_is_a_404(self, client, main_module):
        resp = _list_releases(client, main_module, {"provider": "hardcover", "book_id": "42"})

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Book not found in metadata provider"}

    def test_a_source_record_that_does_not_exist_is_a_404(self, client, main_module):
        class _EmptySource(_Source):
            def get_record(self, record_id, *, fetch_download_count=True):
                return None

        with (
            _no_auth(main_module),
            patch("shelfmark.release_sources.get_source", return_value=_EmptySource()),
            patch("shelfmark.release_sources.source_results_are_releases", return_value=True),
            patch(
                "shelfmark.release_sources.list_available_sources",
                return_value=[{"name": "direct_download", "enabled": True}],
            ),
        ):
            resp = client.get(
                "/api/releases",
                query_string={"provider": "direct_download", "book_id": "gone"},
            )

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Book not found in release source"}

    def test_the_response_carries_book_sources_and_search_info(self, client, main_module):
        resp = _list_releases(
            client, main_module, {"provider": "hardcover", "book_id": "42"}, book=_book()
        )

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["releases"] == []
        assert body["book"]["title"] == "Dune"
        assert body["sources_searched"] == ["direct_download"]
        assert body["column_config"] is not None
        assert body["search_info"] == {
            "direct_download": {"search_type": "title", "total_results": 1}
        }
        assert "errors" not in body

    def test_library_ownership_is_attached_to_the_book(self, client, main_module):
        with patch("shelfmark.core.library_index.ownership", return_value={"epub": True}):
            resp = _list_releases(
                client, main_module, {"provider": "hardcover", "book_id": "42"}, book=_book()
            )

        assert resp.status_code == 200
        assert resp.get_json()["book"]["library"] == {"epub": True}

    def test_no_library_key_when_the_check_is_off(self, client, main_module):
        with patch("shelfmark.core.library_index.ownership", return_value=None):
            resp = _list_releases(
                client, main_module, {"provider": "hardcover", "book_id": "42"}, book=_book()
            )

        assert "library" not in resp.get_json()["book"]

    def test_every_source_failing_returns_503_without_the_source_prefix(self, client, main_module):
        with patch(
            "shelfmark.core.release_search.search_source_releases",
            return_value=(None, [], "direct_download: Bypass failed"),
        ):
            resp = _list_releases(
                client, main_module, {"provider": "hardcover", "book_id": "42"}, book=_book()
            )

        assert resp.status_code == 503
        assert resp.get_json() == {"error": "Bypass failed"}

    def test_a_failing_source_with_releases_elsewhere_still_returns_them(self, client, main_module):
        from shelfmark.release_sources import Release

        release = Release(source="direct_download", source_id="r1", title="Dune", format="epub")
        with patch(
            "shelfmark.core.release_search.search_source_releases",
            return_value=(_Source(), [release], "prowlarr: down"),
        ):
            resp = _list_releases(
                client, main_module, {"provider": "hardcover", "book_id": "42"}, book=_book()
            )

        assert resp.status_code == 200
        body = resp.get_json()
        assert [r["source_id"] for r in body["releases"]] == ["r1"]
        assert body["errors"] == ["prowlarr: down"]

    def test_an_unavailable_source_returns_503(self, client, main_module):
        with patch(
            "shelfmark.core.release_search.search_source_releases",
            side_effect=SourceUnavailableError("Source is down"),
        ):
            resp = _list_releases(
                client, main_module, {"provider": "hardcover", "book_id": "42"}, book=_book()
            )

        assert resp.status_code == 503
        assert resp.get_json() == {"error": "Source is down"}

    def test_the_manual_provider_splits_joined_authors(self, client, main_module):
        resp = _list_releases(
            client,
            main_module,
            {
                "provider": "manual",
                "book_id": "m1",
                "title": "Good Omens",
                "author": "Terry Pratchett, Neil Gaiman",
            },
        )

        assert resp.status_code == 200
        book = resp.get_json()["book"]
        assert book["title"] == "Good Omens"
        assert book["authors"] == ["Terry Pratchett", "Neil Gaiman"]
        assert book["search_author"] == "Terry Pratchett"


class TestReleaseSourcesAndRecords:
    def test_release_sources_lists_the_registry(self, client, main_module):
        sources = [{"name": "direct_download", "enabled": True}]
        with (
            _no_auth(main_module),
            patch("shelfmark.release_sources.list_available_sources", return_value=sources),
        ):
            resp = client.get("/api/release-sources")

        assert resp.status_code == 200
        assert resp.get_json() == sources

    def test_release_sources_failure_returns_500(self, client, main_module):
        with (
            _no_auth(main_module),
            patch(
                "shelfmark.release_sources.list_available_sources",
                side_effect=RuntimeError("registry broke"),
            ),
        ):
            resp = client.get("/api/release-sources")

        assert resp.status_code == 500

    def test_an_unknown_source_is_a_400(self, client, main_module):
        with (
            _no_auth(main_module),
            patch("shelfmark.release_sources.get_source", side_effect=ValueError("unknown")),
        ):
            resp = client.get("/api/release-sources/nope/records/abc")

        assert resp.status_code == 400
        assert resp.get_json() == {"error": "Unknown release source: nope"}

    def test_a_missing_record_is_a_404(self, client, main_module):
        class _NoRecord:
            def get_record(self, record_id):
                return None

        with (
            _no_auth(main_module),
            patch("shelfmark.release_sources.get_source", return_value=_NoRecord()),
        ):
            resp = client.get("/api/release-sources/direct_download/records/abc")

        assert resp.status_code == 404
        assert resp.get_json() == {"error": "Record not found"}

    def test_record_ids_may_contain_slashes(self, client, main_module):
        seen: list[str] = []

        class _Records:
            def get_record(self, record_id):
                seen.append(record_id)
                return BrowseRecord(id=record_id, title="Browsed", source="stub")

        with (
            _no_auth(main_module),
            patch("shelfmark.release_sources.get_source", return_value=_Records()),
        ):
            resp = client.get("/api/release-sources/stub/records/a/b/c")

        assert resp.status_code == 200
        assert seen == ["a/b/c"]
        body = resp.get_json()
        assert body["id"] == "a/b/c"
        assert body["title"] == "Browsed"
        assert None not in body.values()


class TestReleaseRoutesNeedLogin:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/api/releases"),
            ("post", "/api/releases/download"),
            ("get", "/api/release-sources"),
            ("get", "/api/release-sources/stub/records/abc"),
        ],
    )
    def test_anonymous_requests_are_refused(self, client, main_module, method, path):
        with client.session_transaction() as sess:
            sess.clear()
        with patch.object(main_module, "get_auth_mode", return_value="builtin"):
            resp = getattr(client, method)(path)

        assert resp.status_code == 401
        assert resp.get_json() == {"error": "Unauthorized"}

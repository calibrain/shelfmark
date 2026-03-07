"""Tests for /api/releases with direct_download provider context."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from shelfmark.release_sources import (
    ColumnAlign,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    SourceUnavailableError,
)


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


class _FakeDirectSource:
    last_search_type = "title_author"

    def get_record(self, record_id, *, fetch_download_count=True):  # noqa: ANN001
        assert record_id == "md5-abc"
        assert fetch_download_count is True
        return SimpleNamespace(
            id="md5-abc",
            title="The Gun Seller",
            author="Iain Banks",
            preview="https://example.com/cover.jpg",
            description=None,
            publisher=None,
            year=None,
            language=None,
            source="direct_download",
            source_url=None,
        )

    def search(self, book, plan, expand_search=False, content_type="ebook"):  # noqa: ANN001
        assert book.provider == "direct_download"
        assert book.provider_id == "md5-abc"
        assert book.title == "The Gun Seller"
        assert plan.primary_query
        return [
            Release(
                source="direct_download",
                source_id="md5-rel-1",
                title="The Gun Seller",
                format="epub",
                size="2 MB",
            )
        ]

    def get_column_config(self):
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0,2fr) 80px",
        )


def test_releases_accepts_direct_download_provider(main_module, client):
    fake_direct_source = _FakeDirectSource()

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch("shelfmark.release_sources.get_source", return_value=fake_direct_source) as mock_get_source:
            with patch(
                "shelfmark.release_sources.list_available_sources",
                side_effect=AssertionError("list_available_sources should not be called"),
            ):
                resp = client.get(
                    "/api/releases",
                    query_string={
                        "provider": "direct_download",
                        "book_id": "md5-abc",
                    },
                )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["sources_searched"] == ["direct_download"]
    assert body["book"]["provider"] == "direct_download"
    assert body["book"]["provider_id"] == "md5-abc"
    assert body["book"]["title"] == "The Gun Seller"
    assert body["releases"][0]["source"] == "direct_download"
    assert body["releases"][0]["source_id"] == "md5-rel-1"
    assert body["search_info"]["direct_download"]["search_type"] == "title_author"
    assert mock_get_source.call_count == 2
    assert all(call.args == ("direct_download",) for call in mock_get_source.call_args_list)


def test_releases_direct_provider_returns_404_when_book_missing(main_module, client):
    class _MissingDirectSource:
        def get_record(self, record_id, *, fetch_download_count=True):  # noqa: ANN001
            assert record_id == "missing-md5"
            assert fetch_download_count is True
            return None

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch("shelfmark.release_sources.get_source", return_value=_MissingDirectSource()) as mock_get_source:
            resp = client.get(
                "/api/releases",
                query_string={
                    "provider": "direct_download",
                    "book_id": "missing-md5",
                },
            )

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Book not found in release source"}
    mock_get_source.assert_called_once_with("direct_download")


def test_releases_accepts_direct_source_query_mode(main_module, client):
    class _QueryDirectSource:
        last_search_type = "manual"

        def search(self, book, plan, expand_search=False, content_type="ebook"):  # noqa: ANN001
            assert book.provider == "manual"
            assert book.title == "Pride and Prejudice"
            assert plan.source_filters is not None
            assert plan.manual_query == "Pride and Prejudice"
            assert plan.source_filters.author == ["Jane Austen"]
            assert plan.source_filters.format == ["epub"]
            assert content_type == "ebook"
            return [
                Release(
                    source="direct_download",
                    source_id="md5-rel-query",
                    title="Pride and Prejudice",
                    format="epub",
                    size="1 MB",
                    extra={
                        "author": "Jane Austen",
                        "preview": "https://example.com/cover.jpg",
                    },
                )
            ]

        def get_column_config(self):
            return ReleaseColumnConfig(
                columns=[
                    ColumnSchema(
                        key="format",
                        label="Format",
                        render_type=ColumnRenderType.BADGE,
                        align=ColumnAlign.CENTER,
                        width="80px",
                    ),
                ],
                grid_template="minmax(0,2fr) 80px",
            )

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch("shelfmark.release_sources.get_source", return_value=_QueryDirectSource()) as mock_get_source:
            resp = client.get(
                "/api/releases",
                query_string={
                    "source": "direct_download",
                    "query": "Pride and Prejudice",
                    "author": "Jane Austen",
                    "format": "epub",
                },
            )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["sources_searched"] == ["direct_download"]
    assert body["book"]["provider"] == "manual"
    assert body["book"]["title"] == "Pride and Prejudice"
    assert body["releases"][0]["source"] == "direct_download"
    assert body["releases"][0]["source_id"] == "md5-rel-query"
    mock_get_source.assert_called_once_with("direct_download")


def test_release_source_record_endpoint_returns_generic_browse_record(main_module, client):
    fake_direct_source = _FakeDirectSource()

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch("shelfmark.release_sources.get_source", return_value=fake_direct_source) as mock_get_source:
            resp = client.get("/api/release-sources/direct_download/records/md5-abc")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["id"] == "md5-abc"
    assert body["title"] == "The Gun Seller"
    assert body["source"] == "direct_download"
    mock_get_source.assert_called_once_with("direct_download")


def test_releases_direct_provider_returns_503_when_source_unavailable(main_module, client):
    class _UnavailableDirectSource:
        def get_record(self, record_id, *, fetch_download_count=True):  # noqa: ANN001
            raise SourceUnavailableError("Unable to reach download source. Network restricted or mirrors are blocked.")

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch("shelfmark.release_sources.get_source", return_value=_UnavailableDirectSource()):
            resp = client.get(
                "/api/releases",
                query_string={
                    "provider": "direct_download",
                    "book_id": "md5-abc",
                },
            )

    assert resp.status_code == 503
    assert resp.get_json() == {
        "error": "Unable to reach download source. Network restricted or mirrors are blocked."
    }


def test_release_source_record_endpoint_returns_503_when_source_unavailable(main_module, client):
    class _UnavailableDirectSource:
        def get_record(self, record_id, *, fetch_download_count=True):  # noqa: ANN001
            raise SourceUnavailableError("Unable to reach download source. Network restricted or mirrors are blocked.")

    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch("shelfmark.release_sources.get_source", return_value=_UnavailableDirectSource()):
            resp = client.get("/api/release-sources/direct_download/records/md5-abc")

    assert resp.status_code == 503
    assert resp.get_json() == {
        "error": "Unable to reach download source. Network restricted or mirrors are blocked."
    }

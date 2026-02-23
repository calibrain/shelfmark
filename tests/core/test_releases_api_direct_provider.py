"""Tests for /api/releases with direct_download provider context."""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.release_sources import (
    ColumnAlign,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
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
        with patch.object(
            main_module.backend,
            "get_book_info",
            return_value={
                "id": "md5-abc",
                "title": "The Gun Seller",
                "author": "Iain Banks",
                "preview": "https://example.com/cover.jpg",
            },
        ) as mock_get_book_info:
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
    mock_get_book_info.assert_called_once_with("md5-abc")
    mock_get_source.assert_called_once_with("direct_download")


def test_releases_direct_provider_returns_404_when_book_missing(main_module, client):
    with patch.object(main_module, "get_auth_mode", return_value="none"):
        with patch.object(main_module.backend, "get_book_info", return_value=None):
            with patch("shelfmark.release_sources.get_source") as mock_get_source:
                resp = client.get(
                    "/api/releases",
                    query_string={
                        "provider": "direct_download",
                        "book_id": "missing-md5",
                    },
                )

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Book not found in direct source"}
    mock_get_source.assert_not_called()

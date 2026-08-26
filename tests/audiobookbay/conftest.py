"""AudiobookBay test fixtures."""

import pytest

from shelfmark.release_sources.audiobookbay import scraper


@pytest.fixture(autouse=True)
def _clear_detail_page_cache():
    """Detail pages are cached briefly in production; tests must not share them."""
    scraper.clear_detail_page_cache()
    yield
    scraper.clear_detail_page_cache()

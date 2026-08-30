"""One search fetches each distinct AA URL once, however many passes ask for it.

`DirectDownloadSource.search` fans out: a title variant per grouped variant, then -
when nothing was found - the whole set again without the language filter. With
DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH on, the requested language is applied locally
rather than as `&lang=`, so both passes build a byte-identical URL and the retry
re-fetches a page it already had. Behind DDoS-Guard that repeat is a fresh browser
solve, tens of seconds for nothing. See issue #1285.
"""

import pytest
from bs4 import BeautifulSoup

import shelfmark.release_sources.direct_download as dd
from shelfmark.core import search_deadline


@pytest.fixture(autouse=True)
def _no_ambient_deadline():
    token = search_deadline._current.set(None)
    yield
    search_deadline._current.reset(token)


class _Selector:
    current_base = "https://annas-archive.gl"
    last_failure = None

    def rewrite(self, url: str) -> str:
        return url

    def next_mirror_or_rotate_dns(self, *, fatal: bool = False, reason: str = ""):
        return None, "exhausted"


_PAGE = "<html><body><main><table><tbody></tbody></table></main></body></html>"


def _count_fetches(monkeypatch) -> list[str]:
    fetched: list[str] = []
    monkeypatch.setattr(
        dd.downloader, "html_get_page", lambda url, **_k: fetched.append(url) or _PAGE
    )
    monkeypatch.setattr(dd.network, "get_available_aa_urls", lambda: ["https://annas-archive.gl"])
    return fetched


def test_repeated_url_is_fetched_once_within_one_search(monkeypatch):
    fetched = _count_fetches(monkeypatch)
    url = "https://annas-archive.gl/search?q=dune"

    with dd._search_page_reuse():
        first_html, first_table = dd._fetch_search_table(url, _Selector())
        second_html, second_table = dd._fetch_search_table(url, _Selector())

    assert fetched == [url], "the second ask should have been served from the search's cache"
    assert first_html == second_html
    assert second_table is first_table


def test_distinct_urls_are_still_fetched_separately(monkeypatch):
    fetched = _count_fetches(monkeypatch)

    with dd._search_page_reuse():
        dd._fetch_search_table("https://annas-archive.gl/search?q=dune", _Selector())
        dd._fetch_search_table("https://annas-archive.gl/search?q=dune&lang=en", _Selector())

    assert len(fetched) == 2


def test_cache_does_not_leak_between_searches(monkeypatch):
    """A later request must not be answered from an earlier request's pages."""
    fetched = _count_fetches(monkeypatch)
    url = "https://annas-archive.gl/search?q=dune"

    with dd._search_page_reuse():
        dd._fetch_search_table(url, _Selector())
    with dd._search_page_reuse():
        dd._fetch_search_table(url, _Selector())

    assert fetched == [url, url]


def test_without_the_context_every_fetch_still_goes_out(monkeypatch):
    """Callers outside a search - `get_book_info`, downloads - are unaffected."""
    fetched = _count_fetches(monkeypatch)
    url = "https://annas-archive.gl/search?q=dune"

    dd._fetch_search_table(url, _Selector())
    dd._fetch_search_table(url, _Selector())

    assert fetched == [url, url]


def test_a_failure_is_not_cached(monkeypatch):
    """A spent budget raises; the next search must not inherit that as a stored answer."""
    fetched = _count_fetches(monkeypatch)
    url = "https://annas-archive.gl/search?q=dune"

    with dd._search_page_reuse():
        with search_deadline.search_deadline(60) as deadline:
            deadline.event.set()
            with pytest.raises(dd.SearchUnavailableError):
                dd._fetch_search_table(url, _Selector())
        dd._fetch_search_table(url, _Selector())

    assert fetched == [url], "the successful retry should be the only fetch"


def test_language_retry_reuses_the_page_it_already_fetched(monkeypatch):
    """The end-to-end shape: language-from-path makes both passes build the same URL."""
    fetched = _count_fetches(monkeypatch)

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)
    monkeypatch.setattr(dd.network, "get_aa_base_url", lambda: "https://annas-archive.gl")

    filters_with_lang = dd.SearchFilters(lang=["en"])
    filters_without = dd.SearchFilters()

    with dd._search_page_reuse():
        dd.search_books("dune", filters_with_lang)
        dd.search_books("dune", filters_without)

    assert len(fetched) == 1, f"both passes build the same URL, got {fetched}"
    assert "lang=" not in fetched[0]


def test_soup_reuse_is_safe_for_repeated_parsing(monkeypatch):
    """The cached Tag is read repeatedly, so reuse must not consume it."""
    page = (
        "<html><body><main><table><tbody><tr><td>row</td></tr></tbody></table></main></body></html>"
    )
    monkeypatch.setattr(dd.downloader, "html_get_page", lambda _url, **_k: page)
    monkeypatch.setattr(dd.network, "get_available_aa_urls", lambda: ["https://annas-archive.gl"])

    with dd._search_page_reuse():
        _, first = dd._fetch_search_table("https://annas-archive.gl/search?q=dune", _Selector())
        _, second = dd._fetch_search_table("https://annas-archive.gl/search?q=dune", _Selector())

    assert first is not None
    assert second is not None
    assert len(first.find_all("tr")) == 1
    assert len(second.find_all("tr")) == 1
    assert isinstance(BeautifulSoup(str(second), "html.parser"), BeautifulSoup)

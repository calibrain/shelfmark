from shelfmark.core.models import SearchFilters
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import BrowseRecord
from shelfmark.release_sources.direct_download import DirectDownloadSource


def _browse_record(record_id: str, title: str) -> BrowseRecord:
    return BrowseRecord(id=record_id, title=title, source="direct_download")


def _enable_direct_download(monkeypatch):
    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_ENABLED":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: True)
    return dd


class TestDirectDownloadSearchQueries:
    def test_uses_search_title_for_english_queries(self, monkeypatch):
        captured: list[str] = []

        def fake_search_books(query: str, filters):
            captured.append(query)
            return []

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        source.search(book, plan, expand_search=True)

        assert "The Final Empire Brandon Sanderson" in captured
        assert "A végső birodalom Brandon Sanderson" in captured
        assert "Mistborn: The Final Empire Brandon Sanderson" not in captured

    def test_deduplicates_results_across_localized_queries(self, monkeypatch):
        captured: list[tuple[str, list[str] | None]] = []
        records_by_query = {
            "The Final Empire Brandon Sanderson": [
                _browse_record("shared", "Shared release"),
                _browse_record("en-only", "English only"),
            ],
            "A végső birodalom Brandon Sanderson": [
                _browse_record("shared", "Shared release"),
                _browse_record("hu-only", "Hungarian only"),
            ],
        }

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang))
            return records_by_query[query]

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        results = source.search(book, plan, expand_search=True)

        assert captured == [
            ("The Final Empire Brandon Sanderson", ["en"]),
            ("A végső birodalom Brandon Sanderson", ["hu"]),
        ]
        assert [release.source_id for release in results] == ["shared", "en-only", "hu-only"]

    def test_retries_without_language_filters_when_localized_queries_miss(self, monkeypatch):
        captured: list[tuple[str, list[str] | None]] = []
        fallback_results = {
            "The Final Empire Brandon Sanderson": [
                _browse_record("fallback-en", "Fallback English")
            ],
            "A végső birodalom Brandon Sanderson": [
                _browse_record("fallback-hu", "Fallback Hungarian")
            ],
        }

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang))
            if filters.lang:
                return []
            return fallback_results[query]

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            search_title="The Final Empire",
            search_author="Brandon Sanderson",
            authors=["Brandon Sanderson"],
            titles_by_language={
                "en": "Mistborn: The Final Empire",
                "hu": "A végső birodalom",
            },
        )

        plan = build_release_search_plan(book, languages=["en", "hu"])
        results = source.search(book, plan, expand_search=True)

        assert captured == [
            ("The Final Empire Brandon Sanderson", ["en"]),
            ("A végső birodalom Brandon Sanderson", ["hu"]),
            ("The Final Empire Brandon Sanderson", None),
            ("A végső birodalom Brandon Sanderson", None),
        ]
        assert [release.source_id for release in results] == ["fallback-en", "fallback-hu"]

    def test_manual_query_fallback_preserves_other_filters(self, monkeypatch):
        captured: list[tuple[str, list[str] | None, list[str] | None]] = []

        def fake_search_books(query: str, filters):
            captured.append((query, filters.lang, filters.format))
            if filters.lang:
                return []
            return [_browse_record("manual-1", "Manual result")]

        dd = _enable_direct_download(monkeypatch)

        monkeypatch.setattr(dd, "search_books", fake_search_books)

        source = DirectDownloadSource()
        book = BookMetadata(
            provider="hardcover",
            provider_id="123",
            title="Mistborn: The Final Empire",
            authors=["Brandon Sanderson"],
        )

        plan = build_release_search_plan(
            book,
            languages=["en"],
            manual_query="mistborn custom query",
            source_filters=SearchFilters(format=["epub"], sort="newest"),
        )
        results = source.search(book, plan)

        assert [release.source_id for release in results] == ["manual-1"]
        assert captured == [
            ("mistborn custom query", ["en"], ["epub"]),
            ("mistborn custom query", None, ["epub"]),
        ]


def test_parse_search_result_row_detects_language_from_distant_path(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-1"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>fiction</span></td>
      <td><span>epub</span></td>
      <td><span>1 mb</span></td>
      <td><span>lgli/N:\comics1\emule\2021.08.01\[BD FR] Scrameustache.cbz</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "fr"
    assert record.download_path == "lgli/N:\\comics1\\emule\\2021.08.01\\[BD FR] Scrameustache.cbz"


def test_parse_search_result_row_detects_mixed_case_language_from_distant_path(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-mixed"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>fiction</span></td>
      <td><span>pdf</span></td>
      <td><span>1 mb</span></td>
      <td><span>lgli/V:\comics\_0DAY3\[Fr]\BDs [Fr]\!Pdf\S\Scrameustache\Tome 04 - Le totem de l'espace.pdf</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "fr"


def test_parse_search_result_row_overrides_unknown_with_distant_path_language(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-unknown"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td><span>unknown</span></td>
      <td><span>fiction</span></td>
      <td><span>pdf</span></td>
      <td><span>1 mb</span></td>
      <td><span>lgli/V:\comics\_0DAY3\[Fr]\BDs [Fr]\!Pdf\S\Scrameustache\Tome 04 - Le totem de l'espace.pdf</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "fr"


def test_extract_distant_path_accepts_forward_slash_after_drive(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-fslash"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>fiction</span></td>
      <td><span>pdf</span></td>
      <td><span>1 mb</span></td>
      <td><span>lgli/V:/comics/_0DAY3/[Fr]/BDs [Fr]/!Pdf/S/Scrameustache/Tome 04 - Le totem de l'espace.pdf</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "fr"


def test_parse_search_result_row_detects_language_from_spaced_distant_path(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-spaced"><img src="cover.jpg"></a></td>
      <td><span>Le totem de l'espace</span></td>
      <td><span>Gos</span></td>
      <td><span>Dupuis</span></td>
      <td><span>1977</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>Comic book</span></td>
      <td><span>pdf</span></td>
      <td><span>39.7MB</span></td>
      <td><span>lgli /V: \comics \ _0DAY3 \[Fr] \BDs [Fr] \!Pdf \S \Scrameustache \Tome 04 - Le totem de l'espace .pdf</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.download_path is not None
    assert record.language == "fr"


def test_parse_search_result_row_avoids_de_false_positive_from_french_sentence(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-de-fp"><img src="cover.jpg"></a></td>
      <td><span>Le totem de l'espace</span></td>
      <td><span>Gos</span></td>
      <td><span>Dupuis</span></td>
      <td><span>1977</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>Comic book</span></td>
      <td><span>cbr</span></td>
      <td><span>40.6MB</span></td>
      <td><span>lgli/V:\comics\_NON _ENG _ORIG\BDtheque\BD Franco - Belge\S\Scrameustache (le)\Scrameustache (le) - T04 - Le Totem De L'espace [bdc - Jeunesse - Fr].cbr</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "fr"


def test_parse_search_result_row_avoids_en_false_positive_when_french_is_present(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-en-fp"><img src="cover.jpg"></a></td>
      <td><span>Le totem de l'espace</span></td>
      <td><span>Gos</span></td>
      <td><span>Dupuis</span></td>
      <td><span>1977</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>Comic book</span></td>
      <td><span>cbr</span></td>
      <td><span>40.6MB</span></td>
      <td><span>lgli/V:\comics\_0DAY2\Stripboeken Frans - Comix in French - BD en Français\Le Scrameustache\[BD Fr] Le Scrameustache - 04 - Le Totem De L'Espace.cbr</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "fr"


def test_search_books_filters_language_locally_when_path_language_enabled(monkeypatch):
        import shelfmark.release_sources.direct_download as dd

        captured_url: dict[str, str] = {}

        original_get = dd.config.get

        def _fake_get(key: str, default=None, user_id=None):
                del user_id
                if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
                        return True
                return original_get(key, default)

        monkeypatch.setattr(dd.config, "get", _fake_get)
        monkeypatch.setattr(dd.network, "get_aa_base_url", lambda: "https://mirror.example")
        monkeypatch.setattr(dd.network, "AAMirrorSelector", lambda: object())

        def _fake_html_get_page(url: str, selector, allow_bypasser_fallback=False):
                del selector, allow_bypasser_fallback
                captured_url["url"] = url
                return r"""
                <table>
                    <tr>
                        <td><a href="/md5/record-fr"><img src="cover.jpg"></a></td>
                        <td><span>Livre FR</span></td>
                        <td><span>Auteur</span></td>
                        <td><span>Editeur</span></td>
                        <td><span>2025</span></td>
                        <td><span>-</span></td>
                        <td><span>-</span></td>
                        <td></td>
                        <td><span>fiction</span></td>
                        <td><span>pdf</span></td>
                        <td><span>2 mb</span></td>
                        <td><span>lgli/V:\comics\_0DAY3\[Fr]\BDs [Fr]\!Pdf\S\Book FR.pdf</span></td>
                    </tr>
                    <tr>
                        <td><a href="/md5/record-en"><img src="cover.jpg"></a></td>
                        <td><span>Book EN</span></td>
                        <td><span>Author</span></td>
                        <td><span>Publisher</span></td>
                        <td><span>2025</span></td>
                        <td><span>-</span></td>
                        <td><span>-</span></td>
                        <td></td>
                        <td><span>fiction</span></td>
                        <td><span>pdf</span></td>
                        <td><span>2 mb</span></td>
                        <td><span>lgli/V:\comics\_0DAY3\[En]\Comics\!Pdf\S\Book EN.pdf</span></td>
                    </tr>
                </table>
                """

        monkeypatch.setattr(dd.downloader, "html_get_page", _fake_html_get_page)

        records = dd.search_books("demo", SearchFilters(lang=["fr"], format=["pdf"]))

        assert "&lang=" not in captured_url["url"]
        assert len(records) == 1
        assert records[0].id == "record-fr"
        assert records[0].language == "fr"


def test_parse_search_result_row_sets_unknown_when_path_language_not_detected(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return True
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-2"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>fiction</span></td>
      <td><span>epub</span></td>
      <td><span>1 mb</span></td>
      <td><span>lgli/N:\comics1\emule\2021.08.01\Scrameustache.cbz</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is not None
    assert record.language == "unknown"


def test_parse_search_result_row_keeps_legacy_behavior_when_toggle_disabled(monkeypatch):
    from bs4 import BeautifulSoup

    import shelfmark.release_sources.direct_download as dd

    original_get = dd.config.get

    def _fake_get(key: str, default=None, user_id=None):
        del user_id
        if key == "DIRECT_DOWNLOAD_LANGUAGE_FROM_PATH":
            return False
        return original_get(key, default)

    monkeypatch.setattr(dd.config, "get", _fake_get)

    html = r"""
    <tr>
      <td><a href="/md5/record-3"><img src="cover.jpg"></a></td>
      <td><span>A Book Title</span></td>
      <td><span>Author Name</span></td>
      <td><span>Publisher</span></td>
      <td><span>2024</span></td>
      <td><span>-</span></td>
      <td><span>-</span></td>
      <td></td>
      <td><span>fiction</span></td>
      <td><span>epub</span></td>
      <td><span>1 mb</span></td>
      <td><span>lgli/N:\comics1\emule\2021.08.01\[BD FR] Scrameustache.cbz</span></td>
    </tr>
    """
    row = BeautifulSoup(html, "html.parser").find("tr")

    record = dd._parse_search_result_row(row)

    assert record is None

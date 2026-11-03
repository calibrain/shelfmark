import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import MetadataSearchOptions, SearchType
from shelfmark.metadata_providers.lubimyczytac import (
    LubimyczytacProvider,
    _split_title_author,
    _valid_isbn,
)

SEARCH_HTML = """
<html><body>
  <article class="book-card book-card--l" data-book-id="123456">
    <a class="book-card__title" href="/ksiazka/123456/wiedzmin-ostatnie-zyczenie">Ostatnie życzenie</a>
    <div class="book-card__author">
      <a href="/autor/andrzej-sapkowski">Andrzej Sapkowski</a>
    </div>
    <img class="book-card__cover-image" src="/uploads/books/cover.jpg" alt="Okładka" />
    <span>1993</span>
  </article>
  <article class="book-card book-card--l" data-book-id="123457">
    <a class="book-card__title" href="/ksiazka/123457/krew-elfow">Krew elfów</a>
    <div class="book-card__author">
      <a href="/autor/andrzej-sapkowski">Andrzej Sapkowski</a>
    </div>
  </article>
</body></html>
"""

BOOK_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "https://schema.org", "@type": "Book",
  "@id": "https://lubimyczytac.pl/ksiazka/123456/wiedzmin-ostatnie-zyczenie",
  "url": "https://lubimyczytac.pl/ksiazka/123456/wiedzmin-ostatnie-zyczenie",
  "name": "Ostatnie życzenie", "author": [{"@type": "Person", "name": "Andrzej Sapkowski"}],
  "isbn": "9788375780635", "image": "https://img.lubimyczytac.pl/cover.jpg",
  "description": "Zbiór opowiadań o wiedźminie Geralcie.",
  "publisher": {"@type": "Organization", "name": "superNOWA"},
  "datePublished": "2014", "inLanguage": "pl",
  "genre": ["Fantasy", "Opowiadania"],
  "isPartOf": {"@type": "BookSeries", "@id": "https://lubimyczytac.pl/cykl/123", "name": "Wiedźmin", "position": 1}
}
</script></head><body>
<h1>Ostatnie życzenie</h1>
<dl><dt>Tłumacz:</dt><dd>Danuta Stok</dd></dl>
</body></html>
"""


class _Response:
    def __init__(self, text, json_data=None):
        self.text = text
        self.json_data = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self.json_data


class _Session:
    def __init__(self, pages, post_pages=None):
        self.pages = pages
        self.post_pages = post_pages or {}
        self.urls = []
        self.posts = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.urls.append(url)
        for fragment, html in self.pages.items():
            if fragment in url:
                return _Response(html)
        raise requests.HTTPError(f"unexpected URL: {url}")

    def post(self, url, data=None, **kwargs):
        self.posts.append({"url": url, "data": data, "kwargs": kwargs})
        for fragment, html in self.post_pages.items():
            if fragment in url:
                return _Response("", {"data": {"content": html}})
        raise requests.HTTPError(f"unexpected POST URL: {url}")


class _FlakySession(_Session):
    def get(self, url, **kwargs):
        if not self.urls:
            self.urls.append(url)
            raise requests.Timeout
        return super().get(url, **kwargs)


def _provider(session):
    provider = LubimyczytacProvider()
    provider.session = session
    return provider


class TestLubimyczytacSearch:
    def test_search_by_title_parses_cards(self):
        get_metadata_cache().clear()
        session = _Session({"szukaj/ksiazki": SEARCH_HTML})
        books = _provider(session).search(MetadataSearchOptions(query="Ostatnie życzenie"))

        assert [book.provider_id for book in books] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]
        assert books[0].title == "Ostatnie życzenie"
        assert books[0].authors == ["Andrzej Sapkowski"]
        assert books[0].cover_url == "https://lubimyczytac.pl/uploads/books/cover.jpg"
        assert books[0].publish_year is None
        assert "phrase=Ostatnie+%C5%BCyczenie" in session.urls[0]

    def test_search_parses_display_fields(self):
        get_metadata_cache().clear()
        html = SEARCH_HTML.replace(
            "<span>1993</span>",
            "<span>1993</span>"
            '<span class="rating__avarage">8,4</span>'
            '<span class="rating__text"> <small>z</small> 2232 ocen </span>'
            '<span class="book-card__readers"> Czytelnicy: <span class="book-card__highlighted">3841</span></span>'
            '<span class="book-card__reviews"> Opinie: <span class="book-card__highlighted">90</span></span>',
            1,
        )
        books = _provider(_Session({"szukaj/ksiazki": html})).search(
            MetadataSearchOptions(query="Ostatnie życzenie")
        )

        fields = {field.label: field.value for field in books[0].display_fields}
        assert fields["Rating"] == "8,4 (2232)"
        assert fields["Readers"] == "3841"
        assert fields["Reviews"] == "90"

    def test_search_splits_title_and_author_with_separator(self):
        get_metadata_cache().clear()
        bad_html = SEARCH_HTML.replace("Andrzej Sapkowski", "Irene Anders")
        session = _Session(
            {
                "phrase=Ostatnie+%C5%BCyczenie": bad_html,
                "phrase=Ostatnie+%C5%BCyczenie+Sapkowski": SEARCH_HTML,
            }
        )
        provider = _provider(session)

        books = provider.search(
            MetadataSearchOptions(query="Ostatnie życzenie – Andrzej Sapkowski")
        )

        assert books == []
        assert len(session.urls) == 2

    def test_search_splits_author_before_title(self):
        get_metadata_cache().clear()
        bad_html = SEARCH_HTML.replace("Andrzej Sapkowski", "Irene Anders")
        session = _Session(
            {
                "phrase=Ostatnie+%C5%BCyczenie": bad_html,
                "phrase=Andrzej+Sapkowski+Ostatnie+%C5%BCyczenie": SEARCH_HTML,
            }
        )
        provider = _provider(session)

        books = provider.search(MetadataSearchOptions(query="Andrzej Sapkowski Ostatnie życzenie"))

        assert [book.provider_id for book in books] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]
        assert len(session.urls) == 2

    def test_search_by_author_uses_author_field(self):
        get_metadata_cache().clear()
        provider = _provider(_Session({"szukaj/ksiazki": SEARCH_HTML}))
        books = provider.search(
            MetadataSearchOptions(query="Andrzej Sapkowski", search_type=SearchType.AUTHOR)
        )
        assert [book.title for book in books] == ["Ostatnie życzenie", "Krew elfów"]

    def test_author_search_matches_surname_only(self):
        get_metadata_cache().clear()
        provider = _provider(_Session({"szukaj/ksiazki": SEARCH_HTML}))
        books = provider.search(
            MetadataSearchOptions(query="Sapkowski", search_type=SearchType.AUTHOR)
        )
        assert [book.title for book in books] == ["Ostatnie życzenie", "Krew elfów"]

    def test_author_search_matches_name_without_middle_initial(self):
        get_metadata_cache().clear()
        html = SEARCH_HTML.replace("Andrzej Sapkowski", "George R.R. Martin")
        provider = _provider(_Session({"szukaj/ksiazki": html}))
        books = provider.search(
            MetadataSearchOptions(query="George Martin", search_type=SearchType.AUTHOR)
        )
        assert [book.title for book in books] == ["Ostatnie życzenie", "Krew elfów"]

    def test_author_search_rejects_partial_name_matches(self):
        get_metadata_cache().clear()
        html = SEARCH_HTML.replace(
            "Andrzej Sapkowski",
            "Andrzej Sapkowskiowski",
        )
        provider = _provider(_Session({"szukaj/ksiazki": html}))

        books = provider.search(
            MetadataSearchOptions(query="Andrzej Sapkowski", search_type=SearchType.AUTHOR)
        )

        assert books == []

    def test_author_search_keeps_results_without_author_data(self):
        get_metadata_cache().clear()
        html = SEARCH_HTML.replace(
            '<a href="/autor/andrzej-sapkowski">Andrzej Sapkowski</a>',
            "",
        )
        provider = _provider(_Session({"szukaj/ksiazki": html}))

        books = provider.search(
            MetadataSearchOptions(query="Andrzej Sapkowski", search_type=SearchType.AUTHOR)
        )

        assert [book.provider_id for book in books] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]

    def test_author_search_matches_case_insensitively(self):
        get_metadata_cache().clear()
        provider = _provider(_Session({"szukaj/ksiazki": SEARCH_HTML}))

        books = provider.search(
            MetadataSearchOptions(query="ANDRZEJ SAPKOWSKI", search_type=SearchType.AUTHOR)
        )

        assert [book.provider_id for book in books] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]

    def test_search_page_two_uses_post_pagination(self):
        get_metadata_cache().clear()
        page_two_html = (
            SEARCH_HTML.replace("123456", "223456")
            .replace("123457", "223457")
            .replace("Ostatnie życzenie", "Inna książka")
            .replace("Krew elfów", "Druga książka")
        )
        session = _Session({}, post_pages={"/searcher/getNextResults/ksiazki": page_two_html})
        provider = _provider(session)

        books = provider.search(MetadataSearchOptions(query="wiedźmin", page=2))

        assert [book.provider_id for book in books] == [
            "223456--wiedzmin-ostatnie-zyczenie",
            "223457--krew-elfow",
        ]
        assert [book.title for book in books] == ["Inna książka", "Druga książka"]
        assert len(session.posts) == 1
        assert session.posts[0]["data"] == {
            "page": 2,
            "listId": "ksiazki",
            "phrase": "wiedźmin",
            "paginatorType": "Standard",
        }
        assert session.posts[0]["kwargs"]["headers"] == {"X-Requested-With": "XMLHttpRequest"}

    def test_search_respects_result_limit(self):
        get_metadata_cache().clear()
        session = _Session({"szukaj/ksiazki": SEARCH_HTML})
        provider = _provider(session)

        books = provider.search(MetadataSearchOptions(query="wiedźmin", limit=1))

        assert [book.provider_id for book in books] == ["123456--wiedzmin-ostatnie-zyczenie"]

    def test_search_cache_separates_pages_and_limits(self):
        get_metadata_cache().clear()
        session = _Session(
            {"szukaj/ksiazki": SEARCH_HTML},
            post_pages={"/searcher/getNextResults/ksiazki": SEARCH_HTML},
        )
        provider = _provider(session)

        first_page = provider.search(MetadataSearchOptions(query="wiedźmin", page=1, limit=1))
        second_page = provider.search(MetadataSearchOptions(query="wiedźmin", page=2, limit=2))

        assert len(first_page) == 1
        assert len(second_page) == 2
        assert len(session.urls) == 1
        assert len(session.posts) == 1
        assert session.posts[0]["data"]["page"] == 2

    def test_search_ignores_promoted_offers(self):
        get_metadata_cache().clear()
        promoted = """
        <div class="promoted-offers">
          <article class="book-card book-card--l" data-book-id="999999">
            <a class="book-card__title" href="/ksiazka/999999/reklamowana-ksiazka">
              Reklamowana książka
            </a>
          </article>
        </div>
        """
        html = SEARCH_HTML.replace("</body>", promoted + "</body>")
        provider = _provider(_Session({"szukaj/ksiazki": html}))

        books = provider.search(MetadataSearchOptions(query="wiedźmin"))

        assert [book.provider_id for book in books] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]

    def test_search_removes_duplicate_books(self):
        get_metadata_cache().clear()
        duplicate = """
        <article class="book-card book-card--l" data-book-id="123456">
          <a class="book-card__title" href="/ksiazka/123456/wiedzmin-ostatnie-zyczenie">
            Ostatnie życzenie
          </a>
        </article>
        """
        html = SEARCH_HTML.replace("</body>", duplicate + "</body>")
        provider = _provider(_Session({"szukaj/ksiazki": html}))

        books = provider.search(MetadataSearchOptions(query="wiedźmin"))

        assert [book.provider_id for book in books] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]

    def test_search_does_not_cache_request_failures(self):
        get_metadata_cache().clear()
        session = _FlakySession({"szukaj/ksiazki": SEARCH_HTML})
        provider = _provider(session)
        options = MetadataSearchOptions(query="wiedźmin")

        assert provider.search(options) == []
        assert [book.provider_id for book in provider.search(options)] == [
            "123456--wiedzmin-ostatnie-zyczenie",
            "123457--krew-elfow",
        ]
        assert len(session.urls) == 2

    def test_general_search_retries_without_inferred_author(self):
        get_metadata_cache().clear()
        bad_html = SEARCH_HTML.replace("Andrzej Sapkowski", "Irene Anders")
        session = _Session(
            {
                "phrase=Elf%C3%B3w": bad_html,
                "phrase=Wied%C5%BAmin+Krew+Elf%C3%B3w": SEARCH_HTML,
            }
        )
        books = _provider(session).search(MetadataSearchOptions(query="Wiedźmin Krew Elfów"))
        assert "123457--krew-elfow" in [book.provider_id for book in books]
        assert len(session.urls) == 2
        assert "phrase=Wied%C5%BAmin+Krew+Elf%C3%B3w" in session.urls[1]

    def test_empty_raw_retry_continues_to_alternative(self):
        get_metadata_cache().clear()
        session = _Session(
            {
                "phrase=Elf%C3%B3w": "",
                "phrase=Wied%C5%BAmin+Krew+Elf%C3%B3w": "",
                "phrase=Krew+Elf%C3%B3w": SEARCH_HTML,
            }
        )
        books = _provider(session).search(MetadataSearchOptions(query="Wiedźmin Krew Elfów"))
        assert "123457--krew-elfow" in [b.provider_id for b in books]
        assert len(session.urls) == 3

    def test_hyphenated_title_is_not_treated_as_explicit_separator(self):
        title, author, explicit_separator = _split_title_author("Spider-Man Stan Lee")
        assert title == "Spider-Man Stan Lee"
        assert author == ""
        assert explicit_separator is False

    def test_hyphenated_title_retries_original_query_without_author_filter(self):
        get_metadata_cache().clear()
        session = _Session({"phrase=Spider-Man+Stan+Lee": SEARCH_HTML})
        books = _provider(session).search(MetadataSearchOptions(query="Spider-Man Stan Lee"))
        assert books
        assert len(session.urls) == 1
        assert "phrase=Spider-Man+Stan+Lee" in session.urls[0]

    def test_unspaced_hyphen_query_is_kept_whole(self):
        query = "Ostatnie życzenie-Andrzej Sapkowski"
        assert _split_title_author(query) == (query, "", False)

    def test_search_splits_multiword_author_with_separator(self):
        get_metadata_cache().clear()
        query = "Ostatnie życzenie – Anna Maria Kowalska"
        title, author, explicit_separator = _split_title_author(query)
        assert title == "Ostatnie życzenie"
        assert author == "Anna Maria Kowalska"
        assert explicit_separator is True

    def test_author_search_matches_without_polish_diacritics(self):
        get_metadata_cache().clear()
        html = SEARCH_HTML.replace("Andrzej Sapkowski", "Andrzej Żulczyk")
        provider = _provider(_Session({"szukaj/ksiazki": html}))
        books = provider.search(
            MetadataSearchOptions(query="Andrzej Zulczyk", search_type=SearchType.AUTHOR)
        )
        assert len(books) == 2


class TestLubimyczytacBook:
    def test_lightbox_cover_has_priority_over_json_ld(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            "<h1>Ostatnie życzenie</h1>",
            '<a id="js-lightboxCover" href="https://img.lubimyczytac.pl/full.jpg"></a><h1>Ostatnie życzenie</h1>',
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )
        assert book is not None
        assert book.cover_url == "https://img.lubimyczytac.pl/full.jpg"

    def test_get_book_uses_open_graph_cover_fallback(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"image": "https://img.lubimyczytac.pl/cover.jpg",',
            "",
        ).replace(
            "<head>",
            '<head><meta property="og:image" content="https://img.lubimyczytac.pl/og-cover.jpg">',
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.cover_url == "https://img.lubimyczytac.pl/og-cover.jpg"

    def test_get_book_uses_lightbox_cover_fallback(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"image": "https://img.lubimyczytac.pl/cover.jpg",',
            "",
        ).replace(
            "<h1>Ostatnie życzenie</h1>",
            '<a id="js-lightboxCover" href="/covers/lightbox.jpg"></a><h1>Ostatnie życzenie</h1>',
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.cover_url == "https://lubimyczytac.pl/covers/lightbox.jpg"

    def test_get_book_uses_book_cover_link_fallback(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"image": "https://img.lubimyczytac.pl/cover.jpg",',
            "",
        ).replace(
            "<h1>Ostatnie życzenie</h1>",
            '<a class="book-cover__link" href="/covers/book-cover.jpg"></a>'
            "<h1>Ostatnie życzenie</h1>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.cover_url == "https://lubimyczytac.pl/covers/book-cover.jpg"

    def test_get_book_uses_description_and_publisher_html_fallbacks(self):
        get_metadata_cache().clear()
        html = (
            BOOK_HTML.replace(
                '"description": "Zbiór opowiadań o wiedźminie Geralcie.",',
                "",
            )
            .replace(
                '"publisher": {"@type": "Organization", "name": "superNOWA"},',
                "",
            )
            .replace(
                "<h1>Ostatnie życzenie</h1>",
                "<h1>Ostatnie życzenie</h1>"
                '<div id="book-description"><p>Opis z <strong>LubimyCzytać</strong>.</p><p>Drugi akapit.</p></div>'
                '<span class="book__txt">Wydawnictwo: '
                "<a>superNOWA</a></span>",
            )
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.description == "Opis z LubimyCzytać.\n\nDrugi akapit."
        assert book.publisher == "superNOWA"

    def test_get_book_uses_open_graph_description_fallback(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"description": "Zbiór opowiadań o wiedźminie Geralcie.",',
            "",
        ).replace(
            "<head>",
            '<head><meta property="og:description" content="Opis z Open Graph.">',
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.description == "Opis z Open Graph."

    def test_get_book_parses_language_from_dt_dd(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"inLanguage": "pl",',
            "",
        ).replace(
            "<h1>Ostatnie życzenie</h1>",
            "<dt>Język:</dt><dd>polski</dd><h1>Ostatnie życzenie</h1>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.language == "pl"

    def test_get_book_parses_series_from_reference_html_structure(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"isPartOf": {"@type": "BookSeries", "@id": '
            '"https://lubimyczytac.pl/cykl/123", "name": "Wiedźmin", '
            '"position": 1}',
            "",
        ).replace(
            "<h1>Ostatnie życzenie</h1>",
            '<span class="d-none d-sm-block mt-1">Cykl: '
            '<a href="/cykl/123">Wiedźmin</a>, tom 1</span>'
            "<h1>Ostatnie życzenie</h1>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.series_name == "Wiedźmin"
        assert book.series_id == "123"
        assert book.series_position == 1.0

    def test_get_book_parses_metadata_and_translator(self):
        get_metadata_cache().clear()
        book = _provider(
            _Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": BOOK_HTML})
        ).get_book("123456--wiedzmin-ostatnie-zyczenie")

        assert book is not None
        assert book.title == "Ostatnie życzenie"
        assert book.authors == ["Andrzej Sapkowski"]
        assert book.isbn_13 == "9788375780635"
        assert book.cover_url == "https://img.lubimyczytac.pl/cover.jpg"
        assert book.description == "Zbiór opowiadań o wiedźminie Geralcie."
        assert book.publisher == "superNOWA"
        assert book.publish_year == 2014
        assert book.language == "pl"
        assert book.genres == ["Fantasy", "Opowiadania"]
        assert (book.series_id, book.series_name, book.series_position) == (
            "123",
            "Wiedźmin",
            1.0,
        )
        assert [(field.label, field.value) for field in book.display_fields] == [
            ("Translator", "Danuta Stok")
        ]

    def test_get_book_parses_lubimyczytac_detail_fields(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"isPartOf": {"@type": "BookSeries", "@id": '
            '"https://lubimyczytac.pl/cykl/123", "name": "Wiedźmin", "position": 1}',
            '"isPartOfSeries": {"@type": "BookSeries", '
            '"url": "https://lubimyczytac.pl/cykl/10975/wiedzmin-geralt-z-rivii", '
            '"name": "Wiedźmin Geralt z Rivii", "position": "1"}',
        ).replace(
            "<dl><dt>Tłumacz:</dt><dd>Danuta Stok</dd></dl>",
            "<dl><dt>Kategoria:</dt><dd>fantasy, science fiction</dd>"
            "<dt>Cykl:</dt><dd>Wiedźmin Geralt z Rivii (tom 1)</dd>"
            "<dt>Liczba stron:</dt><dd>332</dd>"
            "<dt>ISBN:</dt><dd>9788375780635</dd>"
            "<dt>Język:</dt><dd>polski</dd></dl>",
        )
        html = html.replace(
            chr(34)
            + "datePublished"
            + chr(34)
            + ": "
            + chr(34)
            + "2014"
            + chr(34)
            + ", "
            + chr(34)
            + "inLanguage"
            + chr(34)
            + ": "
            + chr(34)
            + "pl"
            + chr(34)
            + ",",
            chr(34)
            + "datePublished"
            + chr(34)
            + ": "
            + chr(34)
            + "2014"
            + chr(34)
            + ", "
            + chr(34)
            + "inLanguage"
            + chr(34)
            + ": "
            + chr(34)
            + "pl"
            + chr(34)
            + ", "
            + chr(34)
            + "aggregateRating"
            + chr(34)
            + ": {"
            + chr(34)
            + "ratingValue"
            + chr(34)
            + ": "
            + chr(34)
            + "8,9"
            + chr(34)
            + ", "
            + chr(34)
            + "ratingCount"
            + chr(34)
            + ": 2300},",
        )
        html = html.replace(
            "</dl>",
            "<dt>Tytuł oryginału:</dt><dd>The Last Wish</dd><dt>Format:</dt><dd>papier</dd><dt>Data wydania:</dt><dd>2014-01-01</dd><dt>Data 1. wyd. pol.:</dt><dd>1990-01-01</dd></dl>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.isbn_13 == "9788375780635"
        assert book.genres == ["fantasy", "science fiction"]
        assert book.series_name == "Wiedźmin Geralt z Rivii"
        assert book.series_id == "10975"
        assert book.series_position == 1.0
        assert ("Pages", "332") in [(field.label, field.value) for field in book.display_fields]
        fields = {field.label: field.value for field in book.display_fields}
        assert fields["Rating"] == "8,9/10"
        assert fields["Ratings"] == "2300"
        assert fields["Original title"] == "The Last Wish"
        assert fields["Format"] == "papier"
        assert fields["Publication date"] == "2014-01-01"
        assert fields["First Polish edition"] == "1990-01-01"
        assert not any(field.label == "Translator" for field in book.display_fields)

    def test_translator_parser_ignores_unlabelled_biography_mentions(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            "<dl><dt>Tłumacz:</dt><dd>Danuta Stok</dd></dl>",
            "<p>Autor wspomina o tłumaczeniu i tłumaczach w swojej biografii.</p>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert not any(field.label == "Translator" for field in book.display_fields)

    def test_get_book_parses_reference_genres_and_tags(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            '"genre": ["Fantasy", "Opowiadania"],',
            "",
        ).replace(
            "<h1>Ostatnie życzenie</h1>",
            '<div class="book__category d-sm-block d-none">'
            "Fantasy, Opowiadania</div>"
            '<a href="/ksiazki/t/dark-fantasy">Dark fantasy</a>'
            '<a href="/ksiazki/t/magia">Magia</a>'
            "<h1>Ostatnie życzenie</h1>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )

        assert book is not None
        assert book.genres == ["Fantasy", "Opowiadania"]
        assert ("Tags", "Dark fantasy, Magia") in [
            (field.label, field.value) for field in book.display_fields
        ]

    def test_get_book_uses_publication_date_detail_as_year_fallback(self):
        get_metadata_cache().clear()
        html = BOOK_HTML.replace(
            chr(34) + "datePublished" + chr(34) + ": " + chr(34) + "2014" + chr(34) + ",", ""
        )
        html = html.replace(
            "</dl>",
            "<dt>Data wydania:</dt><dd>2014-01-01</dd><dt>Data 1. wyd. pol.:</dt><dd>1993-01-01</dd></dl>",
        )
        book = _provider(_Session({"/ksiazka/123456/wiedzmin-ostatnie-zyczenie": html})).get_book(
            "123456--wiedzmin-ostatnie-zyczenie"
        )
        assert book is not None
        assert book.publish_year == 2014


class TestValidIsbn:
    def test_accepts_isbn10_and_isbn13_with_separators(self):
        assert _valid_isbn("83-7578-063-4") == "8375780634"
        assert _valid_isbn("978-83-7578-063-5") == "9788375780635"

    def test_rejects_other_numeric_values(self):
        assert _valid_isbn("2024") is None

    def test_rejects_invalid_checksums(self):
        assert _valid_isbn("9788375780636") is None
        assert _valid_isbn("8375780635") is None

    def test_accepts_isbn10_with_x_check_digit(self):
        assert _valid_isbn("080442957X") == "080442957X"

import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import MetadataSearchOptions, SearchType
from shelfmark.metadata_providers.moly import MolyProvider, _valid_isbn

SEARCH_HTML = """
<html><body>
<div id="content">
  <div class="search_area">
    <div class="book_with_shop">
      <a href="/konyvek/mocsidzuki-mai-a-telihold-kavezo">
        <img alt="Mocsidzuki Mai: A Telihold kávézó" class="tooltip"
             src="https://assets.moly.hu/system/covers/normal/covers_864003.jpg" />
      </a>
    </div>
    <p>
      <a class="book_selector" data-id="1"
         href="/konyvek/mocsidzuki-mai-a-telihold-kavezo">Mocsidzuki Mai:
        <strong class="highlight">A Telihold</strong> kávézó</a>
      <span class="like_count">82%</span>
      <a rel="modal" class="action" href="/sorozatok/a-telihold-kavezo">(A Telihold kávézó 1.)</a>
    </p>
    <p>
      <a class="book_selector" data-id="2"
         href="/konyvek/mocsidzuki-mai-a-telihold-kavezo">Mocsidzuki Mai: A Telihold kávézó</a>
    </p>
    <p>
      <a class="book_selector" data-id="3"
         href="/konyvek/mocsidzuki-mai-az-igazi-kivansag">Mocsidzuki Mai: Az igazi kívánság</a>
    </p>
    <p>
      <a class="book_selector" data-id="4"
         href="/konyvek/lisa-jewell-a-fold-nyelte-el">Lis<strong class="highlight">a</strong> Jew<strong class="highlight">el</strong>l: <strong class="highlight">A föld nyelte el</strong></a>
    </p>
    <p><a class="book_selector" data-id="5" href="/konyvek/broken">Not a book row</a></p>
  </div>
</div>
</body></html>
"""

BOOK_HTML = """
<html><body>
<div id="content">
  <div class="head_title">
    <h1><span class="item">A ​Telihold kávézó
      <a href="/sorozatok/a-telihold-kavezo">(A Telihold kávézó 1.)</a></span></h1>
    <div class="rating"><span class="like_count">82%</span></div>
  </div>
  <div class="authors"><a href="/alkotok/mocsidzuki-mai">Mocsidzuki Mai</a></div>
  <div class="coverbox">
    <a rel="light" class="zoom" href="/system/covers/big/covers_864003.jpg?1712583436"></a>
  </div>
  <div id="full_description">
    <p>Vigyázat! Cselekményleírást tartalmaz.</p>
    <p>A japánok úgy tartják, hogy ha gondoskodsz egy macskáról,
       az egy napon meghálálja.</p>
  </div>
  <div class="items">
    <div class="edition">
      <div><a href="/kiadok/athenaeum">Athenaeum</a>, Budapest, <abbr>2024</abbr></div>
      <div>180 oldal · ISBN: 9789635434435 · Fordította: Nagy Anita</div>
    </div>
  </div>
  <div id="book_tags">
    <a class="tag" href="/cimkek/asztrologia">asztrológia</a>
    <a class="tag" href="/cimkek/japan">japán</a>
  </div>
</div>
</body></html>
"""


class _MolyResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


class _MolySession:
    """Serves canned HTML per URL substring."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = 0
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls += 1
        for fragment, html in self.pages.items():
            if fragment in url:
                return _MolyResponse(html)
        raise requests.HTTPError(f"unexpected URL: {url}")


class _FlakyMolySession(_MolySession):
    def get(self, url, **kwargs):
        if self.calls == 0:
            self.calls += 1
            raise requests.Timeout
        return super().get(url, **kwargs)


def _provider(session):
    provider = MolyProvider()
    provider.session = session
    return provider


class TestMolySearch:
    def test_search_parses_results(self):
        get_metadata_cache().clear()
        provider = _provider(_MolySession({"/kereses": SEARCH_HTML}))

        books = provider.search(MetadataSearchOptions(query="telihold kávézó"))

        assert len(books) == 3  # duplicate slug deduped, colon-less row skipped

        # Mid-word <strong class="highlight"> wrapping must not split words
        highlighted = books[2]
        assert highlighted.title == "A föld nyelte el"
        assert highlighted.authors == ["Lisa Jewell"]
        book = books[0]
        assert book.provider == "moly"
        assert book.provider_id == "mocsidzuki-mai-a-telihold-kavezo"
        assert book.title == "A Telihold kávézó"
        assert book.authors == ["Mocsidzuki Mai"]
        assert book.cover_url == "https://assets.moly.hu/system/covers/normal/covers_864003.jpg"
        assert book.source_url == "https://moly.hu/konyvek/mocsidzuki-mai-a-telihold-kavezo"
        labels = {f.label: f.value for f in book.display_fields}
        assert labels["Rating"] == "82%"
        assert labels["Series"] == "A Telihold kávézó 1."

    def test_search_second_page_is_empty(self):
        provider = _provider(_MolySession({"/kereses": SEARCH_HTML}))

        assert provider.search(MetadataSearchOptions(query="telihold", page=2)) == []

    def test_search_does_not_cache_request_failures(self):
        get_metadata_cache().clear()
        session = _FlakyMolySession({"/kereses": SEARCH_HTML})
        provider = _provider(session)
        options = MetadataSearchOptions(query="telihold kávézó")

        assert provider.search(options) == []

        books = provider.search(options)

        assert session.calls == 2
        assert [book.provider_id for book in books] == [
            "mocsidzuki-mai-a-telihold-kavezo",
            "mocsidzuki-mai-az-igazi-kivansag",
            "lisa-jewell-a-fold-nyelte-el",
        ]


class TestMolyGetBook:
    def test_get_book_parses_details(self):
        get_metadata_cache().clear()
        provider = _provider(_MolySession({"/konyvek/": BOOK_HTML}))

        book = provider.get_book("mocsidzuki-mai-a-telihold-kavezo")

        assert book is not None
        # Zero-width space stripped, nested series link excluded from title
        assert book.title == "A Telihold kávézó"
        assert book.authors == ["Mocsidzuki Mai"]
        assert book.isbn_13 == "9789635434435"
        assert book.publisher == "Athenaeum"
        assert book.publish_year == 2024
        assert book.language == "hu"
        assert book.cover_url == "https://moly.hu/system/covers/big/covers_864003.jpg?1712583436"
        assert "asztrológia" in book.genres
        # Spoiler warning stripped from the description
        assert book.description is not None
        assert not book.description.startswith("Vigyázat!")
        assert "japánok" in book.description

    def test_isbn_search_resolves_first_result(self):
        get_metadata_cache().clear()
        provider = _provider(_MolySession({"/kereses": SEARCH_HTML, "/konyvek/": BOOK_HTML}))

        book = provider.search(
            MetadataSearchOptions(query="9789635434435", search_type=SearchType.ISBN)
        )[0]

        assert book.provider_id == "mocsidzuki-mai-a-telihold-kavezo"
        assert book.isbn_13 == "9789635434435"


class TestValidIsbn:
    def test_isbn_13(self):
        assert _valid_isbn("978-963-543-443-5") == "9789635434435"

    def test_isbn_10_with_check_x(self):
        assert _valid_isbn("963-543-443-X") == "963543443X"

    def test_rejects_page_counts_and_years(self):
        assert _valid_isbn("2024") is None
        assert _valid_isbn("1234567") is None

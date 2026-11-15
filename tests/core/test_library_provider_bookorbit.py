"""Tests for the BookOrbit library provider."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import requests

from shelfmark.core import library_index
from shelfmark.core.library_providers import all_providers, bookorbit
from shelfmark.metadata_providers import BookMetadata

if TYPE_CHECKING:
    from tests.core.fakes import FakeConfig

pytestmark = pytest.mark.usefixtures("fake_app_config")

_URL = "http://bookorbit:3000"


def _file(file_format: str | None, role: str = "content", **extra: Any) -> dict[str, Any]:
    return {"id": 1, "format": file_format, "role": role, "sizeBytes": 1, **extra}


def _card(
    title: str | None = "Dune",
    authors: list[str] | None = None,
    *,
    files: list[dict[str, Any]] | None = None,
    status: str = "present",
    **fields: Any,
) -> dict[str, Any]:
    """A book card as ``POST /api/v1/books/query`` lists it, cut down to what is read."""
    return {
        "id": 1,
        "status": status,
        "title": title,
        "authors": ["Frank Herbert"] if authors is None else authors,
        "seriesName": None,
        "seriesMemberships": [],
        "files": [_file("epub")] if files is None else files,
        "subtitle": None,
        "isbn13": None,
        **fields,
    }


def _searching(title: str, author: str = "Frank Herbert", **fields: Any) -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="1",
        title=title,
        authors=[author],
        search_title=title,
        search_author=author,
        **fields,
    )


def _owned(card: dict[str, Any], title: str, author: str = "Frank Herbert") -> str | None:
    return library_index.match_entries(_searching(title, author), [bookorbit._card_to_entry(card)])


class _Response:
    def __init__(self, payload: Any, status_code: int) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            msg = f"{self.status_code} Client Error"
            raise requests.HTTPError(msg)

    def json(self) -> Any:
        return self.payload


class _Server:
    """Answers the provider's two calls as BookOrbit does, and records them.

    ``cards`` are served in pages of ``bookorbit._PAGE_SIZE`` (patched to 2), and a query
    is answered with 201, like the real server. ``pages`` replaces the answer for the page
    numbers it holds, and ``status_code`` replaces the status of every answer.
    """

    def __init__(self) -> None:
        self.cards: list[dict[str, Any]] = []
        self.libraries: Any = [{"id": 1, "name": "Books"}]
        self.pages: dict[int, Any] = {}
        self.status_code: int | None = None
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, url: str, *, timeout: float) -> _Response:
        self.gets.append(url)
        return _Response(self.libraries, self.status_code or 200)

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> _Response:
        self.posts.append((url, json))
        page, size = json["pagination"]["page"], json["pagination"]["size"]
        if page in self.pages:
            return _Response(self.pages[page], self.status_code or 201)
        payload = {
            "items": self.cards[page * size : (page + 1) * size],
            "total": len(self.cards),
            "page": page,
            "size": size,
        }
        return _Response(payload, self.status_code or 201)


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> _Server:
    fake = _Server()
    monkeypatch.setattr(bookorbit, "_session", lambda url, key: fake)
    monkeypatch.setattr(bookorbit, "_PAGE_SIZE", 2)
    return fake


@pytest.fixture
def configured(fake_app_config: FakeConfig) -> FakeConfig:
    """Both formats on, with a URL and a key."""
    fake_app_config.values.update(
        LIBRARY_CHECK_BOOKORBIT_EBOOK_ENABLED=True,
        LIBRARY_CHECK_BOOKORBIT_AUDIOBOOK_ENABLED=True,
        BOOKORBIT_URL=_URL,
        BOOKORBIT_API_KEY="bok_test",
    )
    return fake_app_config


def _ebooks() -> list[Any]:
    return bookorbit.BookOrbitEbookLibrary().fetch_entries()


# Mapping a card


def test_a_card_keeps_its_title_words_apart_from_authors_series_and_subtitle() -> None:
    entry = bookorbit._card_to_entry(
        _card(
            "Kiss the Girls",
            ["James Patterson", "Andrew Gross"],
            seriesName="Alex Cross",
            seriesMemberships=[{"seriesName": "Alex Cross"}, {"seriesName": "Patterson Shorts"}],
            subtitle="A Detective Story",
        )
    )

    assert entry.title_tokens == {"kiss", "the", "girls"}
    assert entry.context_tokens == {
        "james",
        "patterson",
        "andrew",
        "gross",
        "alex",
        "cross",
        "shorts",
        "a",
        "detective",
        "story",
    }
    assert entry.tokens == entry.title_tokens | entry.context_tokens


def test_a_hyphenated_isbn13_is_normalized() -> None:
    entry = bookorbit._card_to_entry(_card(isbn13="978-0-593-82024-7"))

    assert entry.isbns == {"9780593820247"}


def test_an_empty_card_maps_to_an_empty_entry() -> None:
    entry = bookorbit._card_to_entry({})

    assert (entry.tokens, entry.isbns, entry.asins, entry.external_ids) == (frozenset(),) * 4


def test_the_hardcover_slug_is_never_an_external_id() -> None:
    # BookOrbit stores Hardcover's slug, which is all digits for a book titled "1984".
    # Read as Shelfmark's numeric Hardcover id it would name an unrelated book.
    entry = bookorbit._card_to_entry(
        _card("Nineteen Eighty-Four", ["George Orwell"], hardcoverId="1984")
    )
    unrelated = BookMetadata(
        provider="hardcover", provider_id="1984", title="Dune Messiah", authors=["Frank Herbert"]
    )

    assert entry.external_ids == frozenset()
    assert library_index.match_entries(unrelated, [entry]) is None


# Which formats a card holds


@pytest.mark.parametrize(
    ("files", "kinds"),
    [
        ([_file("epub")], {"ebook"}),
        ([_file("m4b")], {"audiobook"}),
        ([_file("epub"), _file("mp3"), _file("mp3")], {"ebook", "audiobook"}),
        ([_file("EPUB")], {"ebook"}),
        ([_file("cbz")], {"ebook"}),
        ([_file("epub", role="primary")], {"ebook"}),
        ([_file("epub", mediaOverlay={"available": True, "durationMs": 1000})], {"ebook"}),
        ([_file("m4b"), _file("pdf", role="supplement")], {"audiobook"}),
        ([_file("jpg", role="cover"), _file("opf", role="metadata")], set()),
        ([_file(None)], set()),
        ([], set()),
    ],
    ids=[
        "epub",
        "m4b",
        "both",
        "uppercase-format",
        "comic",
        "primary-role",
        "read-along-epub",
        "supplement-pdf",
        "cover-and-metadata",
        "no-format",
        "no-files",
    ],
)
def test_the_formats_a_card_holds(files: list[dict[str, Any]], kinds: set[str]) -> None:
    assert bookorbit._content_types(_card(files=files)) == kinds


# Missing books


def test_a_missing_book_is_not_owned(configured: FakeConfig, server: _Server) -> None:
    server.cards = [_card("Dune"), _card("Dune Messiah", status="missing")]

    assert [sorted(entry.title_tokens) for entry in _ebooks()] == [["dune"]]


def test_every_book_missing_is_an_unreadable_library_not_an_empty_one(
    configured: FakeConfig, server: _Server
) -> None:
    server.cards = [_card("Dune", status="missing")] * 3

    with pytest.raises(ValueError, match="every BookOrbit book is missing"):
        _ebooks()


def test_one_present_book_on_a_later_page_is_enough(
    configured: FakeConfig, server: _Server
) -> None:
    server.cards = [_card("Dune", status="missing")] * 2 + [_card("Dune Messiah")]

    assert [sorted(entry.title_tokens) for entry in _ebooks()] == [["dune", "messiah"]]


def test_every_book_of_one_format_missing_is_a_real_state(
    configured: FakeConfig, server: _Server
) -> None:
    # The audiobook drive can be gone while the ebooks are fine. That is not an outage of
    # the whole library, so the audiobooks simply read as not owned.
    server.cards = [_card("Dune"), _card("Dune Messiah", files=[_file("m4b")], status="missing")]

    assert bookorbit.BookOrbitAudiobookLibrary().fetch_entries() == []
    assert len(_ebooks()) == 1


# Matching


def test_the_main_title_alone_matches_a_card_with_a_subtitle() -> None:
    card = _card("Carl's Doomsday Scenario", ["Matt Dinniman"], subtitle="A LitRPG Adventure")

    assert _owned(card, "Carl's Doomsday Scenario", "Matt Dinniman") == "owned"
    assert _owned(card, "Carl's Doomsday Scenario: A LitRPG Adventure", "Matt Dinniman") == "owned"


def test_an_isbn10_request_matches_the_isbn13_on_the_card() -> None:
    entry = bookorbit._card_to_entry(_card("Dungeon Crawler Carl", isbn13="9780593820247"))
    book = BookMetadata(
        provider="hardcover",
        provider_id="1",
        title="Unrelated",
        authors=["Nobody"],
        isbn_10="059382024X",
    )

    assert library_index.match_entries(book, [entry]) == "owned"


def test_a_co_author_surname_matches() -> None:
    card = _card("Good Omens", ["Terry Pratchett", "Neil Gaiman"])

    assert _owned(card, "Good Omens", "Neil Gaiman") == "owned"


def test_a_near_miss_title_or_another_author_is_not_owned() -> None:
    assert _owned(_card("Dune Messiah"), "Dune") is None
    assert _owned(_card("Dune"), "Dune", "Someone Else") is None


def test_a_card_without_authors_matches_by_isbn_only() -> None:
    entry = bookorbit._card_to_entry(_card("Dune", [], isbn13="9780441013593"))

    assert library_index.match_entries(_searching("Dune"), [entry]) is None
    assert (
        library_index.match_entries(_searching("Dune", isbn_13="978-0-441-01359-3"), [entry])
        == "owned"
    )


def test_an_abbreviated_series_label_in_the_title_does_not_hide_an_owned_book() -> None:
    hhg = _card(
        "HHG 5: Mostly Harmless",
        ["Douglas Adams"],
        seriesName="The Hitchhiker's Guide to the Galaxy",
    )
    dcc = _card("DCC 7: This Inevitable Ruin", ["Matt Dinniman"], seriesName="Dungeon Crawler Carl")

    assert _owned(hhg, "Mostly Harmless", "Douglas Adams") == "owned"
    assert _owned(dcc, "This Inevitable Ruin", "Matt Dinniman") == "owned"


def test_a_series_membership_alone_also_makes_the_label_the_series() -> None:
    card = _card(
        "DCC 7: This Inevitable Ruin",
        ["Matt Dinniman"],
        seriesMemberships=[{"seriesId": 3, "seriesName": "Dungeon Crawler Carl"}],
    )

    assert _owned(card, "This Inevitable Ruin", "Matt Dinniman") == "owned"


def test_a_series_label_still_does_not_make_a_different_book_match() -> None:
    hhg = _card(
        "HHG 5: Mostly Harmless",
        ["Douglas Adams"],
        seriesName="The Hitchhiker's Guide to the Galaxy",
    )

    assert _owned(hhg, "Harmless", "Douglas Adams") is None
    assert _owned(hhg, "Mostly Harmless", "Someone Else") is None


def test_a_title_prefix_without_a_series_is_left_alone() -> None:
    assert _owned(_card("Messiah 2: Dune"), "Dune") is None


@pytest.mark.parametrize(
    "shelf_title",
    [
        "Dungeon Crawler Carl (Full Cast Edition)",
        "Dungeon Crawler Carl (Dramatized Adaptation)",
        "Dungeon Crawler Carl [GraphicAudio]",
    ],
)
def test_an_edition_marker_in_the_title_is_a_different_edition(shelf_title: str) -> None:
    card = _card(shelf_title, ["Matt Dinniman"])

    assert _owned(card, "Dungeon Crawler Carl", "Matt Dinniman") is None
    # The same edition, asked for by its own name, is on the shelf.
    assert _owned(card, shelf_title, "Matt Dinniman") == "owned"


def test_a_numbered_parenthetical_in_the_title_is_series_metadata() -> None:
    card = _card("Kiss the Girls (Alex Cross Series #11)", ["James Patterson"])

    assert _owned(card, "Kiss the Girls", "James Patterson") == "owned"


def test_an_ebook_only_book_is_owned_as_an_ebook_and_not_as_an_audiobook(
    configured: FakeConfig, server: _Server
) -> None:
    server.cards = [
        _card("Dune"),
        _card("Dungeon Crawler Carl", ["Matt Dinniman"], files=[_file("m4b")]),
    ]

    assert library_index.ownership(_searching("Dune")) == {"ebook": "owned", "audiobook": None}
    assert library_index.ownership(_searching("Dungeon Crawler Carl", "Matt Dinniman")) == {
        "ebook": None,
        "audiobook": "owned",
    }


# Fetching


def test_fetch_reads_every_page_from_page_zero(configured: FakeConfig, server: _Server) -> None:
    server.cards = [_card("One"), _card("Two"), _card("Three")]

    entries = _ebooks()

    assert [sorted(entry.title_tokens) for entry in entries] == [["one"], ["two"], ["three"]]
    assert server.gets == [f"{_URL}/api/v1/libraries"]
    body = {
        "sort": [{"field": "addedAt", "dir": "asc"}],
        "collapseSeries": False,
        "collapseLinked": False,
    }
    # The fake answers every query with 201, as BookOrbit does: any 2xx is a success.
    assert server.posts == [
        (f"{_URL}/api/v1/books/query", {**body, "pagination": {"page": page, "size": 2}})
        for page in (0, 1)
    ]


def test_an_empty_page_before_the_reported_total_fails_the_read(
    configured: FakeConfig, server: _Server
) -> None:
    # Accepted as the whole library, every book it never listed would look unowned.
    server.pages = {0: {"items": [_card("One")], "total": 50}, 1: {"items": [], "total": 50}}

    with pytest.raises(ValueError, match="reports 50 books but listed 1 of them"):
        _ebooks()
    assert len(server.posts) == 2


def test_a_page_short_of_the_reported_total_fails_the_read(
    configured: FakeConfig, server: _Server
) -> None:
    server.pages = {
        0: {"items": [_card("One"), _card("Two")], "total": 4},
        1: {"items": [_card("Three")], "total": 4},
    }

    with pytest.raises(ValueError, match="reports 4 books but listed 3 of them"):
        _ebooks()


def test_a_count_that_drops_during_the_scan_fails_the_read(
    configured: FakeConfig, server: _Server
) -> None:
    # A book deleted between two pages shifts every later one back, so the scan can step
    # over a book it never sees. The drop in the count is the only sign of it.
    server.pages = {
        0: {"items": [_card("One"), _card("Two")], "total": 4},
        1: {"items": [_card("Three")], "total": 3},
    }

    with pytest.raises(ValueError, match="reports 4 books but listed 3 of them"):
        _ebooks()


def test_a_key_that_reaches_no_library_is_an_error(configured: FakeConfig, server: _Server) -> None:
    # BookOrbit answers such a key with empty pages, which would read as an empty library.
    server.libraries = []

    with pytest.raises(ValueError, match="can reach no BookOrbit library"):
        _ebooks()
    assert server.posts == []


def test_a_library_that_reports_books_but_lists_none_is_an_error(
    configured: FakeConfig, server: _Server
) -> None:
    server.pages = {0: {"items": [], "total": 5}}

    with pytest.raises(ValueError, match="reports 5 books but listed 0 of them"):
        _ebooks()


def test_a_library_list_in_an_unexpected_shape_is_an_error(
    configured: FakeConfig, server: _Server
) -> None:
    server.libraries = {"items": [{"id": 1}]}

    with pytest.raises(TypeError, match="library list has an unexpected shape"):
        _ebooks()


@pytest.mark.parametrize(
    "payload",
    [
        {"total": 3},
        {"items": None, "total": 3},
        {"items": [], "total": "3"},
        [1, 2],
        {"items": ["Dune"], "total": 1},
    ],
    ids=["no-items", "null-items", "text-total", "a-list", "not-a-card"],
)
def test_a_book_list_in_an_unexpected_shape_is_an_error(
    configured: FakeConfig, server: _Server, payload: Any
) -> None:
    server.pages = {0: payload}

    with pytest.raises(TypeError, match="book list"):
        _ebooks()


def test_a_really_empty_library_indexes_as_empty(configured: FakeConfig, server: _Server) -> None:
    assert _ebooks() == []


def test_an_unreadable_library_holds_the_sync_when_nothing_was_indexed_before(
    configured: FakeConfig, server: _Server
) -> None:
    server.status_code = 401

    with pytest.raises(requests.HTTPError):
        _ebooks()
    with pytest.raises(library_index.LibraryUnavailableError, match="BookOrbit audiobooks"):
        library_index.require_ready("audiobook")


# Configuration


@pytest.mark.parametrize(
    ("values", "enabled"),
    [
        ({"BOOKORBIT_URL": _URL, "BOOKORBIT_API_KEY": "k"}, True),
        ({"BOOKORBIT_URL": _URL}, False),
        ({"BOOKORBIT_API_KEY": "k"}, False),
    ],
    ids=["configured", "missing-key", "missing-url"],
)
def test_a_provider_is_enabled_only_with_its_toggle_a_url_and_a_key(
    fake_app_config: FakeConfig, values: dict[str, Any], enabled: bool
) -> None:
    fake_app_config.values.update(values)
    ebook, audiobook = bookorbit.BookOrbitEbookLibrary(), bookorbit.BookOrbitAudiobookLibrary()

    assert (ebook.is_enabled(), audiobook.is_enabled()) == (False, False)

    fake_app_config.values["LIBRARY_CHECK_BOOKORBIT_EBOOK_ENABLED"] = True

    assert (ebook.is_enabled(), audiobook.is_enabled()) == (enabled, False)

    fake_app_config.values["LIBRARY_CHECK_BOOKORBIT_AUDIOBOOK_ENABLED"] = True

    assert (ebook.is_enabled(), audiobook.is_enabled()) == (enabled, enabled)


def test_the_names_shown_for_each_format(fake_app_config: FakeConfig) -> None:
    ebook, audiobook = bookorbit.BookOrbitEbookLibrary(), bookorbit.BookOrbitAudiobookLibrary()

    assert (ebook.display_name, audiobook.display_name) == (
        "BookOrbit (ebooks)",
        "BookOrbit (audiobooks)",
    )
    assert (ebook.describe(), audiobook.describe()) == ("BookOrbit", "BookOrbit")

    fake_app_config.values["BOOKORBIT_URL"] = _URL

    assert ebook.describe() == f"BookOrbit ebooks at {_URL}"
    assert audiobook.describe() == f"BookOrbit audiobooks at {_URL}"


def test_the_fingerprint_names_the_server_and_the_key_without_holding_the_key(
    fake_app_config: FakeConfig,
) -> None:
    fake_app_config.values.update(BOOKORBIT_URL=_URL, BOOKORBIT_API_KEY="bok_secret")
    token = bookorbit.BookOrbitEbookLibrary().fingerprint()

    assert token == bookorbit.BookOrbitEbookLibrary().fingerprint()
    assert "bok_secret" not in str(token)
    assert (
        bookorbit.BookOrbitEbookLibrary({"BOOKORBIT_API_KEY": "bok_other"}).fingerprint() != token
    )
    assert (
        bookorbit.BookOrbitEbookLibrary({"BOOKORBIT_URL": "http://other:3000"}).fingerprint()
        != token
    )


# The Test button and the saved library


def _two_servers(monkeypatch: pytest.MonkeyPatch) -> dict[str, _Server]:
    servers = {_URL: _Server(), "http://other:3000": _Server()}
    monkeypatch.setattr(bookorbit, "_session", lambda url, key: servers[url])
    monkeypatch.setattr(bookorbit, "_PAGE_SIZE", 2)
    return servers


def test_a_test_with_unsaved_settings_does_not_answer_for_the_saved_library(
    configured: FakeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Test button keeps what it indexed under the provider's name, where the saved
    # settings' answers are cached too. Another server, tested and never saved, must not
    # stand in for the saved one.
    servers = _two_servers(monkeypatch)
    servers[_URL].cards = [_card("Dune")]
    form = {"BOOKORBIT_URL": "http://other:3000", "BOOKORBIT_API_KEY": "bok_other"}

    assert library_index.test_connection("bookorbit_ebook", form)["success"] is True
    assert library_index.is_in_library(_searching("Dune"), "ebook") is True


def test_testing_the_saved_settings_still_primes_the_index(
    configured: FakeConfig, server: _Server
) -> None:
    server.cards = [_card("Dune")]

    assert library_index.test_connection("bookorbit_ebook", None)["success"] is True
    assert library_index.is_in_library(_searching("Dune"), "ebook") is True
    assert len(server.posts) == 1


def test_a_saved_change_of_server_reindexes_at_the_next_lookup(
    configured: FakeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    servers = _two_servers(monkeypatch)
    servers[_URL].cards = [_card("Dune")]
    servers["http://other:3000"].cards = [_card("Dune Messiah")]

    assert library_index.is_in_library(_searching("Dune"), "ebook") is True

    configured.values["BOOKORBIT_URL"] = "http://other:3000"

    assert library_index.is_in_library(_searching("Dune Messiah"), "ebook") is True
    assert library_index.is_in_library(_searching("Dune"), "ebook") is False


def test_fetch_entries_requires_the_url_and_the_key(fake_app_config: FakeConfig) -> None:
    fake_app_config.values["BOOKORBIT_URL"] = _URL

    with pytest.raises(ValueError, match="URL and API key"):
        _ebooks()


@pytest.mark.parametrize(
    "pasted",
    [
        ' "bookorbit.lan:3000/" ',
        "http://bookorbit.lan:3000/api/v1",
        "http://bookorbit.lan:3000/api/v1/",
    ],
)
def test_the_configured_url_is_normalized(fake_app_config: FakeConfig, pasted: str) -> None:
    fake_app_config.values["BOOKORBIT_URL"] = pasted

    assert (
        bookorbit.BookOrbitEbookLibrary().describe()
        == "BookOrbit ebooks at http://bookorbit.lan:3000"
    )


def test_unsaved_form_values_override_the_saved_settings(fake_app_config: FakeConfig) -> None:
    fake_app_config.values.update(BOOKORBIT_URL="http://saved:3000", BOOKORBIT_API_KEY="saved")

    provider = bookorbit.BookOrbitAudiobookLibrary({"BOOKORBIT_URL": "http://form:3000"})

    assert provider.describe() == "BookOrbit audiobooks at http://form:3000"


def test_the_key_is_sent_as_a_bearer_token() -> None:
    assert bookorbit._session(_URL, "bok_abc").headers["Authorization"] == "Bearer bok_abc"


@pytest.mark.parametrize(
    ("mode", "url", "verify"),
    [
        ("disabled_local", "https://10.0.0.91:3000", False),
        ("disabled_local", "https://bookorbit.example.com", True),
        ("enabled", "https://10.0.0.91:3000", True),
    ],
)
def test_the_session_honours_certificate_validation_for_the_server_url(
    fake_app_config: FakeConfig, mode: str, url: str, verify: bool
) -> None:
    fake_app_config.values["CERTIFICATE_VALIDATION"] = mode

    assert bookorbit._session(url, "k").verify is verify


def test_both_formats_are_registered_after_audiobookshelf() -> None:
    assert [(provider.name, provider.content_types) for provider in all_providers()] == [
        ("calibre", {"ebook"}),
        ("audiobookshelf", {"audiobook"}),
        ("bookorbit_ebook", {"ebook"}),
        ("bookorbit_audiobook", {"audiobook"}),
    ]

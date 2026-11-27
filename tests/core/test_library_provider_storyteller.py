"""Tests for the Storyteller library provider."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core import library_index
from shelfmark.core.library_providers import storyteller
from shelfmark.metadata_providers import BookMetadata

pytestmark = pytest.mark.usefixtures("config")

_CONFIGURED = {
    "LIBRARY_CHECK_STORYTELLER_ENABLED": True,
    "STORYTELLER_URL": "http://storyteller:8001/",
    "STORYTELLER_USERNAME": "shelfmark",
    "STORYTELLER_PASSWORD": "secret",
}


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Settings the provider reads, starting empty, and no remembered sign-in."""
    values: dict[str, Any] = {}
    monkeypatch.setattr(
        storyteller.app_config,
        "get",
        lambda key, default=None, user_id=None: values.get(key, default),
    )
    monkeypatch.setattr(library_index, "_cache", {})
    monkeypatch.setattr(storyteller, "_tokens", {})
    return values


def _book(
    title: str,
    authors: tuple[str, ...] = ("Frank Herbert",),
    *,
    ebook: bool | None = True,
    audiobook: bool | None = None,
    readaloud: str | None = None,
    identifiers: list[dict[str, str]] | None = None,
    series: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A book as ``GET /api/v2/books`` lists it; ``False`` is a slot whose file is missing."""

    def slot(present: bool | None) -> dict[str, Any] | None:
        if present is None:
            return None
        return {"filepath": "/data/library/x", "missing": 0 if present else 1, "identifiers": []}

    return {
        "title": title,
        "authors": [{"name": name} for name in authors],
        "series": [{"name": name} for name in series],
        "identifiers": identifiers or [],
        "ebook": slot(ebook),
        "audiobook": slot(audiobook),
        "readaloud": (
            None
            if readaloud is None
            else {"filepath": "/data/assets/x.epub" if readaloud == "done" else None, "missing": 0}
        ),
    }


def _searching(
    title: str, author: str = "Frank Herbert", *, provider_id: str = "1", isbn_10: str | None = None
) -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id=provider_id,
        title=title,
        authors=[author],
        search_title=title,
        search_author=author,
        isbn_10=isbn_10,
    )


class _Response:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self.payload = payload
        self.is_redirect = status_code in (301, 302, 307, 308)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self.payload


class _Server:
    """A Storyteller stand-in: a sign-in endpoint and the book list."""

    def __init__(self, books: Any, *, password: str = "secret") -> None:
        self.books = books
        self.password = password
        self.valid_tokens: set[str] = set()
        self.sign_ins = 0
        self.list_status = 200
        self.calls: list[str] = []

    def post(
        self, url: str, *, data: dict[str, str], timeout: int, allow_redirects: bool
    ) -> _Response:
        assert not allow_redirects
        assert data["usernameOrEmail"] == "shelfmark"
        self.calls.append(f"POST {url}")
        if data["password"] != self.password:
            return _Response(401)
        self.sign_ins += 1
        token = f"token-{self.sign_ins}"
        self.valid_tokens.add(token)
        return _Response(200, {"access_token": token, "expires_in": 3_600_000})

    def get(
        self, url: str, *, headers: dict[str, str], timeout: int, allow_redirects: bool
    ) -> _Response:
        assert not allow_redirects
        self.calls.append(f"GET {url}")
        token = headers["Authorization"].removeprefix("Bearer ")
        if token not in self.valid_tokens:
            return _Response(401)
        return _Response(self.list_status, self.books)


def _install(monkeypatch: pytest.MonkeyPatch, server: _Server) -> None:
    monkeypatch.setattr(storyteller, "_session", lambda: server)


def test_each_format_lists_only_the_books_that_hold_it(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    config.update(_CONFIGURED)
    _install(
        monkeypatch,
        _Server(
            [
                _book("Dune", ebook=True, audiobook=None),
                _book("Dune Messiah", ebook=None, audiobook=True),
                _book("Children of Dune", ebook=True, audiobook=True),
            ]
        ),
    )

    ebooks = storyteller.StorytellerLibrary("ebook").fetch_entries()
    audiobooks = storyteller.StorytellerLibrary("audiobook").fetch_entries()

    assert library_index.match_entries(_searching("Dune"), ebooks) == "owned"
    assert library_index.match_entries(_searching("Dune"), audiobooks) is None
    assert library_index.match_entries(_searching("Dune Messiah"), audiobooks) == "owned"
    assert library_index.match_entries(_searching("Dune Messiah"), ebooks) is None
    assert library_index.match_entries(_searching("Children of Dune"), ebooks) == "owned"
    assert library_index.match_entries(_searching("Children of Dune"), audiobooks) == "owned"


def test_a_missing_file_does_not_count_as_owned() -> None:
    book = _book("Dune", ebook=False, audiobook=False)

    assert storyteller.holds(book, "ebook") is False
    assert storyteller.holds(book, "audiobook") is False


def test_a_finished_readaloud_counts_for_both_formats() -> None:
    finished = _book("Dune", ebook=None, readaloud="done")
    queued = _book("Dune", ebook=None, readaloud="queued")

    assert storyteller.holds(finished, "ebook") is True
    assert storyteller.holds(finished, "audiobook") is True
    assert storyteller.holds(queued, "ebook") is False
    assert storyteller.holds(queued, "audiobook") is False


def test_identifiers_match_before_the_title() -> None:
    entry = storyteller.book_to_entry(
        _book(
            "Dune (Deluxe Edition)",
            identifiers=[
                {"kind": "isbn-13", "value": "978-0-441-17271-9"},
                {"kind": "audible", "value": " b002v1odyi "},
                {"kind": "hardcover-book-id", "value": "312460"},
                {"kind": "hardcover-book-slug", "value": "dune"},
            ],
        )
    )

    assert entry.isbns == {"9780441172719"}
    assert entry.asins == {"B002V1ODYI"}
    assert entry.external_ids == {("hardcover", "312460")}
    assert library_index.match_entries(_searching("Dune", provider_id="312460"), [entry]) == "owned"
    assert (
        library_index.match_entries(_searching("Different title", isbn_10="0441172717"), [entry])
        == "owned"
    )


def test_format_identifiers_count_too() -> None:
    book = _book("Dune")
    book["ebook"]["identifiers"] = [{"kind": "isbn-13", "value": "9780441172719"}]

    assert storyteller.book_to_entry(book).isbns == {"9780441172719"}


def test_the_title_rule_still_tells_sequels_and_other_authors_apart() -> None:
    entry = storyteller.book_to_entry(_book("Dune Messiah"))

    assert library_index.match_entries(_searching("Dune"), [entry]) is None
    assert library_index.match_entries(_searching("Dune Messiah", "Someone Else"), [entry]) is None


def test_series_words_are_context_not_another_work() -> None:
    entry = storyteller.book_to_entry(_book("Dune Messiah", series=("Dune",)))

    assert library_index.match_entries(_searching("Dune Messiah: Dune"), [entry]) == "owned"


def test_book_to_entry_tolerates_missing_metadata() -> None:
    entry = storyteller.book_to_entry({})

    assert (entry.tokens, entry.isbns, entry.asins) == (frozenset(), frozenset(), frozenset())


def test_one_sign_in_serves_both_formats_and_later_reads(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    config.update(_CONFIGURED)
    server = _Server([_book("Dune", audiobook=True)])
    _install(monkeypatch, server)

    storyteller.StorytellerLibrary("ebook").fetch_entries()
    storyteller.StorytellerLibrary("audiobook").fetch_entries()

    assert server.sign_ins == 1
    assert server.calls == [
        "POST http://storyteller:8001/api/v2/token",
        "GET http://storyteller:8001/api/v2/books",
        "GET http://storyteller:8001/api/v2/books",
    ]


def test_an_ended_token_signs_in_again_once(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    config.update(_CONFIGURED)
    server = _Server([_book("Dune")])
    _install(monkeypatch, server)
    storyteller.StorytellerLibrary("ebook").fetch_entries()
    server.valid_tokens.clear()  # Storyteller restarted, or the session was signed out.

    assert len(storyteller.StorytellerLibrary("ebook").fetch_entries()) == 1
    assert server.sign_ins == 2


def test_a_wrong_password_is_an_error(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    config.update(_CONFIGURED, STORYTELLER_PASSWORD="wrong")
    _install(monkeypatch, _Server([_book("Dune")]))

    with pytest.raises(PermissionError, match="username or password"):
        storyteller.StorytellerLibrary("ebook").fetch_entries()


def test_a_user_without_the_list_permission_is_an_error(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    config.update(_CONFIGURED)
    server = _Server([_book("Dune")])
    server.list_status = 403
    _install(monkeypatch, server)

    with pytest.raises(PermissionError, match="permission to list books"):
        storyteller.StorytellerLibrary("ebook").fetch_entries()


@pytest.mark.parametrize("payload", [{"books": []}, None, "Not found", [_book("Dune"), None]])
def test_a_book_list_in_an_unexpected_shape_is_an_error(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    config.update(_CONFIGURED)
    _install(monkeypatch, _Server(payload))

    with pytest.raises(TypeError, match="unexpected shape"):
        storyteller.StorytellerLibrary("ebook").fetch_entries()


def test_an_unreachable_storyteller_holds_automatic_downloads(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed: with no earlier index, the strict check refuses to call a book missing."""
    config.update(_CONFIGURED)

    def _down() -> Any:
        raise ConnectionError("connection refused")

    monkeypatch.setattr(storyteller, "_session", _down)

    with pytest.raises(library_index.LibraryUnavailableError):
        library_index.require_ready("ebook")


def test_providers_are_disabled_by_default() -> None:
    for content_type in ("ebook", "audiobook"):
        provider = storyteller.StorytellerLibrary(content_type)
        assert provider.is_enabled() is False
        assert provider.content_types == {content_type}


@pytest.mark.parametrize(
    "missing_key", ["STORYTELLER_URL", "STORYTELLER_USERNAME", "STORYTELLER_PASSWORD"]
)
def test_incomplete_configuration_holds_automatic_downloads(
    config: dict[str, Any], missing_key: str
) -> None:
    config.update(_CONFIGURED)
    del config[missing_key]

    with pytest.raises(library_index.LibraryUnavailableError, match="URL, username and password"):
        library_index.require_ready("ebook")


def test_settings_action_checks_unsaved_values_without_changing_library_index(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelfmark.config.settings import _test_storyteller_library

    config.update(_CONFIGURED)
    saved = _Server([_book("Dune")])
    _install(monkeypatch, saved)
    assert library_index.is_in_library(_searching("Dune"), "ebook", strict=True)

    unsaved = _Server([_book("Other", ebook=False, audiobook=True)])
    _install(monkeypatch, unsaved)
    result = _test_storyteller_library({"STORYTELLER_URL": "http://other-storyteller:8001"})

    assert result == {"success": True, "message": "Storyteller: 0 ebooks, 1 audiobooks."}
    assert unsaved.calls == [
        "POST http://other-storyteller:8001/api/v2/token",
        "GET http://other-storyteller:8001/api/v2/books",
    ]
    assert library_index.is_in_library(_searching("Dune"), "ebook", strict=True)


def test_settings_action_checks_a_new_password_even_with_a_cached_session(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from shelfmark.config.settings import _test_storyteller_library

    config.update(_CONFIGURED)
    _install(monkeypatch, _Server([_book("Dune")]))
    assert _test_storyteller_library()["success"]

    assert _test_storyteller_library({"STORYTELLER_PASSWORD": "wrong"}) == {
        "success": False,
        "message": "Storyteller: Storyteller rejected the username or password",
    }


def test_fetch_entries_requires_url_and_credentials() -> None:
    provider = storyteller.StorytellerLibrary("ebook")

    assert provider.describe() == "Storyteller (ebooks)"
    with pytest.raises(ValueError, match="URL, username and password"):
        provider.fetch_entries()


def test_both_formats_are_registered_as_providers() -> None:
    from shelfmark.core.library_providers import all_providers

    names = {provider.name: provider.content_types for provider in all_providers()}

    assert names["storyteller_ebook"] == {"ebook"}
    assert names["storyteller_audiobook"] == {"audiobook"}

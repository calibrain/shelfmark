"""BookOrbit library provider.

Reads a BookOrbit server (3.8.0 or later, the first with API keys) over its REST API:
``GET /api/v1/libraries`` to check that the key reaches a library, then
``POST /api/v1/books/query`` page by page. Both are allowed for read-only keys.

One BookOrbit library holds ebooks and audiobooks, often both for the same book, while a
``LibraryEntry`` carries no format. So the module offers one provider per format, and each
indexes only the books that hold a file of its format.
"""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.library_providers import LibraryEntry, setting
from shelfmark.core.text_match import isbn_variants, tokens
from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    import requests

_TIMEOUT_SECONDS = 15
_PAGE_SIZE = 200  # The server's maximum page size; a larger one is rejected.

_AUDIOBOOK_FORMATS = frozenset({"m4b", "mp3", "m4a", "opus", "ogg", "flac"})
# Comics count as ebooks, as they do in Shelfmark's own ebook formats.
_EBOOK_FORMATS = frozenset(
    {"epub", "kepub", "pdf", "mobi", "azw3", "azw", "fb2", "djvu", "cbz", "cbr", "cb7"}
)
# The roles of the files that are the book itself. Covers, metadata sidecars and
# supplements (a PDF or a video that comes with an audiobook) do not make it owned.
_CONTENT_ROLES = frozenset({"content", "primary"})

_QUERY_BODY: dict[str, Any] = {
    "sort": [{"field": "addedAt", "dir": "asc"}],
    "collapseSeries": False,
    "collapseLinked": False,
}

# The title rules of the Calibre provider: a numbered parenthetical ("(Alex Cross #11)") is
# series metadata, and a leading "Label NN:" is the series label when the book is in one.
_NUMBERED_PARENTHETICAL = re.compile(r"[(\[][^)\]]*\d[^)\]]*[)\]]")
_SERIES_LABEL = re.compile(r"^\s*(?P<label>[^:]{1,40}?)\s+\d+(?:\.\d+)?\s*:\s+\S")


def _config(overrides: Mapping[str, Any] | None = None) -> tuple[str, str]:
    url = normalize_http_url(str(setting(app_config, "BOOKORBIT_URL", "", overrides) or ""))
    # The API lives under /api/v1, which is what people see and paste.
    if url.lower().endswith("/api/v1"):
        url = url[: -len("/api/v1")]
    key = str(setting(app_config, "BOOKORBIT_API_KEY", "", overrides) or "").strip()
    return url, key


def _session(url: str, key: str) -> requests.Session:
    """A session for one BookOrbit server, signed in with its API key.

    The proxy and certificate rules come from ``NetworkSession`` per request, so a LAN
    server goes direct and ``CERTIFICATE_VALIDATION=disabled_local`` covers it.
    """
    from shelfmark.download.network import NetworkSession

    del url  # the rules read each request's own URL
    session = NetworkSession()
    # BookOrbit also takes X-Api-Key, but requests drops only Authorization when a
    # redirect leads to another host.
    session.headers.update({"Authorization": f"Bearer {key}"})
    return session


def _fetch_cards(url: str, key: str) -> list[dict[str, Any]]:
    """Every book card the key can see, ``missing`` ones included."""
    session = _session(url, key)

    resp = session.get(f"{url}/api/v1/libraries", timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    libraries = resp.json()
    if not isinstance(libraries, list):
        msg = "BookOrbit's library list has an unexpected shape"
        raise TypeError(msg)
    if not libraries:
        # Such a key gets empty pages and no error, which would read as an empty library.
        msg = "the API key can reach no BookOrbit library"
        raise ValueError(msg)

    cards: list[dict[str, Any]] = []
    reported = 0
    page = 0
    while True:
        resp = session.post(
            f"{url}/api/v1/books/query",
            json={**_QUERY_BODY, "pagination": {"page": page, "size": _PAGE_SIZE}},
            timeout=_TIMEOUT_SECONDS,
        )
        # The server answers this POST with 201, so any 2xx is a success.
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") if isinstance(data, dict) else None
        total = data.get("total") if isinstance(data, dict) else None
        if not isinstance(items, list) or not isinstance(total, int):
            msg = f"BookOrbit's book list (page {page}) has an unexpected shape"
            raise TypeError(msg)
        if not all(isinstance(card, dict) for card in items):
            msg = f"BookOrbit's book list (page {page}) holds something other than books"
            raise TypeError(msg)
        if total < reported:
            # A book deleted between two pages shifts every later one back by a place,
            # so the scan steps over one it never sees, and a later addition can bring
            # the count back up. A drop on any page is the only sign of it.
            msg = f"BookOrbit's count dropped from {reported} to {total} during the scan"
            raise ValueError(msg)
        reported = total
        cards.extend(items)
        if not items or (page + 1) * _PAGE_SIZE >= total:
            break
        page += 1

    # A scan that ends short of the count the server reported (an empty or short page
    # before the end) is not the whole library: taken as one, every book it never listed
    # would look unowned and be requested again.
    if len(cards) < reported:
        msg = f"BookOrbit reports {reported} books but listed {len(cards)} of them"
        raise ValueError(msg)

    # BookOrbit marks a book missing when its files cannot be found. All of them missing
    # is far more likely unmounted storage than an empty shelf, and indexing that as "owns
    # nothing" would let the sync and automatic downloads fetch everything again.
    if cards and all(card.get("status") == "missing" for card in cards):
        msg = "every BookOrbit book is missing; its storage is probably unavailable"
        raise ValueError(msg)
    return cards


def _content_types(card: Mapping[str, Any]) -> frozenset[str]:
    """The formats a book holds, judged from the files that are the book itself.

    A read-along EPUB, one with the narration attached, is an ebook only: it does not
    play in an audiobook app, so it does not make the book an owned audiobook.
    """
    kinds: set[str] = set()
    for book_file in card.get("files") or ():
        if not isinstance(book_file, dict):
            continue
        if str(book_file.get("role") or "").lower() not in _CONTENT_ROLES:
            continue
        file_format = str(book_file.get("format") or "").lower()
        if file_format in _EBOOK_FORMATS:
            kinds.add("ebook")
        elif file_format in _AUDIOBOOK_FORMATS:
            kinds.add("audiobook")
    return frozenset(kinds)


def _strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [value for value in values if isinstance(value, str) and value.strip()]


def _card_to_entry(card: Mapping[str, Any]) -> LibraryEntry:
    title = str(card.get("title") or "")
    memberships = card.get("seriesMemberships")
    series_names = _strings(
        [card.get("seriesName")]
        + [m.get("seriesName") for m in memberships or () if isinstance(m, dict)]
    )

    title_tok = set(tokens(_NUMBERED_PARENTHETICAL.sub(" ", title)))
    # The subtitle is context, not title: a search for the main title alone must still
    # match, and the subtitle's words must not read as naming a different work.
    context_tok: set[str] = set()
    for text in [*_strings(card.get("authors")), *series_names, str(card.get("subtitle") or "")]:
        context_tok |= set(tokens(text))
    if series_names and (label := _SERIES_LABEL.match(title)):
        context_tok |= set(tokens(label.group("label")))

    # No external id: BookOrbit's hardcoverId is Hardcover's slug, not the numeric id
    # Shelfmark matches on, and an all-digit slug would collide with an unrelated book.
    return LibraryEntry(
        frozenset(title_tok | context_tok),
        isbn_variants(card.get("isbn13")),
        frozenset(),
        frozenset(),
        frozenset(title_tok),
        frozenset(context_tok),
    )


class _BookOrbitLibrary:
    """Ownership of one format via the BookOrbit REST API."""

    name: str
    display_name: str
    content_types: frozenset[str]
    _content_type: str
    _enabled_key: str

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self._overrides = overrides

    def is_enabled(self) -> bool:
        url, key = _config(self._overrides)
        enabled = bool(setting(app_config, self._enabled_key, False, self._overrides))
        return enabled and bool(url) and bool(key)

    def describe(self) -> str:
        url, _key = _config(self._overrides)
        return f"BookOrbit {self._content_type}s at {url}" if url else "BookOrbit"

    def fingerprint(self) -> str:
        # BookOrbit offers no cheap change token for its content, so the token names the
        # server and key instead: a saved change of either re-indexes at the next lookup
        # rather than once the cache expires.
        url, key = _config(self._overrides)
        return hashlib.sha256(f"{url}\n{key}".encode()).hexdigest()

    def fetch_entries(self) -> list[LibraryEntry]:
        url, key = _config(self._overrides)
        if not url or not key:
            msg = "Set the BookOrbit URL and API key first."
            raise ValueError(msg)
        return [
            _card_to_entry(card)
            for card in _fetch_cards(url, key)
            if card.get("status") != "missing" and self._content_type in _content_types(card)
        ]


class BookOrbitEbookLibrary(_BookOrbitLibrary):
    """Ebook ownership via the BookOrbit REST API."""

    name = "bookorbit_ebook"
    display_name = "BookOrbit (ebooks)"
    content_types = frozenset({"ebook"})
    _content_type = "ebook"
    _enabled_key = "LIBRARY_CHECK_BOOKORBIT_EBOOK_ENABLED"


class BookOrbitAudiobookLibrary(_BookOrbitLibrary):
    """Audiobook ownership via the BookOrbit REST API."""

    name = "bookorbit_audiobook"
    display_name = "BookOrbit (audiobooks)"
    content_types = frozenset({"audiobook"})
    _content_type = "audiobook"
    _enabled_key = "LIBRARY_CHECK_BOOKORBIT_AUDIOBOOK_ENABLED"


def check_connection(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The Test button: read both formats with the form's values and count them.

    Both are read whichever toggles are on, since the button checks the URL and the key.
    Unlike `library_index.test_connection`, which the other libraries' buttons use,
    nothing read here is kept. The index is shared with the saved settings, and on a
    failed or emptied read `library_index` falls back to a provider's earlier entries
    whatever their fingerprint, so a server tested but never saved could answer for the
    saved one.
    """
    results: list[tuple[bool, str]] = []
    for provider in (BookOrbitEbookLibrary(overrides), BookOrbitAudiobookLibrary(overrides)):
        try:
            count = len(provider.fetch_entries())
        except Exception as exc:  # noqa: BLE001 - surface any error to the user
            results.append((False, f"{provider.describe()}: {exc}"))
        else:
            results.append((True, f"{provider.describe()}: indexed {count} item(s)"))
    return {
        "success": all(ok for ok, _message in results),
        "message": "; ".join(message for _ok, message in results),
    }

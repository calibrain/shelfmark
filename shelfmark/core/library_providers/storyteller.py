"""Storyteller library provider.

Storyteller keeps one book per title with an ebook, an audiobook and a readaloud slot,
so it answers for both content types: an ebook search is checked against the books
that hold an ebook, an audiobook search against those that hold an audiobook. A book
with only one format therefore still lets the other one be downloaded.

The book list comes from ``GET /api/v2/books``, signed in with a Storyteller user's
name and password through ``POST /api/v2/token`` (the sign-in the Storyteller apps use).
The user needs the "list books" permission and sees the books Storyteller shows it:
books in a private collection count only when the user is a member of it.
"""

from __future__ import annotations

import threading
import time
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.library_providers import LibraryEntry, setting
from shelfmark.core.text_match import isbn_variants, tokens
from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from collections.abc import Mapping

    from requests import Response

    from shelfmark.download.network import NetworkSession

_REQUEST_TIMEOUT = 15
# Sign in again this long before Storyteller says the token ends.
_TOKEN_MARGIN_SECONDS = 60

# Storyteller identifier kind -> what it means here.
_ISBN_KINDS = frozenset({"isbn-13", "isbn-10", "isbn"})
_ASIN_KINDS = frozenset({"asin", "audible"})
# Storyteller identifier kind -> Shelfmark metadata provider name.
_EXTERNAL_ID_KINDS = {"hardcover-book-id": "hardcover"}

_tokens_lock = threading.Lock()
# (url, username) -> (password digest, access token, monotonic expiry)
_tokens: dict[tuple[str, str], tuple[bytes, str, float]] = {}


def _config(overrides: Mapping[str, Any] | None = None) -> tuple[str, str, str]:
    url = normalize_http_url(str(setting(app_config, "STORYTELLER_URL", "", overrides) or ""))
    username = str(setting(app_config, "STORYTELLER_USERNAME", "", overrides) or "").strip()
    password = str(setting(app_config, "STORYTELLER_PASSWORD", "", overrides) or "")
    return url, username, password


def _session() -> NetworkSession:
    from shelfmark.download.network import get_session

    return get_session()


def _token(
    session: NetworkSession, url: str, username: str, password: str, *, rejected: str | None = None
) -> str:
    key = (url, username)
    password_digest = sha256(password.encode()).digest()
    with _tokens_lock:
        cached = _tokens.get(key)
    if cached:
        cached_digest, cached_token, expires_at = cached
        if (
            cached_digest == password_digest
            and cached_token != rejected
            and time.monotonic() < expires_at
        ):
            return cached_token

    resp = session.post(
        f"{url}/api/v2/token",
        data={"usernameOrEmail": username, "password": password},
        timeout=_REQUEST_TIMEOUT,
        allow_redirects=False,
    )
    if resp.status_code == 401:
        raise PermissionError("Storyteller rejected the username or password")
    if resp.is_redirect:
        raise ValueError("Storyteller redirected sign-in. Use its final base URL.")
    resp.raise_for_status()
    payload = resp.json()
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise ValueError("Storyteller's sign-in response has no access token")
    # Storyteller reports expires_in in milliseconds.
    expires_in = float(payload.get("expires_in") or 0) / 1000
    with _tokens_lock:
        _tokens[key] = (
            password_digest,
            token,
            time.monotonic() + expires_in - _TOKEN_MARGIN_SECONDS,
        )
    return token


def _fetch_books(url: str, username: str, password: str) -> list[dict[str, Any]]:
    if not url or not username or not password:
        raise ValueError("Set the Storyteller URL, username and password first.")

    session = _session()

    def request_books(token: str) -> Response:
        return session.get(
            f"{url}/api/v2/books",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_REQUEST_TIMEOUT,
            allow_redirects=False,
        )

    token = _token(session, url, username, password)
    resp = request_books(token)
    if resp.status_code == 401:
        resp = request_books(_token(session, url, username, password, rejected=token))

    if resp.status_code == 403:
        raise PermissionError("The Storyteller user lacks the permission to list books")
    if resp.is_redirect:
        raise ValueError("Storyteller redirected the book list. Use its final base URL.")
    resp.raise_for_status()
    books = resp.json()
    if not isinstance(books, list) or any(not isinstance(book, dict) for book in books):
        raise TypeError("Storyteller's book list has an unexpected shape")
    return books


def _present(slot: object) -> bool:
    """True if a format slot holds a file Storyteller can find."""
    return isinstance(slot, dict) and bool(slot.get("filepath")) and not slot.get("missing")


def holds(book: dict[str, Any], content_type: str) -> bool:
    """A finished readaloud contains both the ebook and its narration."""
    return _present(book.get(content_type)) or _present(book.get("readaloud"))


def check_connection(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Check unsaved settings without putting them into the ownership index."""
    try:
        url, username, password = _config(overrides)
        books = _fetch_books(url, username, password)
    except Exception as exc:  # noqa: BLE001 - show connection errors in Settings
        return {"success": False, "message": f"Storyteller: {exc}"}
    ebooks = sum(holds(book, "ebook") for book in books)
    audiobooks = sum(holds(book, "audiobook") for book in books)
    return {"success": True, "message": f"Storyteller: {ebooks} ebooks, {audiobooks} audiobooks."}


def _identifiers(book: dict[str, Any]) -> list[dict[str, Any]]:
    found = [i for i in book.get("identifiers") or () if isinstance(i, dict)]
    for slot in ("ebook", "audiobook", "readaloud"):
        media = book.get(slot)
        if isinstance(media, dict):
            found.extend(i for i in media.get("identifiers") or () if isinstance(i, dict))
    return found


def _names(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return [str(item.get("name") or "") for item in items if isinstance(item, dict)]


def book_to_entry(book: dict[str, Any]) -> LibraryEntry:
    """One Storyteller book as a matchable library entry."""
    title_tok = set(tokens(str(book.get("title") or "")))
    context_tok: set[str] = set()
    for name in _names(book.get("authors")) + _names(book.get("series")):
        context_tok |= set(tokens(name))

    isbns: set[str] = set()
    asins: set[str] = set()
    external_ids: set[tuple[str, str]] = set()
    for identifier in _identifiers(book):
        kind = str(identifier.get("kind") or "").strip().lower()
        value = str(identifier.get("value") or "").strip()
        if not value:
            continue
        if kind in _ISBN_KINDS:
            isbns |= isbn_variants(value)
        elif kind in _ASIN_KINDS:
            asins.add(value.upper())
        elif provider := _EXTERNAL_ID_KINDS.get(kind):
            external_ids.add((provider, value))

    return LibraryEntry(
        frozenset(title_tok | context_tok),
        frozenset(isbns),
        frozenset(asins),
        frozenset(external_ids),
        frozenset(title_tok),
        frozenset(context_tok),
    )


class StorytellerLibrary:
    """Ebook or audiobook ownership via the Storyteller API, one instance per format."""

    def __init__(self, content_type: str, overrides: Mapping[str, Any] | None = None) -> None:
        self._content_type = content_type
        self._overrides = overrides
        self.name = f"storyteller_{content_type}"
        self.display_name = f"Storyteller {content_type}s"
        self.content_types = frozenset({content_type})

    def is_enabled(self) -> bool:
        return bool(
            setting(app_config, "LIBRARY_CHECK_STORYTELLER_ENABLED", False, self._overrides)
        )

    def describe(self) -> str:
        url, _username, _password = _config(self._overrides)
        where = f" at {url}" if url else ""
        return f"Storyteller{where} ({self._content_type}s)"

    def fingerprint(self) -> tuple[str, str, bytes]:
        url, username, password = _config(self._overrides)
        return url, username, sha256(password.encode()).digest()

    def fetch_entries(self) -> list[LibraryEntry]:
        url, username, password = _config(self._overrides)
        books = _fetch_books(url, username, password)
        return [book_to_entry(book) for book in books if holds(book, self._content_type)]

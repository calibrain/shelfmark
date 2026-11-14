"""In-memory Chomikuj session cache with in-flight login dedupe.

The Chomikuj mobile API requires an ``ApiKey`` obtained from
``account/login``. Shelfmark is single-account, so the cache is keyed by a
PBKDF2-HMAC-SHA256 fingerprint over the username, the password, and the base
URL: changing any of them is a cache miss and starts a fresh login. The
fingerprint is an in-memory cache key only and is never persisted; the salt and
iteration count are public process-local KDF parameters, not password storage.
The fingerprint is also kept on the returned session so callers can compare
cache generations.

Concurrent callers that miss the cache share one in-flight login instead of
logging in twice. Each cache entry owns a :class:`threading.Event`; exactly one
caller (the owner) performs ``client.login`` while the others wait for that
event and then receive the same session or re-raise the same exception. A
rejected login is never cached: the matching entry is evicted before the
failure reaches the caller, so the next call retries. Eviction checks entry
identity, so a login that fails after a concurrent credential change cannot
delete the newer entry.

This module keeps secrets in memory only. It never writes to disk, never logs
the password or the API key, and never holds the module-level lock across the
network call.
"""

import hashlib
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from shelfmark.release_sources.chomikuj.client import ChomikujAuthError, ChomikujError

__all__ = [
    "CACHE_FINGERPRINT_ITERATIONS",
    "CACHE_FINGERPRINT_SALT",
    "ChomikujSession",
    "clear_session",
    "get_session",
]

# Public, process-local PBKDF2 parameters for the in-memory cache key. They are
# not secrets, never leave the process, and are not used for password storage.
CACHE_FINGERPRINT_SALT = b"shelfmark-chomikuj-session-cache-v1"
CACHE_FINGERPRINT_ITERATIONS = 100_000


class _LoginClient(Protocol):
    """The part of :class:`ChomikujClient` the cache needs."""

    def login(self, username: str, password: str) -> Mapping[str, object]:
        """Return the login payload for the given credentials."""
        ...


@dataclass(frozen=True, slots=True)
class ChomikujSession:
    """One resolved Chomikuj login. The password is deliberately absent."""

    api_key: str
    account_id: int | str | None
    account_name: str
    fingerprint: str


class _Entry:
    """Resolved or in-flight login shared by one fingerprint."""

    __slots__ = ("error", "event", "fingerprint", "session")

    def __init__(self, fingerprint: str) -> None:
        """Initialize the entry with its fingerprint and an unresolved state."""
        self.fingerprint = fingerprint
        self.event = threading.Event()
        self.session: ChomikujSession | None = None
        self.error: BaseException | None = None


_cache: dict[str, _Entry] = {}
_cache_lock = threading.Lock()


def _fingerprint(username: str, password: str, base_url: str) -> str:
    """Return the PBKDF2-HMAC-SHA256 cache key for the credentials and base URL.

    The digest is an in-memory cache key only: it is never persisted or logged.
    ``CACHE_FINGERPRINT_SALT`` and ``CACHE_FINGERPRINT_ITERATIONS`` are public
    process-local KDF parameters, not password storage.
    """
    material = f"{username}\0{password}\0{base_url}"
    return hashlib.pbkdf2_hmac(
        "sha256",
        material.encode("utf-8"),
        CACHE_FINGERPRINT_SALT,
        CACHE_FINGERPRINT_ITERATIONS,
    ).hex()


def _to_session(payload: object, fingerprint: str) -> ChomikujSession:
    """Build a session from the login payload or raise a clear error."""
    if not isinstance(payload, Mapping):
        msg = "Chomikuj login returned an unexpected payload"
        raise ChomikujError(msg)
    api_key = payload.get("ApiKey")
    if not isinstance(api_key, str) or not api_key:
        msg = "Chomikuj login did not return an ApiKey"
        raise ChomikujError(msg)
    account_id = payload.get("AccountId")
    if isinstance(account_id, bool) or not isinstance(account_id, (int, str)):
        account_id = None
    account_name = payload.get("AccountName")
    return ChomikujSession(
        api_key=api_key,
        account_id=account_id,
        account_name=account_name if isinstance(account_name, str) else "",
        fingerprint=fingerprint,
    )


def get_session(
    client: _LoginClient,
    *,
    username: str,
    password: str,
    base_url: str,
) -> ChomikujSession:
    """Return a cached session or perform the one login for these credentials."""
    if not isinstance(username, str) or not username.strip():
        msg = "Chomikuj session needs a non-empty username"
        raise ChomikujAuthError(msg)
    if not isinstance(password, str) or not password:
        msg = "Chomikuj session needs a non-empty password"
        raise ChomikujAuthError(msg)
    if not isinstance(base_url, str):
        msg = "Chomikuj session needs a string base URL"
        raise ChomikujError(msg)

    fingerprint = _fingerprint(username, password, base_url)

    with _cache_lock:
        entry = _cache.get(fingerprint)
        if entry is not None and entry.session is not None:
            return entry.session
        if entry is None:
            entry = _Entry(fingerprint)
            _cache[fingerprint] = entry
            owner = True
        else:
            owner = False

    if not owner:
        entry.event.wait()
        if entry.error is not None:
            raise entry.error
        session = entry.session
        if session is None:
            msg = "Chomikuj login finished without a session"
            raise ChomikujError(msg)
        return session

    try:
        payload = client.login(username, password)
        session = _to_session(payload, fingerprint)
    except BaseException as exc:
        entry.error = exc
        with _cache_lock:
            # Only drop this entry: a concurrent credential change (or an
            # explicit clear followed by a newer login) may already own the
            # cache slot, and that newer login must keep it.
            if _cache.get(fingerprint) is entry:
                del _cache[fingerprint]
        entry.event.set()
        raise

    entry.session = session
    entry.event.set()
    return session


def clear_session(
    username: str | None = None,
    password: str | None = None,
    base_url: str | None = None,
) -> None:
    """Drop one cached session, or every session when called without arguments."""
    if username is None and password is None and base_url is None:
        with _cache_lock:
            _cache.clear()
        return
    if username is None or password is None or base_url is None:
        return
    fingerprint = _fingerprint(username, password, base_url)
    with _cache_lock:
        _cache.pop(fingerprint, None)

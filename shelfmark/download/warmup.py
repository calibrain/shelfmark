"""Boot-time warm-up of the direct-download source.

The first AA search after a cold start pays for the whole cold path at once: DNS
resolution, electing a live mirror, spinning up headless Chrome and solving the
DDoS-Guard challenge. That is tens of seconds with the user sat at the search box.

Running one throwaway search shortly after boot moves that cost off the user's first
search. It primes the DNS cache, elects (and quarantines) mirrors, and leaves the
clearance cookie in the bypasser's per-domain cache, so the first real search reuses
it instead of solving from scratch.

Runs on a daemon thread and swallows every failure: this is an optimisation, and a
source that is down at boot must not affect startup or health.
"""

from __future__ import annotations

import threading

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Delay before the warm-up fires. Long enough that it does not compete with the rest
# of startup (and with a container's own health probe) for the first request.
_DEFAULT_DELAY_SECONDS = 15.0

_DEFAULT_QUERY = "The Great Gatsby"

_warmup_thread: threading.Thread | None = None
_warmup_lock = threading.Lock()


def _as_bool(value: object, *, default: bool) -> bool:
    """Coerce a config value that may arrive as a string, bool or None."""
    if value is None:
        return default
    if isinstance(value, str):
        from shelfmark.config.env import string_to_bool

        return string_to_bool(value)
    return bool(value)


def is_enabled() -> bool:
    """Whether the boot-time warm-up search should run."""
    if not _as_bool(config.get("SEARCH_WARMUP_ENABLED", True), default=True):
        return False
    # Nothing to warm if the source is off, and no challenge to pre-solve without
    # the bypasser - a plain search is fast enough not to need this.
    if not _as_bool(config.get("DIRECT_DOWNLOAD_ENABLED", True), default=True):
        logger.debug("Search warm-up skipped: direct download disabled")
        return False
    return True


def warmup_query() -> str:
    """The query used to warm the source."""
    raw = config.get("SEARCH_WARMUP_QUERY", _DEFAULT_QUERY)
    query = str(raw).strip() if raw else ""
    return query or _DEFAULT_QUERY


def run_warmup() -> bool:
    """Run one warm-up search. Returns True if it produced results.

    Never raises: every failure mode here is one the next real search would hit
    anyway, and reporting it is the search path's job, not the warm-up's.
    """
    from shelfmark.core.mirrors import has_aa_mirror_configuration

    if not has_aa_mirror_configuration():
        logger.debug("Search warm-up skipped: no Anna's Archive mirrors configured")
        return False

    query = warmup_query()
    logger.info("Warming up direct download search (%r)", query)
    try:
        from shelfmark.core.models import SearchFilters
        from shelfmark.release_sources.direct_download import search_books

        results = search_books(query, SearchFilters())
    except Exception:
        # Broad by design: a warm-up must never take the app down, and the source
        # raises everything from network errors to parse failures.
        logger.warning("Search warm-up did not complete; first user search may be slow")
        logger.debug("Search warm-up failure detail", exc_info=True)
        return False

    if results:
        logger.info("Search warm-up complete: %s results, source is ready", len(results))
        return True
    logger.info("Search warm-up returned no results; source reachable but empty")
    return False


def start(delay_seconds: float = _DEFAULT_DELAY_SECONDS) -> bool:
    """Schedule the warm-up on a daemon thread. Safe to call multiple times."""
    global _warmup_thread

    if not is_enabled():
        return False

    with _warmup_lock:
        if _warmup_thread is not None and _warmup_thread.is_alive():
            logger.debug("Search warm-up already scheduled")
            return False

        def _run() -> None:
            run_warmup()

        _warmup_thread = threading.Timer(delay_seconds, _run)
        _warmup_thread.daemon = True
        _warmup_thread.name = "SearchWarmup"
        _warmup_thread.start()

    logger.debug("Search warm-up scheduled in %ss", delay_seconds)
    return True

"""MyAnonamouse enrichment for Prowlarr results.

Prowlarr's MyAnonamouse indexer keeps the title, author, language and filetype
of each torrent but drops the narrator and series MAM returns alongside them, and
Torznab has no field to carry them. With the user's MAM session ID (``mam_id``)
Shelfmark runs the same text search against MAM's JSON API and matches the
torrents back to Prowlarr's results by their MAM torrent ID.
"""

import json
import re
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import urlsplit

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.release_sources.prowlarr.utils import coerce_int_like

logger = setup_logger(__name__)

DEFAULT_MAM_BASE_URL = "https://www.myanonamouse.net"
_SEARCH_PATH = "/tor/js/loadSearchJSONbasic.php"
_USER_PATH = "/jsonLoad.php"
_REQUEST_TIMEOUT_SECONDS = 15
_RESULTS_PER_PAGE = 100
_MAX_SEARCHES = 3
_CACHE_TTL_SECONDS = 3600
_HTTP_FORBIDDEN = 403

# Prowlarr sets both infoUrl and guid to "{BaseUrl}t/{id}".
_TORRENT_URL_RE = re.compile(r"^https?://[^/]*myanonamouse\.net/t/(\d+)", re.IGNORECASE)
# Uploaders put the bitrate in the free-text tags: "64 kbps", "128kbps", "64 kb/s".
_BITRATE_RE = re.compile(r"\b(\d{2,4}(?:\.\d+)?)\s*k(?:bps|b/s|bit/s)\b", re.IGNORECASE)

_MAM_REQUEST_ERRORS = (requests.exceptions.RequestException, ValueError)


@dataclass(frozen=True)
class MamTorrentDetails:
    """The fields Prowlarr drops from a MyAnonamouse search result."""

    narrator: str | None = None
    series: str | None = None
    bitrate: str | None = None
    bitrate_kbps: int | None = None


_cache: dict[int, tuple[MamTorrentDetails, float]] = {}
_cache_lock = Lock()


def mam_torrent_id(url: object) -> int | None:
    """Return the MAM torrent ID from a Prowlarr infoUrl/guid, or None for other trackers."""
    if not isinstance(url, str):
        return None
    match = _TORRENT_URL_RE.match(url.strip())
    return int(match.group(1)) if match else None


def mam_base_url(url: object) -> str:
    """Return the MAM origin a Prowlarr result points at (Prowlarr's configured base URL)."""
    if isinstance(url, str) and mam_torrent_id(url) is not None:
        parts = urlsplit(url.strip())
        return f"{parts.scheme}://{parts.netloc}"
    return DEFAULT_MAM_BASE_URL


def _decode_info(raw: object) -> dict[str, Any]:
    """MAM returns author/narrator/series info as a JSON-encoded object string."""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _parse_names(raw: object) -> str | None:
    names = [str(v).strip() for v in _decode_info(raw).values() if isinstance(v, str)]
    names = [n for n in names if n]
    return ", ".join(dict.fromkeys(names)) or None


def _parse_series(raw: object) -> str | None:
    """Format series_info ({"id": ["Name", "1"]}) as "Name #1"."""
    entries: list[str] = []
    for value in _decode_info(raw).values():
        name: str = ""
        number: str = ""
        if isinstance(value, list | tuple) and value:
            name = str(value[0] or "").strip()
            if len(value) > 1 and value[1] not in (None, ""):
                number = str(value[1]).strip()
        elif isinstance(value, str):
            name = value.strip()
        if not name:
            continue
        entries.append(f"{name} #{number}" if number else name)
    return ", ".join(dict.fromkeys(entries)) or None


def _parse_bitrate(tags: object) -> tuple[str | None, int | None]:
    if not isinstance(tags, str):
        return None, None
    match = _BITRATE_RE.search(tags)
    if not match:
        return None, None
    kbps = round(float(match.group(1)))
    return f"{kbps} Kbps", kbps


def parse_torrent_details(item: dict[str, Any]) -> MamTorrentDetails:
    """Pull narrator, series and bitrate out of one MAM search result."""
    bitrate, bitrate_kbps = _parse_bitrate(item.get("tags"))
    return MamTorrentDetails(
        narrator=_parse_names(item.get("narrator_info")),
        series=_parse_series(item.get("series_info")),
        bitrate=bitrate,
        bitrate_kbps=bitrate_kbps,
    )


class MamAuthError(Exception):
    """MAM rejected the session ID."""


class MamClient:
    """Minimal MyAnonamouse JSON API client authenticated by a ``mam_id`` cookie."""

    def __init__(self, mam_id: str, base_url: str = DEFAULT_MAM_BASE_URL) -> None:
        """Create a client for the given session ID and MAM origin."""
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.cookies.set("mam_id", mam_id.strip())
        self._session.headers.update({"Accept": "application/json"})

    def _get(self, path: str, params: dict[str, str] | None = None) -> object:
        url = self.base_url + path
        response = self._session.get(
            url,
            params=params,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            proxies=get_proxies(url),
            verify=get_ssl_verify(url),
        )
        if response.status_code == _HTTP_FORBIDDEN:
            # MAM explains itself in the body (bad cookie, IP/ASN mismatch, ...).
            reply = " ".join(response.text.split())[:200]
            msg = (
                "MyAnonamouse rejected the session ID (403). Sessions only work from the "
                "IP/ASN they are locked to, so check Shelfmark reaches MAM from that address"
                + (f". MAM said: {reply}" if reply else "")
            )
            raise MamAuthError(msg)
        response.raise_for_status()
        return response.json()

    def get_username(self) -> str | None:
        """Return the account name the session belongs to (used by Test Connection)."""
        data = self._get(_USER_PATH)
        if not isinstance(data, dict):
            return None
        username = data.get("username")
        return str(username) if username else None

    def search(self, text: str) -> list[dict[str, Any]]:
        """Search torrents across all categories by title, author, narrator and series."""
        params = {
            "tor[text]": text,
            "tor[searchType]": "all",
            "tor[searchIn]": "torrents",
            "tor[srchIn][title]": "true",
            "tor[srchIn][author]": "true",
            "tor[srchIn][narrator]": "true",
            "tor[srchIn][series]": "true",
            "tor[cat][]": "0",
            "tor[sortType]": "default",
            "tor[startNumber]": "0",
            "perpage": str(_RESULTS_PER_PAGE),
        }
        data = self._get(_SEARCH_PATH, params)
        if not isinstance(data, dict):
            return []
        # An empty search answers {"error": "Nothing returned, out of N"}.
        items = data.get("data")
        if not isinstance(items, list):
            return []
        return [item for item in items if isinstance(item, dict)]


def _cached(torrent_id: int) -> MamTorrentDetails | None:
    with _cache_lock:
        entry = _cache.get(torrent_id)
        if entry is None:
            return None
        details, cached_at = entry
        if time.time() - cached_at > _CACHE_TTL_SECONDS:
            del _cache[torrent_id]
            return None
        return details


def _store(details_by_id: dict[int, MamTorrentDetails]) -> None:
    now = time.time()
    with _cache_lock:
        for torrent_id, details in details_by_id.items():
            _cache[torrent_id] = (details, now)


def lookup_torrent_details(
    mam_id: str,
    torrent_ids: set[int],
    queries: list[str],
    *,
    base_url: str = DEFAULT_MAM_BASE_URL,
    deadline: float | None = None,
) -> dict[int, MamTorrentDetails]:
    """Fetch narrator/series/bitrate for the given MAM torrent IDs (best effort).

    Reruns the queries Prowlarr was sent until every ID is found, capped at a few
    requests. Any failure is logged and returns what was found so far; enrichment
    must never break the Prowlarr search it decorates.
    """
    found: dict[int, MamTorrentDetails] = {}
    for torrent_id in torrent_ids:
        details = _cached(torrent_id)
        if details is not None:
            found[torrent_id] = details

    missing = torrent_ids - found.keys()
    if not missing or not mam_id.strip():
        return found

    client = MamClient(mam_id, base_url)
    searched = 0
    for query in dict.fromkeys(q.strip() for q in queries if q and q.strip()):
        if not missing or searched >= _MAX_SEARCHES:
            break
        if deadline is not None and time.monotonic() > deadline:
            logger.debug("MAM enrichment: search budget spent, %s torrent(s) left", len(missing))
            break
        searched += 1
        try:
            items = client.search(query)
        except MamAuthError as e:
            logger.warning("MAM enrichment disabled for this search: %s", e)
            break
        except _MAM_REQUEST_ERRORS as e:
            logger.warning("MAM enrichment search failed for '%s': %s", query, e)
            break

        fetched: dict[int, MamTorrentDetails] = {}
        for item in items:
            torrent_id = coerce_int_like(item.get("id"))
            if torrent_id is not None:
                fetched[torrent_id] = parse_torrent_details(item)
        _store(fetched)
        for torrent_id in missing & fetched.keys():
            found[torrent_id] = fetched[torrent_id]
        missing -= fetched.keys()

    logger.debug(
        "MAM enrichment: %s of %s torrent(s) matched in %s search(es)",
        len(found),
        len(torrent_ids),
        searched,
    )
    return found

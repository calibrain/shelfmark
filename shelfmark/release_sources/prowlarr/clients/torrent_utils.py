"""Shared utilities for torrent clients."""

import base64
import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

_PROWLARR_DOWNLOAD_PATH = re.compile(r"(?:/api/v1/indexer)?/\d+/download$")


def _decode_prowlarr_link(link_value: str) -> Optional[str]:
    """Decode Prowlarr's link param into a usable URL, if possible."""
    if not link_value:
        return None

    value = link_value.strip()
    if not value:
        return None

    if value.startswith(("http://", "https://", "magnet:")):
        return value

    # Try urlsafe + standard base64 decoding with padding.
    padded = value + "=" * (-len(value) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            decoded = decoder(padded).decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        if decoded.startswith(("http://", "https://", "magnet:")):
            return decoded

    return None


def _get_prowlarr_fallback_url(url: str) -> Optional[str]:
    """Try to extract the original download URL from a Prowlarr download proxy URL."""
    try:
        parsed = urlparse(url)
        if not _PROWLARR_DOWNLOAD_PATH.search(parsed.path):
            return None

        params = parse_qs(parsed.query)
        link_value = (params.get("link") or [None])[0]
        if not link_value:
            return None

        return _decode_prowlarr_link(link_value)
    except Exception:
        return None


@dataclass
class TorrentInfo:
    """Parsed information from a torrent URL."""

    info_hash: Optional[str]
    """Lowercase hex info_hash (32 or 40 chars), or None if extraction failed."""

    torrent_data: Optional[bytes]
    """Raw .torrent file content, only populated for .torrent URLs."""

    is_magnet: bool
    """True if the URL was a magnet link."""

    magnet_url: Optional[str] = None
    """The actual magnet URL, if available."""

    def with_info_hash(self, info_hash: Optional[str]) -> "TorrentInfo":
        """Return a copy with the info_hash replaced when provided."""
        if info_hash:
            return TorrentInfo(
                info_hash=info_hash,
                torrent_data=self.torrent_data,
                is_magnet=self.is_magnet,
                magnet_url=self.magnet_url,
            )
        return self


def extract_torrent_info(
    url: str,
    fetch_torrent: bool = True,
    expected_hash: Optional[str] = None,
    allow_prowlarr_fallback: bool = True,
) -> TorrentInfo:
    """Extract info_hash from magnet link or .torrent URL.

    Notes:
        When the URL points at Prowlarr's proxied download endpoint, it typically
        requires the `X-Api-Key` header. If `PROWLARR_API_KEY` is configured,
        include it for the torrent fetch request.

        This mirrors how Sonarr builds an authenticated download request via the
        indexer when grabbing torrent files.
    """
    fallback_url: Optional[str] = None
    if allow_prowlarr_fallback:
        decoded_url = _get_prowlarr_fallback_url(url)
        if decoded_url and decoded_url != url:
            logger.debug(f"Decoded Prowlarr link, using direct URL: {decoded_url[:80]}...")
            fallback_url = url
            url = decoded_url

    is_magnet = url.startswith("magnet:")

    # Try to extract hash from magnet URL
    if is_magnet:
        info_hash = extract_hash_from_magnet(url)
        if not info_hash and expected_hash:
            info_hash = expected_hash
        return TorrentInfo(info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=url)

    # Not a magnet - try to fetch and parse the .torrent file
    if expected_hash:
        return TorrentInfo(info_hash=expected_hash, torrent_data=None, is_magnet=False)

    if not fetch_torrent:
        return TorrentInfo(info_hash=None, torrent_data=None, is_magnet=False)

    headers: dict[str, str] = {}
    api_key = str(config.get("PROWLARR_API_KEY", "") or "").strip()
    if api_key:
        headers["X-Api-Key"] = api_key

    def resolve_url(current: str, location: str) -> str:
        if not location:
            return current
        # Support relative redirect locations
        return urljoin(current, location)

    try:
        logger.debug(f"Fetching torrent file from: {url[:80]}...")

        # Use allow_redirects=False to handle magnet link redirects manually
        # Some indexers redirect download URLs to magnet links
        resp = requests.get(url, timeout=30, allow_redirects=False, headers=headers)

        # Check if this is a redirect to a magnet link
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resolve_url(url, resp.headers.get("Location", ""))
            if redirect_url.startswith("magnet:"):
                logger.debug("Download URL redirected to magnet link")
                info_hash = extract_hash_from_magnet(redirect_url)
                if not info_hash and expected_hash:
                    info_hash = expected_hash
                return TorrentInfo(
                    info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=redirect_url
                )
            # Not a magnet redirect, follow it manually
            logger.debug(f"Following redirect to: {redirect_url[:80]}...")
            resp = requests.get(redirect_url, timeout=30, headers=headers)

        resp.raise_for_status()
        torrent_data = resp.content

        # Check if response is actually a magnet link (text response)
        # Some indexers return magnet links as plain text instead of redirecting
        if len(torrent_data) < 2000:  # Magnet links are typically short
            try:
                text_content = torrent_data.decode("utf-8", errors="ignore").strip()
                if text_content.startswith("magnet:"):
                    logger.debug("Download URL returned magnet link as response body")
                    info_hash = extract_hash_from_magnet(text_content)
                    if not info_hash and expected_hash:
                        info_hash = expected_hash
                    return TorrentInfo(
                        info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=text_content
                    )
            except Exception:
                pass  # Not text, continue with torrent parsing

        info_hash = extract_info_hash_from_torrent(torrent_data)
        if info_hash:
            logger.debug(f"Extracted hash from torrent file: {info_hash}")
        else:
            logger.warning("Could not extract hash from torrent file")
        return TorrentInfo(info_hash=info_hash, torrent_data=torrent_data, is_magnet=False)
    except Exception as e:
        logger.debug(f"Could not fetch torrent file: {e}")
        if allow_prowlarr_fallback and fallback_url:
            logger.debug(f"Retrying torrent fetch via Prowlarr proxy: {fallback_url[:80]}...")
            return extract_torrent_info(
                fallback_url,
                fetch_torrent=fetch_torrent,
                expected_hash=expected_hash,
                allow_prowlarr_fallback=False,
            )
        return TorrentInfo(info_hash=None, torrent_data=None, is_magnet=False)


    headers: dict[str, str] = {}
    api_key = str(config.get("PROWLARR_API_KEY", "") or "").strip()
    if api_key:
        headers["X-Api-Key"] = api_key

    def resolve_url(current: str, location: str) -> str:
        if not location:
            return current
        # Support relative redirect locations
        return urljoin(current, location)

    try:
        logger.debug(f"Fetching torrent file from: {url[:80]}...")

        # Use allow_redirects=False to handle magnet link redirects manually
        # Some indexers redirect download URLs to magnet links
        resp = requests.get(url, timeout=30, allow_redirects=False, headers=headers)

        # Check if this is a redirect to a magnet link
        if resp.status_code in (301, 302, 303, 307, 308):
            redirect_url = resolve_url(url, resp.headers.get("Location", ""))
            if redirect_url.startswith("magnet:"):
                logger.debug("Download URL redirected to magnet link")
                info_hash = extract_hash_from_magnet(redirect_url)
                return TorrentInfo(
                    info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=redirect_url
                )
            # Not a magnet redirect, follow it manually
            logger.debug(f"Following redirect to: {redirect_url[:80]}...")
            resp = requests.get(redirect_url, timeout=30, headers=headers)

        resp.raise_for_status()
        torrent_data = resp.content

        # Check if response is actually a magnet link (text response)
        # Some indexers return magnet links as plain text instead of redirecting
        if len(torrent_data) < 2000:  # Magnet links are typically short
            try:
                text_content = torrent_data.decode("utf-8", errors="ignore").strip()
                if text_content.startswith("magnet:"):
                    logger.debug("Download URL returned magnet link as response body")
                    info_hash = extract_hash_from_magnet(text_content)
                    return TorrentInfo(
                        info_hash=info_hash, torrent_data=None, is_magnet=True, magnet_url=text_content
                    )
            except Exception:
                pass  # Not text, continue with torrent parsing

        info_hash = extract_info_hash_from_torrent(torrent_data)
        if info_hash:
            logger.debug(f"Extracted hash from torrent file: {info_hash}")
        else:
            logger.warning("Could not extract hash from torrent file")
        return TorrentInfo(info_hash=info_hash, torrent_data=torrent_data, is_magnet=False)
    except Exception as e:
        logger.debug(f"Could not fetch torrent file: {e}")
        return TorrentInfo(info_hash=None, torrent_data=None, is_magnet=False)


def parse_transmission_url(url: str) -> Tuple[str, int, str]:
    """Parse Transmission URL into (host, port, path)."""
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 9091
    path = parsed.path or "/transmission/rpc"

    # Ensure path ends with /rpc
    if not path.endswith("/rpc"):
        path = path.rstrip("/") + "/transmission/rpc"

    return host, port, path


def bencode_decode(data: bytes) -> tuple:
    """Decode bencoded data. Returns (value, remaining_bytes)."""
    if data[0:1] == b'd':
        # Dictionary
        result = {}
        data = data[1:]
        while data[0:1] != b'e':
            key, data = bencode_decode(data)
            value, data = bencode_decode(data)
            result[key] = value
        return result, data[1:]
    elif data[0:1] == b'l':
        # List
        result = []
        data = data[1:]
        while data[0:1] != b'e':
            value, data = bencode_decode(data)
            result.append(value)
        return result, data[1:]
    elif data[0:1] == b'i':
        # Integer
        end = data.index(b'e')
        return int(data[1:end]), data[end + 1:]
    elif data[0:1].isdigit():
        # Byte string
        colon = data.index(b':')
        length = int(data[:colon])
        start = colon + 1
        return data[start:start + length], data[start + length:]
    else:
        first_byte = data[0:1]
        raise ValueError(
            f"Invalid bencode data: expected 'd', 'l', 'i', or digit, "
            f"got {first_byte!r}. First 20 bytes: {data[:20]!r}"
        )


def bencode_encode(data) -> bytes:
    """Encode data to bencode format."""
    if isinstance(data, dict):
        # Keys must be sorted (bencode spec requirement)
        result = b'd'
        for key in sorted(data.keys()):
            result += bencode_encode(key)
            result += bencode_encode(data[key])
        result += b'e'
        return result
    elif isinstance(data, list):
        result = b'l'
        for item in data:
            result += bencode_encode(item)
        result += b'e'
        return result
    elif isinstance(data, int):
        return f'i{data}e'.encode()
    elif isinstance(data, bytes):
        return f'{len(data)}:'.encode() + data
    elif isinstance(data, str):
        encoded = data.encode('utf-8')
        return f'{len(encoded)}:'.encode() + encoded
    else:
        raise ValueError(
            f"Cannot bencode type {type(data).__name__}: "
            f"expected dict, list, int, bytes, or str. Value: {data!r}"
        )


def extract_info_hash_from_torrent(torrent_data: bytes) -> Optional[str]:
    """Extract info_hash from .torrent file data."""
    try:
        decoded, _ = bencode_decode(torrent_data)
        if b'info' not in decoded:
            return None

        info_bencoded = bencode_encode(decoded[b'info'])
        return hashlib.sha1(info_bencoded).hexdigest().lower()
    except Exception as e:
        logger.debug(f"Failed to parse torrent file: {e}")
        return None


def extract_hash_from_magnet(magnet_url: str) -> Optional[str]:
    """Extract info_hash from a magnet URL."""
    if not magnet_url.startswith("magnet:"):
        return None

    parsed = urlparse(magnet_url)
    params = parse_qs(parsed.query)

    for xt in params.get("xt", []):
        # Format: urn:btih:<hash> (32 or 40 chars)
        match = re.match(r"urn:btih:([a-fA-F0-9]{40}|[a-zA-Z0-9]{32})", xt)
        if match:
            hash_value = match.group(1)

            # 40-char hex or 32-char hex (ED2K) - return as-is
            if len(hash_value) == 40 or re.match(r'^[a-fA-F0-9]{32}$', hash_value):
                return hash_value.lower()

            # 32-char base32 - decode to hex
            if re.match(r'^[A-Z2-7]{32}$', hash_value.upper()):
                try:
                    return base64.b32decode(hash_value.upper()).hex().lower()
                except Exception:
                    pass

            # Fallback: return as-is
            return hash_value.lower()

    return None

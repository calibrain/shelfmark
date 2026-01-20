"""
Shared utilities for Prowlarr release source.

Provides common helper functions used across the Prowlarr plugin.
"""

from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def get_protocol(result: dict) -> str:
    """Get the download protocol from a Prowlarr result.

    Uses the protocol field directly if available, otherwise infers from URLs.
    """
    protocol = str(result.get("protocol", "")).lower()
    if protocol in ("torrent", "usenet"):
        return protocol

    magnet_url = str(result.get("magnetUrl") or "").lower()
    download_url = str(result.get("downloadUrl") or "").lower()

    # Prefer magnetUrl for inference if present.
    if magnet_url.startswith("magnet:"):
        return "torrent"

    if download_url.startswith("magnet:") or ".torrent" in download_url:
        return "torrent"
    if ".nzb" in download_url:
        return "usenet"

    return "unknown"


def get_preferred_download_url(result: dict) -> str:
    """Pick the best URL to hand to a download client.

    For torrent results, prefer magnetUrl when available (downloadUrl may be a
    Prowlarr proxy URL that needs auth/headers).
    """
    protocol = str(result.get("protocol", "")).lower()
    magnet_url = str(result.get("magnetUrl") or "").strip()
    download_url = sanitize_download_url(str(result.get("downloadUrl") or "").strip())

    if protocol == "torrent":
        return magnet_url or download_url
    if protocol == "usenet":
        return download_url or magnet_url

    # Unknown protocol: if it looks like a magnet, still prefer it.
    if magnet_url.lower().startswith("magnet:"):
        return magnet_url

    return download_url or magnet_url


def sanitize_download_url(download_url: str) -> str:
    """Normalize Prowlarr download URLs to avoid malformed query strings."""
    if not download_url:
        return download_url

    normalized = download_url.strip()
    if not normalized:
        return normalized

    lower = normalized.lower()
    if not (lower.startswith("http://") or lower.startswith("https://")):
        return normalized

    if " " not in normalized:
        return normalized

    parsed = urlparse(normalized)
    if not parsed.query:
        return normalized

    cleaned_pairs = []
    changed = False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if cleaned_key != key or cleaned_value != value:
            changed = True
        cleaned_pairs.append((cleaned_key, cleaned_value))

    if not changed:
        return normalized

    cleaned_query = urlencode(cleaned_pairs, doseq=True)
    return urlunparse(parsed._replace(query=cleaned_query))


def get_protocol_display(result: dict) -> str:
    """
    Get a user-friendly display label for the protocol.

    Args:
        result: Prowlarr search result dictionary

    Returns:
        Display label: "torrent", "nzb", or "unknown"
    """
    protocol = get_protocol(result)
    if protocol == "usenet":
        return "nzb"
    return protocol


def get_unique_path(staging_dir: Path, name: str, suffix: str = "") -> Path:
    """
    Generate a unique path in staging_dir, appending _N if needed.

    Args:
        staging_dir: Directory to create the path in
        name: Base name for the file/directory
        suffix: Optional suffix (e.g., ".epub" for files)

    Returns:
        Unique Path that doesn't exist in staging_dir
    """
    staged_path = staging_dir / (name + suffix)
    if not staged_path.exists():
        return staged_path

    counter = 1
    while True:
        staged_path = staging_dir / f"{name}_{counter}{suffix}"
        if not staged_path.exists():
            return staged_path
        counter += 1

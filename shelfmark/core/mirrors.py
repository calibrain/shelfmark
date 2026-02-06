"""Centralized mirror configuration for all download sources."""

from typing import List

from shelfmark.core.utils import normalize_http_url

# Lazy import to avoid circular imports
_config_module = None


def _get_config():
    """Lazy import of config module to avoid circular imports."""
    global _config_module
    if _config_module is None:
        from shelfmark.core.config import config
        _config_module = config
    return _config_module


# Default mirror lists (hardcoded fallbacks)
DEFAULT_AA_MIRRORS = [
    "https://annas-archive.gl",
    "https://annas-archive.li",
]

DEFAULT_LIBGEN_MIRRORS = [
    "https://libgen.gl",
    "https://libgen.li",
    "https://libgen.bz",
    "https://libgen.la",
    "https://libgen.vg",
]

DEFAULT_ZLIB_MIRRORS = [
    "https://z-lib.fm",
    "https://z-lib.gs",
    "https://z-lib.id",
    "https://z-library.sk",
    "https://zlibrary-global.se",
]

DEFAULT_WELIB_MIRRORS = [
    "https://welib.org",
]


def _normalize_mirror_url(url: str) -> str:
    return normalize_http_url(url, default_scheme="https")


def get_aa_mirrors() -> List[str]:
    """
    Get Anna's Archive mirrors.

    Returns:
        Ordered list of AA mirror URLs.

        If AA_MIRROR_URLS is configured, it is treated as the full list.
        Otherwise, defaults are used and AA_ADDITIONAL_URLS (legacy) is appended.

        Notes:
        - The list is used to populate the AA mirror dropdown in Settings.
        - When AA_BASE_URL is set to 'auto', mirrors are tried in the order listed.
    """
    config = _get_config()

    mirrors: list[str] = []

    configured_list = config.get("AA_MIRROR_URLS", None)
    if isinstance(configured_list, list):
        for url in configured_list:
            normalized = _normalize_mirror_url(str(url))
            if normalized and normalized not in mirrors:
                mirrors.append(normalized)
    elif isinstance(configured_list, str) and configured_list.strip():
        # Allow comma-separated env/manual configs.
        for url in configured_list.split(","):
            normalized = _normalize_mirror_url(url)
            if normalized and normalized not in mirrors:
                mirrors.append(normalized)

    if not mirrors:
        mirrors = [_normalize_mirror_url(url) for url in DEFAULT_AA_MIRRORS]
        mirrors = [url for url in mirrors if url]

        # Backwards-compatible append-only behavior for legacy configs/env.
        additional = config.get("AA_ADDITIONAL_URLS", "")
        if additional:
            for url in additional.split(","):
                normalized = _normalize_mirror_url(url)
                if normalized and normalized not in mirrors:
                    mirrors.append(normalized)

    return mirrors


def get_libgen_mirrors() -> List[str]:
    """
    Get LibGen mirrors: defaults + any additional from config.

    Returns:
        List of LibGen mirror URLs (defaults first, then custom additions).
    """
    mirrors = [_normalize_mirror_url(url) for url in DEFAULT_LIBGEN_MIRRORS]
    mirrors = [url for url in mirrors if url]
    config = _get_config()

    additional = config.get("LIBGEN_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            normalized = _normalize_mirror_url(url)
            if normalized and normalized not in mirrors:
                mirrors.append(normalized)

    return mirrors


def get_zlib_mirrors() -> List[str]:
    """
    Get Z-Library mirrors, with primary first.

    Returns:
        List of Z-Library mirror URLs, primary first.
    """
    config = _get_config()

    primary = _normalize_mirror_url(config.get("ZLIB_PRIMARY_URL", DEFAULT_ZLIB_MIRRORS[0]))
    if not primary:
        primary = _normalize_mirror_url(DEFAULT_ZLIB_MIRRORS[0])
    mirrors = [primary]

    # Add other defaults (excluding primary)
    for url in DEFAULT_ZLIB_MIRRORS:
        normalized = _normalize_mirror_url(url)
        if normalized and normalized != primary:
            mirrors.append(normalized)

    # Add custom mirrors
    additional = config.get("ZLIB_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            normalized = _normalize_mirror_url(url)
            if normalized and normalized not in mirrors:
                mirrors.append(normalized)

    return mirrors


def get_zlib_primary_url() -> str:
    """
    Get the primary Z-Library mirror URL.

    Returns:
        Primary Z-Library mirror URL.
    """
    config = _get_config()
    primary = _normalize_mirror_url(config.get("ZLIB_PRIMARY_URL", DEFAULT_ZLIB_MIRRORS[0]))
    return primary or _normalize_mirror_url(DEFAULT_ZLIB_MIRRORS[0])


def get_zlib_url_template() -> str:
    """
    Get Z-Library URL template using configured primary mirror.

    Returns:
        URL template with {md5} placeholder.
    """
    primary = get_zlib_primary_url()
    return f"{primary}/md5/{{md5}}"


def get_welib_mirrors() -> List[str]:
    """
    Get Welib mirrors, with primary first.

    Returns:
        List of Welib mirror URLs, primary first.
    """
    config = _get_config()

    primary = _normalize_mirror_url(config.get("WELIB_PRIMARY_URL", DEFAULT_WELIB_MIRRORS[0]))
    if not primary:
        primary = _normalize_mirror_url(DEFAULT_WELIB_MIRRORS[0])
    mirrors = [primary]

    # Add other defaults (excluding primary)
    for url in DEFAULT_WELIB_MIRRORS:
        normalized = _normalize_mirror_url(url)
        if normalized and normalized != primary:
            mirrors.append(normalized)

    # Add custom mirrors
    additional = config.get("WELIB_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            normalized = _normalize_mirror_url(url)
            if normalized and normalized not in mirrors:
                mirrors.append(normalized)

    return mirrors


def get_welib_primary_url() -> str:
    """
    Get the primary Welib mirror URL.

    Returns:
        Primary Welib mirror URL.
    """
    config = _get_config()
    primary = _normalize_mirror_url(config.get("WELIB_PRIMARY_URL", DEFAULT_WELIB_MIRRORS[0]))
    return primary or _normalize_mirror_url(DEFAULT_WELIB_MIRRORS[0])


def get_welib_url_template() -> str:
    """
    Get Welib URL template using configured primary mirror.

    Returns:
        URL template with {md5} placeholder.
    """
    primary = get_welib_primary_url()
    return f"{primary}/md5/{{md5}}"


def get_zlib_cookie_domains() -> set:
    """
    Get set of Z-Library domains that need full cookie handling.

    Used by internal_bypasser for CF bypass cookie management.

    Returns:
        Set of domain strings (without protocol).
    """
    domains = set()

    # Add all default domains
    for url in DEFAULT_ZLIB_MIRRORS:
        normalized = _normalize_mirror_url(url)
        if normalized:
            domain = normalized.replace("https://", "").replace("http://", "").split("/")[0]
            domains.add(domain)

    # Add custom domains
    config = _get_config()
    additional = config.get("ZLIB_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            normalized = _normalize_mirror_url(url)
            if normalized:
                domain = normalized.replace("https://", "").replace("http://", "").split("/")[0]
                domains.add(domain)

    return domains

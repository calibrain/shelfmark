"""Centralized mirror configuration for all download sources."""

from typing import List

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
    "https://annas-archive.se",
    "https://annas-archive.li",
    "https://annas-archive.pm",
    "https://annas-archive.in",
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


def get_aa_mirrors() -> List[str]:
    """
    Get Anna's Archive mirrors from config + defaults.

    Returns:
        List of AA mirror URLs, starting with defaults then custom additions.
    """
    mirrors = list(DEFAULT_AA_MIRRORS)
    config = _get_config()

    additional = config.get("AA_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            url = url.strip()
            if url and url not in mirrors:
                mirrors.append(url)

    return mirrors


def get_libgen_mirrors() -> List[str]:
    """
    Get LibGen mirrors: defaults + any additional from config.

    Returns:
        List of LibGen mirror URLs (defaults first, then custom additions).
    """
    mirrors = list(DEFAULT_LIBGEN_MIRRORS)
    config = _get_config()

    additional = config.get("LIBGEN_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            url = url.strip()
            if url and url not in mirrors:
                mirrors.append(url)

    return mirrors


def get_zlib_mirrors() -> List[str]:
    """
    Get Z-Library mirrors, with primary first.

    Returns:
        List of Z-Library mirror URLs, primary first.
    """
    config = _get_config()

    primary = config.get("ZLIB_PRIMARY_URL", DEFAULT_ZLIB_MIRRORS[0])
    mirrors = [primary]

    # Add other defaults (excluding primary)
    for url in DEFAULT_ZLIB_MIRRORS:
        if url != primary:
            mirrors.append(url)

    # Add custom mirrors
    additional = config.get("ZLIB_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            url = url.strip()
            if url and url not in mirrors:
                mirrors.append(url)

    return mirrors


def get_zlib_primary_url() -> str:
    """
    Get the primary Z-Library mirror URL.

    Returns:
        Primary Z-Library mirror URL.
    """
    config = _get_config()
    return config.get("ZLIB_PRIMARY_URL", DEFAULT_ZLIB_MIRRORS[0])


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

    primary = config.get("WELIB_PRIMARY_URL", DEFAULT_WELIB_MIRRORS[0])
    mirrors = [primary]

    # Add other defaults (excluding primary)
    for url in DEFAULT_WELIB_MIRRORS:
        if url != primary:
            mirrors.append(url)

    # Add custom mirrors
    additional = config.get("WELIB_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            url = url.strip()
            if url and url not in mirrors:
                mirrors.append(url)

    return mirrors


def get_welib_primary_url() -> str:
    """
    Get the primary Welib mirror URL.

    Returns:
        Primary Welib mirror URL.
    """
    config = _get_config()
    return config.get("WELIB_PRIMARY_URL", DEFAULT_WELIB_MIRRORS[0])


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
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        domains.add(domain)

    # Add custom domains
    config = _get_config()
    additional = config.get("ZLIB_ADDITIONAL_URLS", "")
    if additional:
        for url in additional.split(","):
            url = url.strip()
            if url:
                domain = url.replace("https://", "").replace("http://", "").split("/")[0]
                domains.add(domain)

    return domains

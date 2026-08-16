"""Clearance cookies won by a bypass, shared by every bypasser implementation.

Kept in its own module rather than inside a bypasser because both of them feed it and
both read from it. The internal bypasser cannot host it: it imports seleniumbase at
module scope, which is exactly the dependency an external-bypasser deployment is
entitled not to have installed.
"""

import threading
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Cookie storage - shared with requests library for Cloudflare bypass
# Nested mapping of domain to cookie name to cookie metadata.
_cf_cookies: dict[str, dict] = {}
_cf_cookies_lock = threading.Lock()

# User-Agent storage - Cloudflare ties cf_clearance to the UA that solved the challenge
_cf_user_agents: dict[str, str] = {}

# Protection cookie names we care about (Cloudflare and DDoS-Guard)
CF_COOKIE_NAMES = {"cf_clearance", "__cf_bm", "cf_chl_2", "cf_chl_prog"}
DDG_COOKIE_NAMES = {
    "__ddg1_",
    "__ddg2_",
    "__ddg5_",
    "__ddg8_",
    "__ddg9_",
    "__ddg10_",
    "__ddgid_",
    "__ddgmark_",
    "ddg_last_challenge",
}

# DDoS-Guard cookies that describe *one* check rather than granting clearance, and so
# must never be replayed on a later request. Observed live on Anna's Archive:
#
#   __ddg9_   the client IP address
#   __ddg10_  the unix timestamp the check was issued
#   __ddg8_   an opaque token issued with them, same ~40 minute expiry
#
# Clearance itself lives in __ddg1_/__ddg2_/__ddgid_ (roughly a year) and __ddg5_.
# Replaying the trio is actively harmful: once the timestamp ages out - or the egress
# IP changes, which happens routinely behind a VPN - the values no longer describe the
# caller, DDoS-Guard re-arms its check and answers every request with a ?check=1
# redirect. That is the redirect loop, and it is self-inflicted. Dropping them simply
# lets DDoS-Guard issue a fresh set, exactly as it does for a browser.
DDG_EPHEMERAL_COOKIE_NAMES = {
    "__ddg8_",
    "__ddg9_",
    "__ddg10_",
    "ddg_last_challenge",
}


def _get_base_domain(domain: str) -> str:
    """Extract base domain from hostname (e.g., 'www.example.com' -> 'example.com')."""
    return ".".join(domain.split(".")[-2:]) if "." in domain else domain


def _get_full_cookie_domains() -> set[str]:
    """Return mirror domains that need full-session cookie extraction."""
    from shelfmark.core.mirrors import get_zlib_cookie_domains

    return {_get_base_domain(domain) for domain in get_zlib_cookie_domains()}


def _should_extract_cookie(name: str, *, extract_all: bool) -> bool:
    """Determine if a cookie should be extracted based on its name."""
    # Checked before extract_all: a per-check token is wrong to replay for every
    # domain, including the full-session ones.
    if name in DDG_EPHEMERAL_COOKIE_NAMES:
        return False
    if extract_all:
        return True
    is_cf = name in CF_COOKIE_NAMES or name.startswith("cf_")
    is_ddg = name in DDG_COOKIE_NAMES or name.startswith("__ddg")
    return is_cf or is_ddg


def _cookie_field(cookie: Any, name: str) -> Any:
    """Read one field from a cookie in either shape we are handed.

    The internal bypasser extracts CDP cookie objects; an external bypasser returns
    the same fields as JSON objects, so the difference is attribute versus key access.
    """
    if isinstance(cookie, Mapping):
        return cookie.get(name)
    return getattr(cookie, name, None)


def _cookie_expiry(cookie: Any) -> float | None:
    """A cookie's absolute expiry, or None when it is a session cookie.

    The two spellings are not interchangeable and both reach this store. CDP and
    Playwright cookies carry `expires`; the WebDriver cookie object - what a
    Selenium-based solver such as FlareSolverr returns - carries `expiry`. Reading
    only one silently turns every cookie from the other into a never-expiring one,
    which is exactly how dead clearance ends up replayed forever (see
    get_cf_cookies_for_domain).

    The value is coerced rather than trusted: it arrives as JSON from a service we
    do not control, and a string here used to raise straight out of the store.
    """
    for field in ("expires", "expiry"):
        raw = _cookie_field(cookie, field)
        if raw is None:
            continue
        try:
            expiry = float(raw)
        except TypeError, ValueError:
            logger.debug("Unreadable cookie expiry %r; treating as a session cookie", raw)
            return None
        # <= 0 is how both shapes spell "session cookie", not "expired in 1970".
        return expiry if expiry > 0 else None
    return None


def store_extracted_cookies(
    *,
    url: str,
    cookies: list[Any],
    user_agent: str | None = None,
) -> None:
    """Store filtered bypass cookies (and optional UA) for a URL domain."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if not domain:
        return

    base_domain = _get_base_domain(domain)
    extract_all = base_domain in _get_full_cookie_domains()

    cookies_found: dict[str, dict[str, Any]] = {}
    for cookie in cookies:
        name = _cookie_field(cookie, "name") or ""
        if not _should_extract_cookie(name, extract_all=extract_all):
            continue
        secure = _cookie_field(cookie, "secure")
        cookies_found[name] = {
            "value": _cookie_field(cookie, "value") or "",
            "domain": _cookie_field(cookie, "domain") or domain,
            "path": _cookie_field(cookie, "path") or "/",
            "expiry": _cookie_expiry(cookie),
            "secure": True if secure is None else bool(secure),
            "httpOnly": True,
        }

    if not cookies_found:
        return

    with _cf_cookies_lock:
        _cf_cookies[base_domain] = cookies_found
        if user_agent:
            _cf_user_agents[base_domain] = user_agent
            logger.debug("Stored UA for %s: %s...", base_domain, str(user_agent)[:60])
        else:
            logger.debug("No UA captured for %s", base_domain)

    cookie_type = "all" if extract_all else "protection"
    logger.debug("Extracted %s %s cookies for %s", len(cookies_found), cookie_type, base_domain)


def _is_cookie_expired(cookie: dict[str, Any]) -> bool:
    """Whether a stored cookie's expiry has passed. Session cookies never expire here."""
    expiry = cookie.get("expiry")
    if expiry is None:
        expiry = cookie.get("expires")
    if not expiry or expiry <= 0:
        return False
    return time.time() > expiry


def get_cf_cookies_for_domain(domain: str) -> dict[str, str]:
    """Get stored cookies for a domain. Returns empty dict if none available."""
    if not domain:
        return {}

    base_domain = _get_base_domain(domain)

    with _cf_cookies_lock:
        cookies = _cf_cookies.get(base_domain, {})
        if not cookies:
            return {}

        cf_clearance = cookies.get("cf_clearance", {})
        if cf_clearance and _is_cookie_expired(cf_clearance):
            logger.debug("CF cookies expired for %s", base_domain)
            _cf_cookies.pop(base_domain, None)
            return {}

        # Expiry applies to every cookie, not just Cloudflare's. DDoS-Guard domains
        # have no cf_clearance, so the check above never fired for them and dead
        # cookies were replayed indefinitely - the server answers those with a
        # challenge, which is indistinguishable from having sent nothing at all.
        live = {name: c for name, c in cookies.items() if not _is_cookie_expired(c)}
        if len(live) != len(cookies):
            expired = sorted(set(cookies) - set(live))
            logger.debug("Dropping expired cookies for %s: %s", base_domain, expired)
            if live:
                _cf_cookies[base_domain] = live
            else:
                _cf_cookies.pop(base_domain, None)

        return {name: c["value"] for name, c in live.items()}


def has_valid_cf_cookies(domain: str) -> bool:
    """Check if we have valid Cloudflare cookies for a domain."""
    return bool(get_cf_cookies_for_domain(domain))


def get_cf_user_agent_for_domain(domain: str) -> str | None:
    """Get the User-Agent that was used during bypass for a domain."""
    if not domain:
        return None
    with _cf_cookies_lock:
        return _cf_user_agents.get(_get_base_domain(domain))


def export_store() -> tuple[dict[str, dict], dict[str, str]]:
    """Snapshot the whole store, for handing to another process.

    The internal bypasser's Docker helper solves in a subprocess, so the clearance it
    wins has to be serialized back to the parent or the solve is lost with the child.
    """
    with _cf_cookies_lock:
        return (
            {domain: dict(cookies) for domain, cookies in _cf_cookies.items()},
            dict(_cf_user_agents),
        )


def import_store(cookies: object, user_agents: object) -> None:
    """Merge a snapshot produced by :func:`export_store` into this process's store."""
    with _cf_cookies_lock:
        if isinstance(cookies, dict):
            _cf_cookies.update(cookies)
        if isinstance(user_agents, dict):
            _cf_user_agents.update(
                {str(domain): str(agent) for domain, agent in user_agents.items()}
            )


def clear_cf_cookies(domain: str | None = None) -> None:
    """Clear stored Cloudflare cookies and User-Agent. If domain is None, clear all."""
    with _cf_cookies_lock:
        if domain:
            base_domain = _get_base_domain(domain)
            _cf_cookies.pop(base_domain, None)
            _cf_user_agents.pop(base_domain, None)
        else:
            _cf_cookies.clear()
            _cf_user_agents.clear()

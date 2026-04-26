"""Internal Cloudflare bypass implementation using SeleniumBase and CDP helpers."""

import _thread
import asyncio
import json
import os
import random
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import suppress
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from threading import Event
from typing import Any, Protocol, TypedDict, TypeGuard
from urllib.parse import urlparse

import requests
from seleniumbase import cdp_driver
from seleniumbase.undetected.cdp_driver.connection import ProtocolException

from shelfmark.bypass import BypassCancelledError
from shelfmark.bypass.fingerprint import get_screen_size
from shelfmark.config import env
from shelfmark.config.env import LOG_DIR
from shelfmark.config.settings import RECORDING_DIR
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.download import network
from shelfmark.download.network import get_proxies, get_ssl_verify

logger = setup_logger(__name__)

SELENIUMBASE_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "shelfmark" / "seleniumbase"
SELENIUMBASE_DOWNLOADS_DIR = SELENIUMBASE_RUNTIME_ROOT / "downloaded_files"
_BYPASSED_BODY_LENGTH_MIN = 100_000
_BYPASS_EMOJI_MATCH_MIN = 3
_LOADING_BODY_LENGTH_MAX = 50
_PAGE_BODY_PREVIEW_CHARS = 500
_BROWSER_START_TIMEOUT_SECONDS = 45.0
_BYPASS_SUBPROCESS_TIMEOUT_SECONDS = 420.0
_BYPASS_CHILD_ENV = "SHELFMARK_INTERNAL_BYPASSER_CHILD"

# Challenge detection indicators
CLOUDFLARE_INDICATORS = [
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "cloudflare.com/products/turnstile",
]

DDOS_GUARD_INDICATORS = [
    "ddos-guard",
    "ddos guard",
    "checking your browser before accessing",
    "complete the manual check to continue",
    "could not verify your browser automatically",
]


class _DisplayState(TypedDict):
    ffmpeg: subprocess.Popen[bytes] | None
    ffmpeg_output: Path | None


class _PageWithWindowRect(Protocol):
    async def set_window_rect(self, x: int, _y: int, width: int, height: int) -> object: ...


class _BrowserWithWindowRectPage(Protocol):
    page: _PageWithWindowRect


DISPLAY: _DisplayState = {
    "ffmpeg": None,
    "ffmpeg_output": None,
}
LOCKED = threading.Lock()
_PGREP_PATH = shutil.which("pgrep")
_PKILL_PATH = shutil.which("pkill")
_RNG = random.SystemRandom()

_CDP_OPERATION_ERRORS = (
    asyncio.TimeoutError,
    AttributeError,
    NameError,
    OSError,
    ProtocolException,
    RuntimeError,
    TypeError,
    ValueError,
)
_PATH_INSPECTION_ERRORS = (OSError, RuntimeError, TypeError, ValueError)
_REQUEST_OPERATION_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    requests.RequestException,
)
_SUBPROCESS_OPERATION_ERRORS = (
    OSError,
    RuntimeError,
    TypeError,
    ValueError,
    subprocess.SubprocessError,
)


def _get_native_attr(module: str, name: str, fallback: Any) -> Any:
    """Return an unpatched stdlib attribute when running under gevent."""
    try:
        from gevent import monkey

        original = monkey.get_original(module, name)
    except ImportError, AttributeError, RuntimeError:
        return fallback
    else:
        return original or fallback


_NATIVE_START_NEW_THREAD = _get_native_attr("_thread", "start_new_thread", _thread.start_new_thread)
_NATIVE_EVENT = _get_native_attr("threading", "Event", threading.Event)
_NATIVE_LOCK = _get_native_attr("threading", "Lock", threading.Lock)


def _coerce_positive_int(value: object, default: int) -> int:
    """Return a positive integer config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int) and value > 0:
        return value
    return default


def _coerce_non_negative_float(value: object, default: float) -> float:
    """Return a non-negative float config value or the provided default."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float) and value >= 0:
        return float(value)
    return default


def _has_window_rect_page(candidate: object) -> TypeGuard[_BrowserWithWindowRectPage]:
    """Check whether a browser wrapper exposes page.set_window_rect()."""
    page = getattr(candidate, "page", None)
    return callable(getattr(page, "set_window_rect", None))


def _describe_runtime_path(path: str | Path) -> str:
    """Return compact ownership/mode info for a runtime path."""
    try:
        path = Path(path)
        link_target = ""
        if path.is_symlink():
            link_target = f" -> {path.readlink()}"
        st = path.stat()
        mode = stat.S_IMODE(st.st_mode)
        return f"{path}{link_target} exists uid={st.st_uid} gid={st.st_gid} mode={oct(mode)}"
    except FileNotFoundError:
        return f"{path} missing"
    except _PATH_INSPECTION_ERRORS as e:
        return f"{path} error={type(e).__name__}: {e}"


class _CdpWorker:
    def __init__(self) -> None:
        self._thread_id: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = _NATIVE_EVENT()
        self._lock = _NATIVE_LOCK()

    def _run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            loop.run_forever()
            with suppress(Exception):
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
        finally:
            self._thread_id = None

    def start(self) -> None:
        with self._lock:
            if self._loop and self._loop.is_running() and not self._loop.is_closed():
                return
            self._loop = None
            self._ready.clear()
            self._thread_id = _NATIVE_START_NEW_THREAD(self._run, ())
        if not self._ready.wait(timeout=10):
            msg = "CDP worker loop failed to start"
            raise RuntimeError(msg)

    def run(self, coro: Any, timeout: float | None = None) -> Any:
        self.start()
        if not self._loop or self._loop.is_closed():
            msg = "CDP worker loop not available"
            raise RuntimeError(msg)
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)


_CDP_WORKER = _CdpWorker()

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


def _get_base_domain(domain: str) -> str:
    """Extract base domain from hostname (e.g., 'www.example.com' -> 'example.com')."""
    return ".".join(domain.split(".")[-2:]) if "." in domain else domain


def _get_full_cookie_domains() -> set[str]:
    """Return mirror domains that need full-session cookie extraction."""
    from shelfmark.core.mirrors import get_zlib_cookie_domains

    return {_get_base_domain(domain) for domain in get_zlib_cookie_domains()}


def _should_extract_cookie(name: str, *, extract_all: bool) -> bool:
    """Determine if a cookie should be extracted based on its name."""
    if extract_all:
        return True
    is_cf = name in CF_COOKIE_NAMES or name.startswith("cf_")
    is_ddg = name in DDG_COOKIE_NAMES or name.startswith("__ddg")
    return is_cf or is_ddg


def _store_extracted_cookies(
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
        name = getattr(cookie, "name", "") or ""
        if not _should_extract_cookie(name, extract_all=extract_all):
            continue
        expires = getattr(cookie, "expires", None)
        if expires is not None and expires <= 0:
            expires = None
        cookies_found[name] = {
            "value": getattr(cookie, "value", ""),
            "domain": getattr(cookie, "domain", None) or domain,
            "path": getattr(cookie, "path", None) or "/",
            "expiry": expires,
            "secure": bool(getattr(cookie, "secure", True)),
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


async def _extract_cookies_from_cdp(driver: Any, page: Any, url: str) -> None:
    """Extract cookies from a CDP browser after successful bypass."""
    try:
        try:
            all_cookies = await driver.cookies.get_all(requests_cookie_format=True)
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Failed to get cookies via CDP: %s", e)
            return

        try:
            user_agent = await page.evaluate("navigator.userAgent")
        except _CDP_OPERATION_ERRORS:
            user_agent = None

        _store_extracted_cookies(url=url, cookies=all_cookies, user_agent=user_agent)

    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Failed to extract cookies: %s", e)


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
        if cf_clearance:
            expiry = cf_clearance.get("expiry")
            if expiry is None:
                expiry = cf_clearance.get("expires")
            if expiry and expiry > 0 and time.time() > expiry:
                logger.debug("CF cookies expired for %s", base_domain)
                _cf_cookies.pop(base_domain, None)
                return {}

        return {name: c["value"] for name, c in cookies.items()}


def has_valid_cf_cookies(domain: str) -> bool:
    """Check if we have valid Cloudflare cookies for a domain."""
    return bool(get_cf_cookies_for_domain(domain))


def get_cf_user_agent_for_domain(domain: str) -> str | None:
    """Get the User-Agent that was used during bypass for a domain."""
    if not domain:
        return None
    with _cf_cookies_lock:
        return _cf_user_agents.get(_get_base_domain(domain))


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


def _cleanup_orphan_processes() -> int:
    """Kill orphan Chrome/Xvfb/ffmpeg processes. Only runs in Docker mode."""
    if not env.DOCKERMODE:
        return 0

    _stop_ffmpeg_recording()

    processes_to_kill = ["chrome", "chromium", "Xvfb", "ffmpeg"]
    total_killed = 0

    logger.debug("Checking for orphan processes...")
    logger.log_resource_usage()

    if _PGREP_PATH is None or _PKILL_PATH is None:
        logger.warning("Skipping orphan-process cleanup because pgrep/pkill are unavailable")
        return 0

    for proc_name in processes_to_kill:
        try:
            result = subprocess.run(
                [_PGREP_PATH, "-f", proc_name],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue

            pids = result.stdout.strip().split("\n")
            count = len(pids)
            logger.info("Found %s orphan %s process(es), killing...", count, proc_name)

            kill_result = subprocess.run(
                [_PKILL_PATH, "-9", "-f", proc_name],
                capture_output=True,
                check=False,
                timeout=5,
            )
            if kill_result.returncode == 0:
                total_killed += count
            else:
                logger.warning("pkill for %s returned %s", proc_name, kill_result.returncode)

        except subprocess.TimeoutExpired:
            logger.warning("Timeout while checking for %s processes", proc_name)
        except _SUBPROCESS_OPERATION_ERRORS as e:
            logger.debug("Error checking for %s processes: %s", proc_name, e)

    if total_killed > 0:
        time.sleep(1)
        logger.info("Cleaned up %s orphan process(es)", total_killed)
        logger.log_resource_usage()
    else:
        logger.debug("No orphan processes found")

    return total_killed


async def _get_page_info(page: Any) -> tuple[str, str, str]:
    """Extract page title, body text, and current URL safely."""
    try:
        title = (await page.get_title() or "").lower()
    except _CDP_OPERATION_ERRORS:
        title = ""
    try:
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        body = (body or "").lower()
    except _CDP_OPERATION_ERRORS:
        body = ""
    try:
        current_url = await page.get_current_url() or ""
    except _CDP_OPERATION_ERRORS:
        current_url = ""
    return title, body, current_url


def _check_indicators(title: str, body: str, indicators: list[str]) -> str | None:
    """Check if any indicator is present in title or body. Returns the found indicator or None."""
    for indicator in indicators:
        if indicator in title or indicator in body:
            return indicator
    return None


def _has_cloudflare_patterns(body: str, url: str) -> bool:
    """Check for Cloudflare-specific patterns in body or URL."""
    return "cf-" in body or "cloudflare" in url.lower() or "/cdn-cgi/" in url


async def _detect_challenge_type(page: Any) -> str:
    """Detect challenge type: 'cloudflare', 'ddos_guard', or 'none'."""
    title, body, current_url = await _get_page_info(page)

    # DDOS-Guard indicators
    if found := _check_indicators(title, body, DDOS_GUARD_INDICATORS):
        logger.debug("DDOS-Guard indicator found: '%s'", found)
        return "ddos_guard"

    # Cloudflare indicators
    if found := _check_indicators(title, body, CLOUDFLARE_INDICATORS):
        logger.debug("Cloudflare indicator found: '%s'", found)
        return "cloudflare"

    # Check URL patterns
    if _has_cloudflare_patterns(body, current_url):
        return "cloudflare"

    return "none"


async def _is_bypassed(page: Any, *, escape_emojis: bool = True) -> bool:
    """Check if the protection has been bypassed."""
    title, body, current_url = await _get_page_info(page)
    body_len = len(body.strip())

    # Long page content = probably bypassed
    if body_len > _BYPASSED_BODY_LENGTH_MIN:
        logger.debug("Page content too long, probably bypassed (len: %s)", body_len)
        return True

    # Multiple emojis = probably real content
    if escape_emojis:
        import emoji

        if len(emoji.emoji_list(body)) >= _BYPASS_EMOJI_MATCH_MIN:
            logger.debug("Detected emojis in page, probably bypassed")
            return True

    # Check for protection indicators (means NOT bypassed)
    if _check_indicators(title, body, CLOUDFLARE_INDICATORS + DDOS_GUARD_INDICATORS):
        return False

    # Cloudflare URL patterns
    if _has_cloudflare_patterns(body, current_url):
        logger.debug("Cloudflare patterns detected in page")
        return False

    # Page too short = still loading
    if body_len < _LOADING_BODY_LENGTH_MAX:
        logger.debug("Page content too short, might still be loading")
        return False

    logger.debug("Bypass check passed - Title: '%s', Body length: %s", title[:100], body_len)
    return True


async def _bypass_method_humanlike(page: Any) -> bool:
    """Human-like behavior with scroll, wait, and reload."""
    try:
        logger.debug("Attempting bypass: human-like interaction")
        await asyncio.sleep(_RNG.uniform(6, 10))

        try:
            await page.evaluate("window.scrollTo(0, 10000);")
            await page.wait()
            await asyncio.sleep(_RNG.uniform(1, 2))
            await page.evaluate("window.scrollTo(0, 0);")
            await page.wait()
            await asyncio.sleep(_RNG.uniform(2, 3))
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Scroll behavior failed: %s", e)

        if await _is_bypassed(page):
            return True

        logger.debug("Trying page refresh...")
        await page.reload(ignore_cache=True)
        await asyncio.sleep(_RNG.uniform(5, 8))

        if await _is_bypassed(page):
            return True

        try:
            await page.solve_captcha()
            await asyncio.sleep(_RNG.uniform(3, 5))
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Final captcha click failed: %s", e)

        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Human-like method failed: %s", e)
        return False


async def _bypass_method_cdp_solve(page: Any) -> bool:
    """CDP Mode with solve_captcha() - auto-detects challenge type."""
    try:
        logger.debug("Attempting bypass: CDP solve_captcha")
        await page.solve_captcha()
        await asyncio.sleep(_RNG.uniform(3, 5))
        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP solve_captcha failed: %s", e)
        return False


CDP_CLICK_SELECTORS = [
    "#turnstile-widget div",  # Cloudflare Turnstile
    "#cf-turnstile div",  # Alternative CF Turnstile
    "iframe[src*='challenges']",  # CF challenge iframe
    "input[type='checkbox']",  # Generic checkbox (DDOS-Guard)
    "[class*='checkbox']",  # Class-based checkbox
    "#challenge-running",  # CF challenge indicator
]


async def _bypass_method_cdp_click(page: Any) -> bool:
    """CDP Mode with native clicking - no PyAutoGUI dependency."""
    try:
        logger.debug("Attempting bypass: CDP native click")

        for selector in CDP_CLICK_SELECTORS:
            try:
                if not await page.is_element_visible(selector):
                    continue

                logger.debug("CDP clicking: %s", selector)
                await page.click(selector)
                await asyncio.sleep(_RNG.uniform(2, 4))

                if await _is_bypassed(page):
                    return True
            except _CDP_OPERATION_ERRORS as e:
                logger.debug("CDP click on '%s' failed: %s", selector, e)

        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP Mode click failed: %s", e)
        return False


CDP_GUI_CLICK_SELECTORS = [
    "#turnstile-widget div",  # Cloudflare Turnstile
    "#cf-turnstile div",  # Alternative CF Turnstile
    "#challenge-stage div",  # CF challenge stage
    "input[type='checkbox']",  # Generic checkbox
    "[class*='cb-i']",  # DDOS-Guard checkbox
]


async def _bypass_method_cdp_gui_click(page: Any) -> bool:
    """CDP Mode with gui_click-style behavior."""
    try:
        logger.debug("Attempting bypass: CDP gui_click (mouse-based)")

        try:
            logger.debug("Trying solve_captcha()")
            await page.solve_captcha()
            await asyncio.sleep(_RNG.uniform(3, 5))

            if await _is_bypassed(page):
                return True
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("solve_captcha() failed: %s", e)

        for selector in CDP_GUI_CLICK_SELECTORS:
            try:
                if not await page.is_element_visible(selector):
                    continue

                logger.debug("CDP click_with_offset: %s", selector)
                await page.click_with_offset(selector, 0, 0, center=True)
                await asyncio.sleep(_RNG.uniform(3, 5))

                if await _is_bypassed(page):
                    return True
            except _CDP_OPERATION_ERRORS as e:
                logger.debug("CDP gui_click on '%s' failed: %s", selector, e)

        return await _is_bypassed(page)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP Mode gui_click failed: %s", e)
        return False


BYPASS_METHODS = [
    _bypass_method_cdp_solve,
    _bypass_method_cdp_gui_click,
    _bypass_method_cdp_click,
    _bypass_method_humanlike,
]

MAX_CONSECUTIVE_SAME_CHALLENGE = 3


def _check_cancellation(cancel_flag: Event | None, message: str) -> None:
    """Check if cancellation was requested and raise if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info(message)
        msg = "Bypass cancelled"
        raise BypassCancelledError(msg)


async def _bypass(
    page: Any, max_retries: int | None = None, cancel_flag: Event | None = None
) -> bool:
    """Attempt to bypass Cloudflare/DDOS-Guard protection using multiple methods."""
    max_retries = (
        max_retries if max_retries is not None else _coerce_positive_int(app_config.MAX_RETRY, 10)
    )

    last_challenge_type = None
    consecutive_same_challenge = 0
    # Allow at least one full pass through all bypass methods before aborting due to a "stuck" challenge.
    min_same_challenge_before_abort = max(MAX_CONSECUTIVE_SAME_CHALLENGE, len(BYPASS_METHODS) + 1)

    for try_count in range(max_retries):
        _check_cancellation(cancel_flag, "Bypass cancelled by user")

        if await _is_bypassed(page):
            if try_count == 0:
                logger.info("Page already bypassed")
            return True

        challenge_type = await _detect_challenge_type(page)
        logger.debug("Challenge detected: %s", challenge_type)

        # No challenge detected but page doesn't look bypassed - wait and retry
        if challenge_type == "none":
            logger.info("No challenge detected, waiting for page to settle...")
            await asyncio.sleep(_RNG.uniform(2, 3))
            if await _is_bypassed(page):
                return True
            # Try a simple refresh instead of captcha methods
            try:
                await page.reload(ignore_cache=True)
                await asyncio.sleep(_RNG.uniform(1, 2))
                if await _is_bypassed(page):
                    logger.info("Bypass successful after refresh")
                    return True
            except _CDP_OPERATION_ERRORS as e:
                logger.debug("Refresh during no-challenge wait failed: %s", e)
            continue

        if challenge_type == last_challenge_type:
            consecutive_same_challenge += 1
            if consecutive_same_challenge >= min_same_challenge_before_abort:
                logger.warning(
                    "Same challenge (%s) detected %s times - aborting",
                    challenge_type,
                    consecutive_same_challenge,
                )
                return False
        else:
            consecutive_same_challenge = 1
        last_challenge_type = challenge_type

        method = BYPASS_METHODS[try_count % len(BYPASS_METHODS)]
        logger.info("Bypass attempt %s/%s using %s", try_count + 1, max_retries, method.__name__)

        if try_count > 0:
            wait_time = min(_RNG.uniform(2, 4) * try_count, 12)
            logger.info("Waiting %0.1fs before trying...", wait_time)
            for _ in range(int(wait_time)):
                _check_cancellation(cancel_flag, "Bypass cancelled during wait")
                await asyncio.sleep(1)
            await asyncio.sleep(wait_time - int(wait_time))

        try:
            if await method(page):
                logger.info("Bypass successful using %s", method.__name__)
                return True
        except BypassCancelledError:
            raise
        except _CDP_OPERATION_ERRORS as e:
            logger.warning("Exception in %s: %s", method.__name__, e)

        logger.info("Bypass method %s failed.", method.__name__)

    logger.warning("Exceeded maximum retries. Bypass failed.")
    return False


def _get_browser_args() -> list[str]:
    """Build extra Chrome arguments, pre-resolving hostnames via patched DNS.

    Pre-resolves AA hostnames and passes IPs to Chrome via --host-resolver-rules,
    bypassing Chrome's DNS entirely for those hosts.
    """
    arguments = [
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--allow-running-insecure-content",
        "--ignore-certificate-errors-spki-list",
        "--ignore-certificate-errors-skip-list",
        # Chrome 144+ disabled automatic SwiftShader fallback for WebGL (security reasons).
        # Without this flag, WebGL is broken in headless/Docker which triggers bot detection.
        # See: https://issues.chromium.org/issues/40277080
        "--enable-unsafe-swiftshader",
    ]

    if app_config.get("DEBUG", False):
        arguments.extend(
            ["--enable-logging", "--v=1", "--log-file=" + str(LOG_DIR / "chrome_browser.log")]
        )

    host_rules = _build_host_resolver_rules()
    if host_rules:
        arguments.append(f"--host-resolver-rules={', '.join(host_rules)}")
        logger.debug("Chrome: Using host resolver rules for %s hosts", len(host_rules))
    else:
        logger.warning("Chrome: No hosts could be pre-resolved")

    return arguments


def _build_host_resolver_rules() -> list[str]:
    """Pre-resolve AA hostnames and build Chrome host resolver rules."""
    host_rules = []

    try:
        for url in network.get_available_aa_urls():
            hostname = urlparse(url).hostname
            if not hostname:
                continue

            try:
                results = socket.getaddrinfo(hostname, 443, socket.AF_INET)
                if results:
                    ip = results[0][4][0]
                    host_rules.append(f"MAP {hostname} {ip}")
                    logger.debug("Chrome: Pre-resolved %s -> %s", hostname, ip)
                else:
                    logger.warning("Chrome: No addresses returned for %s", hostname)
            except socket.gaierror as e:
                logger.warning("Chrome: Could not pre-resolve %s: %s", hostname, e)
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        logger.error_trace(f"Error pre-resolving hostnames for Chrome: {e}")

    return host_rules


DRIVER_RESET_ERRORS = {"ProtocolException", "RuntimeError", "TimeoutError"}


async def _get(url: str, driver: Any, cancel_flag: Event | None = None) -> str:
    """Fetch URL with Cloudflare bypass using a CDP browser."""
    _check_cancellation(cancel_flag, "Bypass cancelled before starting")

    logger.debug("CDP_GET: %s", url)

    logger.debug("Opening URL with SeleniumBase CDP...")
    page = await driver.get(url)
    with suppress(Exception):
        await page.wait()

    _check_cancellation(cancel_flag, "Bypass cancelled after page load")

    try:
        current_url = await page.get_current_url()
        title = await page.get_title()
        logger.debug("Page loaded - URL: %s, Title: %s", current_url, title)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Could not get page info: %s", e)

    logger.debug("Starting bypass process...")
    if await _bypass(page, cancel_flag=cancel_flag):
        await _extract_cookies_from_cdp(driver, page, url)
        return await page.get_page_source()

    logger.warning("Bypass completed but page still shows protection")
    try:
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        if body:
            preview = body
            if len(body) > _PAGE_BODY_PREVIEW_CHARS:
                preview = body[:_PAGE_BODY_PREVIEW_CHARS] + "..."
            logger.debug("Page content: %s", preview)
    except _CDP_OPERATION_ERRORS as exc:
        logger.debug("Could not inspect protected page body: %s", exc)

    return ""


def _run_bypass_in_current_process(url: str, retry: int, cancel_flag: Event | None = None) -> str:
    """Run the CDP bypass in the current process."""

    async def _run_bypass() -> str:
        driver = None
        try:
            driver = await _create_cdp_browser(url)

            for attempt in range(retry):
                _check_cancellation(cancel_flag, "Bypass cancelled before attempt")

                try:
                    result = await _get(url, driver, cancel_flag)
                    if result:
                        return result
                except BypassCancelledError:
                    raise
                except _CDP_OPERATION_ERRORS as e:
                    error_details = f"{type(e).__name__}: {e}"
                    logger.warning(
                        "Bypass failed (attempt %s/%s): %s", attempt + 1, retry, error_details
                    )
                    logger.debug("Stack trace: %s", traceback.format_exc())

                    # On CDP errors, quit and create a fresh browser
                    if type(e).__name__ in DRIVER_RESET_ERRORS:
                        logger.info("Restarting Chrome due to browser error...")
                        await _close_cdp_driver(driver)
                        driver = await _create_cdp_browser(url)

            logger.error("Bypass failed after %s attempts", retry)
            return ""
        finally:
            if driver:
                await _close_cdp_driver(driver)

    if os.environ.get(_BYPASS_CHILD_ENV) == "1":
        return asyncio.run(_run_bypass())
    return _CDP_WORKER.run(_run_bypass())


def _store_child_bypass_state(payload: dict[str, Any]) -> None:
    cookies = payload.get("cookies")
    if isinstance(cookies, dict):
        with _cf_cookies_lock:
            _cf_cookies.update(cookies)

    user_agents = payload.get("user_agents")
    if isinstance(user_agents, dict):
        with _cf_cookies_lock:
            _cf_user_agents.update(
                {str(domain): str(agent) for domain, agent in user_agents.items()}
            )


def _get_via_subprocess(url: str, retry: int, cancel_flag: Event | None = None) -> str:
    """Run the browser bypass in a helper process isolated from gunicorn/gevent."""
    _check_cancellation(cancel_flag, "Bypass cancelled before helper process")
    result_path = (
        Path(tempfile.gettempdir()) / f"shelfmark-bypass-{os.getpid()}-{time.time_ns()}.json"
    )
    payload = {"url": url, "retry": retry, "result_path": str(result_path)}
    env_vars = os.environ.copy()
    env_vars[_BYPASS_CHILD_ENV] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "shelfmark.bypass.internal_bypasser"],
        stdin=subprocess.PIPE,
        text=True,
        env=env_vars,
    )
    try:
        proc.communicate(json.dumps(payload), timeout=_BYPASS_SUBPROCESS_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        msg = "Internal bypasser helper process timed out"
        raise TimeoutError(msg) from None

    try:
        result = json.loads(result_path.read_text())
    except FileNotFoundError as exc:
        msg = f"Internal bypasser helper exited without a result (code {proc.returncode})"
        raise RuntimeError(msg) from exc
    finally:
        with suppress(OSError):
            result_path.unlink()

    if not isinstance(result, dict):
        msg = "Internal bypasser helper returned an invalid result"
        raise TypeError(msg)

    if not result.get("ok"):
        error_type = result.get("error_type", "RuntimeError")
        error = result.get("error", "Internal bypasser helper failed")
        trace = result.get("traceback")
        if trace:
            logger.debug("Internal bypasser helper traceback: %s", trace)
        msg = f"{error_type}: {error}"
        raise RuntimeError(msg)

    _store_child_bypass_state(result)
    html = result.get("html", "")
    return html if isinstance(html, str) else ""


def get(url: str, retry: int | None = None, cancel_flag: Event | None = None) -> str:
    """Fetch a URL with protection bypass. Creates fresh Chrome instance for each bypass."""
    retry = retry if retry is not None else _coerce_positive_int(app_config.MAX_RETRY, 10)

    with LOCKED:
        # Try cookies first - another request may have completed bypass while waiting
        cached_result = _try_with_cached_cookies(url, urlparse(url).hostname or "")
        if cached_result:
            return cached_result

        if env.DOCKERMODE and os.environ.get(_BYPASS_CHILD_ENV) != "1":
            return _get_via_subprocess(url, retry, cancel_flag)
        return _run_bypass_in_current_process(url, retry, cancel_flag)


def _get_proxy_string(url: str) -> str | None:
    """Return a single proxy string for CDP, honoring NO_PROXY."""
    proxies = get_proxies(url)
    if not proxies:
        return None
    proxy_url = proxies.get("https") or proxies.get("http")
    return proxy_url or None


async def _create_cdp_browser(url: str) -> Any:
    """Create a fresh CDP browser instance."""
    browser_args = _get_browser_args()
    screen_width, screen_height = get_screen_size()
    display_width = screen_width + 100
    display_height = screen_height + 150
    proxy = _get_proxy_string(url)

    logger.debug("Creating Pure CDP browser with args: %s", browser_args)
    logger.debug("Browser screen size: %sx%s", screen_width, screen_height)

    try:
        driver = await asyncio.wait_for(
            cdp_driver.start_async(
                headless=False,
                headed=False,
                xvfb=True,
                xvfb_metrics=f"{display_width},{display_height}",
                sandbox=False,
                lang="en",
                incognito=True,
                ad_block=True,
                proxy=proxy,
                browser_args=browser_args,
            ),
            timeout=_BROWSER_START_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning(
            "Pure CDP browser startup timed out after %.0fs",
            _BROWSER_START_TIMEOUT_SECONDS,
        )
        if env.DOCKERMODE:
            _cleanup_orphan_processes()
        raise
    except Exception as e:
        logger.warning("Pure CDP browser startup failed: %s: %s", type(e).__name__, e)
        logger.warning(
            "SeleniumBase runtime paths: cwd=%s; %s; %s; %s; %s",
            Path.cwd(),
            _describe_runtime_path(SELENIUMBASE_DOWNLOADS_DIR),
            _describe_runtime_path("/app/downloaded_files"),
            _describe_runtime_path("downloaded_files"),
            _describe_runtime_path(tempfile.gettempdir()),
        )
        if env.DOCKERMODE:
            _cleanup_orphan_processes()
        msg = f"Pure CDP browser startup failed: {e}"
        raise RuntimeError(msg) from e

    if _has_window_rect_page(driver):
        try:
            await driver.page.set_window_rect(0, 0, screen_width, screen_height)
        except _CDP_OPERATION_ERRORS as e:
            logger.debug("Failed to set window size: %s", e)

    # Start FFmpeg recording if debug mode (record each bypass session)
    if app_config.get("DEBUG", False) and not DISPLAY.get("ffmpeg"):
        _start_ffmpeg_recording(display=os.environ.get("DISPLAY", ":0"))

    await asyncio.sleep(_coerce_non_negative_float(app_config.DEFAULT_SLEEP, 5.0))
    logger.info("Chrome browser ready (Pure CDP)")
    logger.log_resource_usage()
    return driver


async def _close_cdp_driver(driver: Any) -> None:
    """Close CDP connections and stop the browser."""
    if not driver:
        return

    logger.debug("Quitting Chrome browser (CDP)...")

    _stop_ffmpeg_recording()

    try:
        connections = []
        if hasattr(driver, "connection") and driver.connection:
            connections.append(driver.connection)
        if hasattr(driver, "targets") and driver.targets:
            connections.extend(driver.targets)
        for conn in connections:
            await _close_websocket_connection(conn)
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Error during connection cleanup: %s", e)

    try:
        driver.stop()
        logger.debug("Stopped CDP browser")
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("CDP stop: %s", e)

    if env.DOCKERMODE:
        await asyncio.sleep(0.3)
        try:
            pid = getattr(driver, "_process_pid", None)

            def _pid_alive(check_pid: int) -> bool:
                try:
                    os.kill(check_pid, 0)
                except ProcessLookupError:
                    return False
                except PermissionError:
                    return True
                return True

            if pid and _pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGTERM)
                    await asyncio.sleep(0.1)
                    if _pid_alive(pid):
                        os.kill(pid, signal.SIGKILL)
                    logger.debug("Killed Chrome pid %s", pid)
                except (OSError, RuntimeError, TypeError, ValueError) as e:
                    logger.debug("Failed to kill Chrome pid %s: %s", pid, e)
        except (OSError, RuntimeError, TypeError, ValueError) as e:
            logger.debug("Process cleanup failed: %s", e)

    logger.log_resource_usage()


async def _close_websocket_connection(conn: Any) -> None:
    """Close one websocket-like connection, ignoring best-effort failures."""
    try:
        await conn.aclose()
    except _CDP_OPERATION_ERRORS as e:
        logger.debug("Failed to close websocket connection: %s", e)


def _start_ffmpeg_recording(display: str) -> None:
    """Start FFmpeg screen recording for debug mode."""
    global DISPLAY
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%y%m%d-%H%M%S")
    output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"

    screen_width, screen_height = get_screen_size()
    display_width = screen_width + 100
    display_height = screen_height + 150

    ffmpeg_cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "x11grab",
        "-video_size",
        f"{display_width}x{display_height}",
        "-i",
        display,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-maxrate",
        "700k",
        "-bufsize",
        "1400k",
        "-crf",
        "36",
        "-pix_fmt",
        "yuv420p",
        "-tune",
        "animation",
        "-x264-params",
        "bframes=0:deblock=-1,-1",
        "-r",
        "15",
        "-an",
        output_file.as_posix(),
        "-nostats",
        "-loglevel",
        "0",
    ]
    logger.debug("Starting FFmpeg recording to %s", output_file)
    logger.debug_trace(f"FFmpeg command: {' '.join(ffmpeg_cmd)}")
    DISPLAY["ffmpeg"] = subprocess.Popen(ffmpeg_cmd)
    DISPLAY["ffmpeg_output"] = output_file


def _stop_ffmpeg_recording() -> None:
    """Stop FFmpeg screen recording if running."""
    import signal

    global DISPLAY
    proc = DISPLAY.get("ffmpeg")
    if not proc:
        return
    if proc.poll() is not None:
        logger.debug("FFmpeg already stopped")
        DISPLAY["ffmpeg"] = None
        DISPLAY["ffmpeg_output"] = None
        return
    try:
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=5)
        logger.debug("Stopped ffmpeg recording")
    except _SUBPROCESS_OPERATION_ERRORS as e:
        logger.debug("ffmpeg stop: %s", e)
        with suppress(Exception):
            proc.terminate()
            proc.wait(timeout=2)
        with suppress(Exception):
            proc.kill()
    DISPLAY["ffmpeg"] = None
    DISPLAY["ffmpeg_output"] = None


def _try_with_cached_cookies(url: str, hostname: str) -> str | None:
    """Attempt request with cached cookies before using Chrome."""
    cookies = get_cf_cookies_for_domain(hostname)
    if not cookies:
        return None

    try:
        headers = {}
        stored_ua = get_cf_user_agent_for_domain(hostname)
        if stored_ua:
            headers["User-Agent"] = stored_ua

        logger.debug("Trying request with cached cookies: %s", url)
        response = requests.get(
            url,
            cookies=cookies,
            headers=headers,
            proxies=get_proxies(url),
            timeout=(5, 10),
            verify=get_ssl_verify(url),
        )
        if response.status_code == HTTPStatus.OK:
            logger.debug("Cached cookies worked, skipped Chrome bypass")
            return response.text
    except _REQUEST_OPERATION_ERRORS as exc:
        logger.debug("Cached cookie retry failed for %s: %s", url, exc)

    return None


def get_bypassed_page(
    url: str, selector: network.AAMirrorSelector | None = None, cancel_flag: Event | None = None
) -> str | None:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser."""
    sel = selector or network.AAMirrorSelector()
    attempt_url = sel.rewrite(url)
    hostname = urlparse(attempt_url).hostname or ""

    cached_result = _try_with_cached_cookies(attempt_url, hostname)
    if cached_result:
        return cached_result

    try:
        response_html = get(attempt_url, cancel_flag=cancel_flag)
    except BypassCancelledError:
        raise
    except _CDP_OPERATION_ERRORS + _REQUEST_OPERATION_ERRORS:
        _check_cancellation(cancel_flag, "Bypass cancelled")
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            attempt_url = sel.rewrite(url)
            response_html = get(attempt_url, cancel_flag=cancel_flag)
        else:
            raise

    if not response_html.strip():
        msg = "Failed to bypass Cloudflare"
        raise requests.exceptions.RequestException(msg)

    return response_html


def _run_child_process() -> int:
    """CLI entrypoint used by the Docker helper subprocess."""
    request = json.loads(sys.stdin.read() or "{}")
    result_path = Path(str(request["result_path"]))
    url = str(request["url"])
    retry = _coerce_positive_int(
        request.get("retry"), _coerce_positive_int(app_config.MAX_RETRY, 10)
    )

    try:
        html = get(url, retry=retry)
        payload = {
            "ok": True,
            "html": html,
            "cookies": _cf_cookies,
            "user_agents": _cf_user_agents,
        }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - helper boundary must serialize failures.
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_run_child_process())

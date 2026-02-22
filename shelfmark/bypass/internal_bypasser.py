import asyncio
import os
import random
import signal
import socket
import subprocess
import threading
import time
import traceback
from datetime import datetime
from threading import Event
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from seleniumbase import cdp_driver

from shelfmark.bypass import BypassCancelledException
from shelfmark.bypass.fingerprint import get_screen_size
from shelfmark.config import env
from shelfmark.config.env import LOG_DIR
from shelfmark.config.settings import RECORDING_DIR
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.download import network
from shelfmark.download.network import get_proxies, get_ssl_verify

logger = setup_logger(__name__)

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

DISPLAY = {
    "ffmpeg": None,
    "ffmpeg_output": None,
}
LOCKED = threading.Lock()


class _CdpWorker:
    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        loop.run_forever()
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except Exception:
            pass
        finally:
            loop.close()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._ready.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="cdp-worker",
                daemon=True,
            )
            self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("CDP worker loop failed to start")

    def run(self, coro: Any, timeout: Optional[float] = None) -> Any:
        self.start()
        if not self._loop or self._loop.is_closed():
            raise RuntimeError("CDP worker loop not available")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)


_CDP_WORKER = _CdpWorker()

# Cookie storage - shared with requests library for Cloudflare bypass
# Structure: {domain: {cookie_name: {value, expiry, ...}}}
_cf_cookies: dict[str, dict] = {}
_cf_cookies_lock = threading.Lock()

# User-Agent storage - Cloudflare ties cf_clearance to the UA that solved the challenge
_cf_user_agents: dict[str, str] = {}

# Protection cookie names we care about (Cloudflare and DDoS-Guard)
CF_COOKIE_NAMES = {'cf_clearance', '__cf_bm', 'cf_chl_2', 'cf_chl_prog'}
DDG_COOKIE_NAMES = {'__ddg1_', '__ddg2_', '__ddg5_', '__ddg8_', '__ddg9_', '__ddg10_', '__ddgid_', '__ddgmark_', 'ddg_last_challenge'}

# Domains requiring full session cookies (not just protection cookies)
FULL_COOKIE_DOMAINS = {'z-lib.fm', 'z-lib.gs', 'z-lib.id', 'z-library.sk', 'zlibrary-global.se'}


def _get_base_domain(domain: str) -> str:
    """Extract base domain from hostname (e.g., 'www.example.com' -> 'example.com')."""
    return '.'.join(domain.split('.')[-2:]) if '.' in domain else domain


def _should_extract_cookie(name: str, extract_all: bool) -> bool:
    """Determine if a cookie should be extracted based on its name."""
    if extract_all:
        return True
    is_cf = name in CF_COOKIE_NAMES or name.startswith('cf_')
    is_ddg = name in DDG_COOKIE_NAMES or name.startswith('__ddg')
    return is_cf or is_ddg


def _store_extracted_cookies(
    *,
    url: str,
    cookies: list[Any],
    user_agent: Optional[str] = None,
) -> None:
    """Store filtered bypass cookies (and optional UA) for a URL domain."""
    parsed = urlparse(url)
    domain = parsed.hostname or ""
    if not domain:
        return

    base_domain = _get_base_domain(domain)
    extract_all = base_domain in FULL_COOKIE_DOMAINS

    cookies_found: dict[str, dict[str, Any]] = {}
    for cookie in cookies:
        name = getattr(cookie, "name", "") or ""
        if not _should_extract_cookie(name, extract_all):
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
            logger.debug(f"Stored UA for {base_domain}: {str(user_agent)[:60]}...")
        else:
            logger.debug(f"No UA captured for {base_domain}")

    cookie_type = "all" if extract_all else "protection"
    logger.debug(f"Extracted {len(cookies_found)} {cookie_type} cookies for {base_domain}")


async def _extract_cookies_from_cdp(driver, page, url: str) -> None:
    """Extract cookies from a CDP browser after successful bypass."""
    try:
        try:
            all_cookies = await driver.cookies.get_all(requests_cookie_format=True)
        except Exception as e:
            logger.debug(f"Failed to get cookies via CDP: {e}")
            return

        try:
            user_agent = await page.evaluate("navigator.userAgent")
        except Exception:
            user_agent = None

        _store_extracted_cookies(url=url, cookies=all_cookies, user_agent=user_agent)

    except Exception as e:
        logger.debug(f"Failed to extract cookies: {e}")

def get_cf_cookies_for_domain(domain: str) -> dict[str, str]:
    """Get stored cookies for a domain. Returns empty dict if none available."""
    if not domain:
        return {}

    base_domain = _get_base_domain(domain)

    with _cf_cookies_lock:
        cookies = _cf_cookies.get(base_domain, {})
        if not cookies:
            return {}

        cf_clearance = cookies.get('cf_clearance', {})
        if cf_clearance:
            expiry = cf_clearance.get('expiry')
            if expiry is None:
                expiry = cf_clearance.get('expires')
            if expiry and expiry > 0 and time.time() > expiry:
                logger.debug(f"CF cookies expired for {base_domain}")
                _cf_cookies.pop(base_domain, None)
                return {}

        return {name: c['value'] for name, c in cookies.items()}


def has_valid_cf_cookies(domain: str) -> bool:
    """Check if we have valid Cloudflare cookies for a domain."""
    return bool(get_cf_cookies_for_domain(domain))


def get_cf_user_agent_for_domain(domain: str) -> Optional[str]:
    """Get the User-Agent that was used during bypass for a domain."""
    if not domain:
        return None
    with _cf_cookies_lock:
        return _cf_user_agents.get(_get_base_domain(domain))


def clear_cf_cookies(domain: str = None) -> None:
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

    for proc_name in processes_to_kill:
        try:
            result = subprocess.run(
                ["pgrep", "-f", proc_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0 or not result.stdout.strip():
                continue

            pids = result.stdout.strip().split('\n')
            count = len(pids)
            logger.info(f"Found {count} orphan {proc_name} process(es), killing...")

            kill_result = subprocess.run(
                ["pkill", "-9", "-f", proc_name],
                capture_output=True,
                timeout=5
            )
            if kill_result.returncode == 0:
                total_killed += count
            else:
                logger.warning(f"pkill for {proc_name} returned {kill_result.returncode}")

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout while checking for {proc_name} processes")
        except Exception as e:
            logger.debug(f"Error checking for {proc_name} processes: {e}")

    if total_killed > 0:
        time.sleep(1)
        logger.info(f"Cleaned up {total_killed} orphan process(es)")
        logger.log_resource_usage()
    else:
        logger.debug("No orphan processes found")

    return total_killed

async def _get_page_info(page) -> tuple[str, str, str]:
    """Extract page title, body text, and current URL safely."""
    try:
        title = (await page.get_title() or "").lower()
    except Exception:
        title = ""
    try:
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        body = (body or "").lower()
    except Exception:
        body = ""
    try:
        current_url = await page.get_current_url() or ""
    except Exception:
        current_url = ""
    return title, body, current_url


def _check_indicators(title: str, body: str, indicators: list[str]) -> Optional[str]:
    """Check if any indicator is present in title or body. Returns the found indicator or None."""
    for indicator in indicators:
        if indicator in title or indicator in body:
            return indicator
    return None

def _has_cloudflare_patterns(body: str, url: str) -> bool:
    """Check for Cloudflare-specific patterns in body or URL."""
    return "cf-" in body or "cloudflare" in url.lower() or "/cdn-cgi/" in url

async def _detect_challenge_type(page) -> str:
    """Detect challenge type: 'cloudflare', 'ddos_guard', or 'none'."""
    try:
        title, body, current_url = await _get_page_info(page)

        # DDOS-Guard indicators
        if found := _check_indicators(title, body, DDOS_GUARD_INDICATORS):
            logger.debug(f"DDOS-Guard indicator found: '{found}'")
            return "ddos_guard"

        # Cloudflare indicators
        if found := _check_indicators(title, body, CLOUDFLARE_INDICATORS):
            logger.debug(f"Cloudflare indicator found: '{found}'")
            return "cloudflare"

        # Check URL patterns
        if _has_cloudflare_patterns(body, current_url):
            return "cloudflare"

        return "none"
    except Exception as e:
        logger.warning(f"Error detecting challenge type: {e}")
        return "none"

async def _is_bypassed(page, escape_emojis: bool = True) -> bool:
    """Check if the protection has been bypassed."""
    try:
        title, body, current_url = await _get_page_info(page)
        body_len = len(body.strip())

        # Long page content = probably bypassed
        if body_len > 100000:
            logger.debug(f"Page content too long, probably bypassed (len: {body_len})")
            return True

        # Multiple emojis = probably real content
        if escape_emojis:
            import emoji
            if len(emoji.emoji_list(body)) >= 3:
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
        if body_len < 50:
            logger.debug("Page content too short, might still be loading")
            return False

        logger.debug(f"Bypass check passed - Title: '{title[:100]}', Body length: {body_len}")
        return True

    except Exception as e:
        logger.warning(f"Error checking bypass status: {e}")
        return False

async def _bypass_method_humanlike(page) -> bool:
    """Human-like behavior with scroll, wait, and reload."""
    try:
        logger.debug("Attempting bypass: human-like interaction")
        await asyncio.sleep(random.uniform(6, 10))

        try:
            await page.evaluate("window.scrollTo(0, 10000);")
            await page.wait()
            await asyncio.sleep(random.uniform(1, 2))
            await page.evaluate("window.scrollTo(0, 0);")
            await page.wait()
            await asyncio.sleep(random.uniform(2, 3))
        except Exception as e:
            logger.debug(f"Scroll behavior failed: {e}")

        if await _is_bypassed(page):
            return True

        logger.debug("Trying page refresh...")
        await page.reload(ignore_cache=True)
        await asyncio.sleep(random.uniform(5, 8))

        if await _is_bypassed(page):
            return True

        try:
            await page.solve_captcha()
            await asyncio.sleep(random.uniform(3, 5))
        except Exception as e:
            logger.debug(f"Final captcha click failed: {e}")

        return await _is_bypassed(page)
    except Exception as e:
        logger.debug(f"Human-like method failed: {e}")
        return False


async def _bypass_method_cdp_solve(page) -> bool:
    """CDP Mode with solve_captcha() - auto-detects challenge type."""
    try:
        logger.debug("Attempting bypass: CDP solve_captcha")
        await page.solve_captcha()
        await asyncio.sleep(random.uniform(3, 5))
        return await _is_bypassed(page)
    except Exception as e:
        logger.debug(f"CDP solve_captcha failed: {e}")
        return False


CDP_CLICK_SELECTORS = [
    "#turnstile-widget div",      # Cloudflare Turnstile
    "#cf-turnstile div",          # Alternative CF Turnstile
    "iframe[src*='challenges']",  # CF challenge iframe
    "input[type='checkbox']",     # Generic checkbox (DDOS-Guard)
    "[class*='checkbox']",        # Class-based checkbox
    "#challenge-running",         # CF challenge indicator
]


async def _bypass_method_cdp_click(page) -> bool:
    """CDP Mode with native clicking - no PyAutoGUI dependency."""
    try:
        logger.debug("Attempting bypass: CDP native click")

        for selector in CDP_CLICK_SELECTORS:
            try:
                if not await page.is_element_visible(selector):
                    continue

                logger.debug(f"CDP clicking: {selector}")
                await page.click(selector)
                await asyncio.sleep(random.uniform(2, 4))

                if await _is_bypassed(page):
                    return True
            except Exception as e:
                logger.debug(f"CDP click on '{selector}' failed: {e}")

        return await _is_bypassed(page)
    except Exception as e:
        logger.debug(f"CDP Mode click failed: {e}")
        return False


CDP_GUI_CLICK_SELECTORS = [
    "#turnstile-widget div",      # Cloudflare Turnstile
    "#cf-turnstile div",          # Alternative CF Turnstile
    "#challenge-stage div",       # CF challenge stage
    "input[type='checkbox']",     # Generic checkbox
    "[class*='cb-i']",            # DDOS-Guard checkbox
]


async def _bypass_method_cdp_gui_click(page) -> bool:
    """CDP Mode with gui_click-style behavior."""
    try:
        logger.debug("Attempting bypass: CDP gui_click (mouse-based)")

        try:
            logger.debug("Trying solve_captcha()")
            await page.solve_captcha()
            await asyncio.sleep(random.uniform(3, 5))

            if await _is_bypassed(page):
                return True
        except Exception as e:
            logger.debug(f"solve_captcha() failed: {e}")

        for selector in CDP_GUI_CLICK_SELECTORS:
            try:
                if not await page.is_element_visible(selector):
                    continue

                logger.debug(f"CDP click_with_offset: {selector}")
                await page.click_with_offset(selector, 0, 0, center=True)
                await asyncio.sleep(random.uniform(3, 5))

                if await _is_bypassed(page):
                    return True
            except Exception as e:
                logger.debug(f"CDP gui_click on '{selector}' failed: {e}")

        return await _is_bypassed(page)
    except Exception as e:
        logger.debug(f"CDP Mode gui_click failed: {e}")
        return False


BYPASS_METHODS = [
    _bypass_method_cdp_solve,
    _bypass_method_cdp_gui_click,
    _bypass_method_cdp_click,
    _bypass_method_humanlike,
]

MAX_CONSECUTIVE_SAME_CHALLENGE = 3


def _check_cancellation(cancel_flag: Optional[Event], message: str) -> None:
    """Check if cancellation was requested and raise if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info(message)
        raise BypassCancelledException("Bypass cancelled")


async def _bypass(page, max_retries: Optional[int] = None, cancel_flag: Optional[Event] = None) -> bool:
    """Attempt to bypass Cloudflare/DDOS-Guard protection using multiple methods."""
    max_retries = max_retries if max_retries is not None else app_config.MAX_RETRY

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
        logger.debug(f"Challenge detected: {challenge_type}")

        # No challenge detected but page doesn't look bypassed - wait and retry
        if challenge_type == "none":
            logger.info("No challenge detected, waiting for page to settle...")
            await asyncio.sleep(random.uniform(2, 3))
            if await _is_bypassed(page):
                return True
            # Try a simple refresh instead of captcha methods
            try:
                await page.reload(ignore_cache=True)
                await asyncio.sleep(random.uniform(1, 2))
                if await _is_bypassed(page):
                    logger.info("Bypass successful after refresh")
                    return True
            except Exception as e:
                logger.debug(f"Refresh during no-challenge wait failed: {e}")
            continue

        if challenge_type == last_challenge_type:
            consecutive_same_challenge += 1
            if consecutive_same_challenge >= min_same_challenge_before_abort:
                logger.warning(
                    f"Same challenge ({challenge_type}) detected {consecutive_same_challenge} times - aborting"
                )
                return False
        else:
            consecutive_same_challenge = 1
        last_challenge_type = challenge_type

        method = BYPASS_METHODS[try_count % len(BYPASS_METHODS)]
        logger.info(f"Bypass attempt {try_count + 1}/{max_retries} using {method.__name__}")

        if try_count > 0:
            wait_time = min(random.uniform(2, 4) * try_count, 12)
            logger.info(f"Waiting {wait_time:.1f}s before trying...")
            for _ in range(int(wait_time)):
                _check_cancellation(cancel_flag, "Bypass cancelled during wait")
                await asyncio.sleep(1)
            await asyncio.sleep(wait_time - int(wait_time))

        try:
            if await method(page):
                logger.info(f"Bypass successful using {method.__name__}")
                return True
        except BypassCancelledException:
            raise
        except Exception as e:
            logger.warning(f"Exception in {method.__name__}: {e}")

        logger.info(f"Bypass method {method.__name__} failed.")

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
        arguments.extend([
            "--enable-logging",
            "--v=1",
            "--log-file=" + str(LOG_DIR / "chrome_browser.log")
        ])

    host_rules = _build_host_resolver_rules()
    if host_rules:
        arguments.append(f'--host-resolver-rules={", ".join(host_rules)}')
        logger.debug(f"Chrome: Using host resolver rules for {len(host_rules)} hosts")
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
                    logger.debug(f"Chrome: Pre-resolved {hostname} -> {ip}")
                else:
                    logger.warning(f"Chrome: No addresses returned for {hostname}")
            except socket.gaierror as e:
                logger.warning(f"Chrome: Could not pre-resolve {hostname}: {e}")
    except Exception as e:
        logger.error_trace(f"Error pre-resolving hostnames for Chrome: {e}")

    return host_rules

DRIVER_RESET_ERRORS = {"ProtocolException", "RuntimeError", "TimeoutError"}


async def _get(url: str, driver, cancel_flag: Optional[Event] = None) -> str:
    """Fetch URL with Cloudflare bypass using a CDP browser."""
    _check_cancellation(cancel_flag, "Bypass cancelled before starting")

    logger.debug(f"CDP_GET: {url}")

    logger.debug("Opening URL with SeleniumBase CDP...")
    page = await driver.get(url)
    try:
        await page.wait()
    except Exception:
        pass

    _check_cancellation(cancel_flag, "Bypass cancelled after page load")

    try:
        current_url = await page.get_current_url()
        title = await page.get_title()
        logger.debug(f"Page loaded - URL: {current_url}, Title: {title}")
    except Exception as e:
        logger.debug(f"Could not get page info: {e}")

    logger.debug("Starting bypass process...")
    if await _bypass(page, cancel_flag=cancel_flag):
        await _extract_cookies_from_cdp(driver, page, url)
        return await page.get_page_source()

    logger.warning("Bypass completed but page still shows protection")
    try:
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        if body:
            logger.debug(f"Page content: {body[:500]}..." if len(body) > 500 else body)
    except Exception:
        pass

    return ""


def get(url: str, retry: Optional[int] = None, cancel_flag: Optional[Event] = None) -> str:
    """Fetch a URL with protection bypass. Creates fresh Chrome instance for each bypass."""
    retry = retry if retry is not None else app_config.MAX_RETRY

    with LOCKED:
        # Try cookies first - another request may have completed bypass while waiting
        cached_result = _try_with_cached_cookies(url, urlparse(url).hostname or "")
        if cached_result:
            return cached_result

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
                    except BypassCancelledException:
                        raise
                    except Exception as e:
                        error_details = f"{type(e).__name__}: {e}"
                        logger.warning(f"Bypass failed (attempt {attempt + 1}/{retry}): {error_details}")
                        logger.debug(f"Stack trace: {traceback.format_exc()}")

                        # On CDP errors, quit and create a fresh browser
                        if type(e).__name__ in DRIVER_RESET_ERRORS:
                            logger.info("Restarting Chrome due to browser error...")
                            await _close_cdp_driver(driver)
                            driver = await _create_cdp_browser(url)

                logger.error(f"Bypass failed after {retry} attempts")
                return ""
            finally:
                if driver:
                    await _close_cdp_driver(driver)

        return _CDP_WORKER.run(_run_bypass())

def _get_proxy_string(url: str) -> Optional[str]:
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

    logger.debug(f"Creating Pure CDP browser with args: {browser_args}")
    logger.debug(f"Browser screen size: {screen_width}x{screen_height}")

    driver = await cdp_driver.start_async(
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
    )

    try:
        await driver.page.set_window_rect(0, 0, screen_width, screen_height)
    except Exception as e:
        logger.debug(f"Failed to set window size: {e}")

    # Start FFmpeg recording if debug mode (record each bypass session)
    if app_config.get("DEBUG", False) and not DISPLAY.get("ffmpeg"):
        _start_ffmpeg_recording(display=os.environ.get("DISPLAY", ":0"))

    await asyncio.sleep(app_config.DEFAULT_SLEEP)
    logger.info("Chrome browser ready (Pure CDP)")
    logger.log_resource_usage()
    return driver


async def _close_cdp_driver(driver) -> None:
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
            try:
                await conn.aclose()
            except Exception as e:
                logger.debug(f"Failed to close websocket connection: {e}")
    except Exception as e:
        logger.debug(f"Error during connection cleanup: {e}")

    try:
        driver.stop()
        logger.debug("Stopped CDP browser")
    except Exception as e:
        logger.debug(f"CDP stop: {e}")

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
                    logger.debug(f"Killed Chrome pid {pid}")
                except Exception as e:
                    logger.debug(f"Failed to kill Chrome pid {pid}: {e}")
        except Exception as e:
            logger.debug(f"Process cleanup failed: {e}")

    logger.log_resource_usage()


def _start_ffmpeg_recording(display: str) -> None:
    """Start FFmpeg screen recording for debug mode."""
    global DISPLAY
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
    output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"

    screen_width, screen_height = get_screen_size()
    display_width = screen_width + 100
    display_height = screen_height + 150

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-f", "x11grab",
        "-video_size", f"{display_width}x{display_height}",
        "-i", display,
        "-c:v", "libx264", "-preset", "ultrafast",
        "-maxrate", "700k", "-bufsize", "1400k", "-crf", "36",
        "-pix_fmt", "yuv420p", "-tune", "animation",
        "-x264-params", "bframes=0:deblock=-1,-1",
        "-r", "15", "-an",
        output_file.as_posix(),
        "-nostats", "-loglevel", "0"
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
    output_file = DISPLAY.get("ffmpeg_output")
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
    except Exception as e:
        logger.debug(f"ffmpeg stop: {e}")
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
    DISPLAY["ffmpeg"] = None
    DISPLAY["ffmpeg_output"] = None


def _try_with_cached_cookies(url: str, hostname: str) -> Optional[str]:
    """Attempt request with cached cookies before using Chrome."""
    cookies = get_cf_cookies_for_domain(hostname)
    if not cookies:
        return None

    try:
        headers = {}
        stored_ua = get_cf_user_agent_for_domain(hostname)
        if stored_ua:
            headers['User-Agent'] = stored_ua

        logger.debug(f"Trying request with cached cookies: {url}")
        response = requests.get(url, cookies=cookies, headers=headers, proxies=get_proxies(url), timeout=(5, 10), verify=get_ssl_verify(url))
        if response.status_code == 200:
            logger.debug("Cached cookies worked, skipped Chrome bypass")
            return response.text
    except Exception:
        pass

    return None


def get_bypassed_page(
    url: str,
    selector: Optional[network.AAMirrorSelector] = None,
    cancel_flag: Optional[Event] = None
) -> Optional[str]:
    """Fetch HTML content from a URL using the internal Cloudflare Bypasser."""
    sel = selector or network.AAMirrorSelector()
    attempt_url = sel.rewrite(url)
    hostname = urlparse(attempt_url).hostname or ""

    cached_result = _try_with_cached_cookies(attempt_url, hostname)
    if cached_result:
        return cached_result

    try:
        response_html = get(attempt_url, cancel_flag=cancel_flag)
    except BypassCancelledException:
        raise
    except Exception:
        _check_cancellation(cancel_flag, "Bypass cancelled")
        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            attempt_url = sel.rewrite(url)
            response_html = get(attempt_url, cancel_flag=cancel_flag)
        else:
            raise

    if not response_html.strip():
        raise requests.exceptions.RequestException("Failed to bypass Cloudflare")

    return response_html

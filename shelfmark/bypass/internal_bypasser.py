import os
import random
import socket
import subprocess
import threading
import time
import traceback
from datetime import datetime
from threading import Event
from typing import Optional
from urllib.parse import urlparse

import requests
from seleniumbase import Driver

from shelfmark.bypass import BypassCancelledException
from shelfmark.bypass.fingerprint import get_screen_size
from shelfmark.config import env
from shelfmark.config.env import LOG_DIR
from shelfmark.config.settings import RECORDING_DIR
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.download import network
from shelfmark.download.network import get_proxies

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
    "xvfb": None,
    "ffmpeg": None,
}
LOCKED = threading.Lock()

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


def _extract_cookies_from_driver(driver, url: str) -> None:
    """Extract cookies from Chrome after successful bypass."""
    try:
        parsed = urlparse(url)
        domain = parsed.hostname or ""
        if not domain:
            return

        base_domain = _get_base_domain(domain)
        extract_all = base_domain in FULL_COOKIE_DOMAINS

        cookies_found = {}
        for cookie in driver.get_cookies():
            name = cookie.get('name', '')
            if _should_extract_cookie(name, extract_all):
                cookies_found[name] = {
                    'value': cookie.get('value', ''),
                    'domain': cookie.get('domain', domain),
                    'path': cookie.get('path', '/'),
                    'expiry': cookie.get('expiry'),
                    'secure': cookie.get('secure', True),
                    'httpOnly': cookie.get('httpOnly', True),
                }

        if not cookies_found:
            return

        try:
            user_agent = driver.execute_script("return navigator.userAgent")
        except Exception:
            user_agent = None

        with _cf_cookies_lock:
            _cf_cookies[base_domain] = cookies_found
            if user_agent:
                _cf_user_agents[base_domain] = user_agent
                logger.debug(f"Stored UA for {base_domain}: {user_agent[:60]}...")
            else:
                logger.debug(f"No UA captured for {base_domain}")

        cookie_type = "all" if extract_all else "protection"
        logger.debug(f"Extracted {len(cookies_found)} {cookie_type} cookies for {base_domain}")

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
            if expiry and time.time() > expiry:
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


def _reset_pyautogui_display_state():
    try:
        import pyautogui
        import Xlib.display
        pyautogui._pyautogui_x11._display = Xlib.display.Display(os.environ['DISPLAY'])
    except Exception as e:
        logger.warning(f"Error resetting pyautogui display state: {e}")


def _cleanup_orphan_processes() -> int:
    """Kill orphan Chrome/Xvfb/ffmpeg processes. Only runs in Docker mode."""
    if not env.DOCKERMODE:
        return 0

    processes_to_kill = ["chrome", "chromedriver", "Xvfb", "ffmpeg"]
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

def _get_page_info(sb) -> tuple[str, str, str]:
    """Extract page title, body text, and current URL safely."""
    try:
        title = sb.get_title().lower()
    except Exception:
        title = ""
    try:
        body = sb.get_text("body").lower()
    except Exception:
        body = ""
    try:
        current_url = sb.get_current_url()
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

def _detect_challenge_type(sb) -> str:
    """Detect challenge type: 'cloudflare', 'ddos_guard', or 'none'."""
    try:
        title, body, current_url = _get_page_info(sb)
        
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

def _is_bypassed(sb, escape_emojis: bool = True) -> bool:
    """Check if the protection has been bypassed."""
    try:
        title, body, current_url = _get_page_info(sb)
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

def _simulate_human_behavior(sb) -> None:
    """Simulate human-like behavior before bypass attempt."""
    try:
        time.sleep(random.uniform(0.5, 1.5))

        if random.random() < 0.3:
            sb.scroll_down(random.randint(20, 50))
            time.sleep(random.uniform(0.2, 0.5))
            sb.scroll_up(random.randint(10, 30))
            time.sleep(random.uniform(0.2, 0.4))

        try:
            import pyautogui
            x, y = pyautogui.position()
            pyautogui.moveTo(
                x + random.randint(-10, 10),
                y + random.randint(-10, 10),
                duration=random.uniform(0.05, 0.15)
            )
        except Exception as e:
            logger.debug(f"Mouse jiggle failed: {e}")
    except Exception as e:
        logger.debug(f"Human simulation failed: {e}")


def _bypass_method_handle_captcha(sb) -> bool:
    """Method 2: Use uc_gui_handle_captcha() - TAB+SPACEBAR approach, stealthier than click."""
    try:
        logger.debug("Attempting bypass: uc_gui_handle_captcha (TAB+SPACEBAR)")
        _simulate_human_behavior(sb)
        sb.uc_gui_handle_captcha()
        time.sleep(random.uniform(3, 5))
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"uc_gui_handle_captcha failed: {e}")
        return False


def _bypass_method_click_captcha(sb) -> bool:
    """Method 3: Use uc_gui_click_captcha() - direct click via PyAutoGUI."""
    try:
        logger.debug("Attempting bypass: uc_gui_click_captcha (direct click)")
        _simulate_human_behavior(sb)
        sb.uc_gui_click_captcha()
        time.sleep(random.uniform(3, 5))

        if _is_bypassed(sb):
            return True

        # Retry once with longer wait
        logger.debug("First click attempt failed, retrying...")
        time.sleep(random.uniform(4, 6))
        sb.uc_gui_click_captcha()
        time.sleep(random.uniform(3, 5))
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"uc_gui_click_captcha failed: {e}")
        return False


def _bypass_method_humanlike(sb) -> bool:
    """Human-like behavior with scroll, wait, and reload."""
    try:
        logger.debug("Attempting bypass: human-like interaction")
        time.sleep(random.uniform(6, 10))

        try:
            sb.scroll_to_bottom()
            time.sleep(random.uniform(1, 2))
            sb.scroll_to_top()
            time.sleep(random.uniform(2, 3))
        except Exception as e:
            logger.debug(f"Scroll behavior failed: {e}")

        if _is_bypassed(sb):
            return True

        logger.debug("Trying page refresh...")
        sb.refresh()
        time.sleep(random.uniform(5, 8))

        if _is_bypassed(sb):
            return True

        try:
            sb.uc_gui_click_captcha()
            time.sleep(random.uniform(3, 5))
        except Exception as e:
            logger.debug(f"Final captcha click failed: {e}")

        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"Human-like method failed: {e}")
        return False


def _safe_reconnect(sb) -> None:
    """Safely attempt to reconnect WebDriver after CDP mode."""
    try:
        sb.reconnect()
    except Exception as e:
        logger.debug(f"Reconnect failed: {e}")


def _bypass_method_cdp_solve(sb) -> bool:
    """CDP Mode with solve_captcha() - WebDriver disconnected, no PyAutoGUI.

    CDP Mode disconnects WebDriver during interaction, making detection harder.
    The solve_captcha() method auto-detects challenge type.
    """
    try:
        logger.debug("Attempting bypass: CDP Mode solve_captcha")
        sb.activate_cdp_mode(sb.get_current_url())
        time.sleep(random.uniform(1, 2))

        try:
            sb.cdp.solve_captcha()
            time.sleep(random.uniform(3, 5))
            sb.reconnect()
            time.sleep(random.uniform(1, 2))

            if _is_bypassed(sb):
                return True
        except Exception as e:
            logger.debug(f"CDP solve_captcha failed: {e}")
            _safe_reconnect(sb)

        return False
    except Exception as e:
        logger.debug(f"CDP Mode solve failed: {e}")
        _safe_reconnect(sb)
        return False


CDP_CLICK_SELECTORS = [
    "#turnstile-widget div",      # Cloudflare Turnstile
    "#cf-turnstile div",          # Alternative CF Turnstile
    "iframe[src*='challenges']",  # CF challenge iframe
    "input[type='checkbox']",     # Generic checkbox (DDOS-Guard)
    "[class*='checkbox']",        # Class-based checkbox
    "#challenge-running",         # CF challenge indicator
]


def _bypass_method_cdp_click(sb) -> bool:
    """CDP Mode with native clicking - no PyAutoGUI dependency.

    Uses sb.cdp.click() which is native CDP clicking (SeleniumBase 4.45.6+).
    """
    try:
        logger.debug("Attempting bypass: CDP Mode native click")
        sb.activate_cdp_mode(sb.get_current_url())
        time.sleep(random.uniform(1, 2))

        for selector in CDP_CLICK_SELECTORS:
            try:
                if not sb.cdp.is_element_visible(selector):
                    continue

                logger.debug(f"CDP clicking: {selector}")
                sb.cdp.click(selector)
                time.sleep(random.uniform(2, 4))

                sb.reconnect()
                time.sleep(random.uniform(1, 2))

                if _is_bypassed(sb):
                    return True

                sb.activate_cdp_mode(sb.get_current_url())
                time.sleep(random.uniform(0.5, 1))
            except Exception as e:
                logger.debug(f"CDP click on '{selector}' failed: {e}")

        _safe_reconnect(sb)
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"CDP Mode click failed: {e}")
        _safe_reconnect(sb)
        return False


CDP_GUI_CLICK_SELECTORS = [
    "#turnstile-widget div",      # Cloudflare Turnstile
    "#cf-turnstile div",          # Alternative CF Turnstile
    "#challenge-stage div",       # CF challenge stage
    "input[type='checkbox']",     # Generic checkbox
    "[class*='cb-i']",            # DDOS-Guard checkbox
]


def _bypass_method_cdp_gui_click(sb) -> bool:
    """CDP Mode with PyAutoGUI-based clicking - uses actual mouse movement.

    Most human-like approach for advanced protections (Kasada, DataDome, Akamai).
    """
    try:
        logger.debug("Attempting bypass: CDP Mode gui_click (mouse-based)")
        sb.activate_cdp_mode(sb.get_current_url())
        time.sleep(random.uniform(1, 2))

        try:
            logger.debug("Trying cdp.gui_click_captcha()")
            sb.cdp.gui_click_captcha()
            time.sleep(random.uniform(3, 5))

            sb.reconnect()
            time.sleep(random.uniform(1, 2))

            if _is_bypassed(sb):
                return True

            sb.activate_cdp_mode(sb.get_current_url())
            time.sleep(random.uniform(0.5, 1))
        except Exception as e:
            logger.debug(f"cdp.gui_click_captcha() failed: {e}")

        for selector in CDP_GUI_CLICK_SELECTORS:
            try:
                if not sb.cdp.is_element_visible(selector):
                    continue

                logger.debug(f"CDP gui_click_element: {selector}")
                sb.cdp.gui_click_element(selector)
                time.sleep(random.uniform(3, 5))

                sb.reconnect()
                time.sleep(random.uniform(1, 2))

                if _is_bypassed(sb):
                    return True

                sb.activate_cdp_mode(sb.get_current_url())
                time.sleep(random.uniform(0.5, 1))
            except Exception as e:
                logger.debug(f"CDP gui_click on '{selector}' failed: {e}")

        _safe_reconnect(sb)
        return _is_bypassed(sb)
    except Exception as e:
        logger.debug(f"CDP Mode gui_click failed: {e}")
        _safe_reconnect(sb)
        return False


BYPASS_METHODS = [
    _bypass_method_cdp_solve,
    _bypass_method_cdp_click,
    _bypass_method_cdp_gui_click,
    _bypass_method_handle_captcha,
    _bypass_method_click_captcha,
    _bypass_method_humanlike,
]

MAX_CONSECUTIVE_SAME_CHALLENGE = 3


def _check_cancellation(cancel_flag: Optional[Event], message: str) -> None:
    """Check if cancellation was requested and raise if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info(message)
        raise BypassCancelledException("Bypass cancelled")


def _bypass(sb, max_retries: Optional[int] = None, cancel_flag: Optional[Event] = None) -> bool:
    """Attempt to bypass Cloudflare/DDOS-Guard protection using multiple methods."""
    max_retries = max_retries if max_retries is not None else app_config.MAX_RETRY

    last_challenge_type = None
    consecutive_same_challenge = 0
    # Allow at least one full pass through all bypass methods before aborting due to a "stuck" challenge.
    min_same_challenge_before_abort = max(MAX_CONSECUTIVE_SAME_CHALLENGE, len(BYPASS_METHODS) + 1)

    for try_count in range(max_retries):
        _check_cancellation(cancel_flag, "Bypass cancelled by user")

        if _is_bypassed(sb):
            if try_count == 0:
                logger.info("Page already bypassed")
            return True

        challenge_type = _detect_challenge_type(sb)
        logger.debug(f"Challenge detected: {challenge_type}")

        # No challenge detected but page doesn't look bypassed - wait and retry
        if challenge_type == "none":
            logger.info("No challenge detected, waiting for page to settle...")
            time.sleep(random.uniform(2, 3))
            if _is_bypassed(sb):
                return True
            # Try a simple reconnect instead of captcha methods
            try:
                sb.reconnect()
                time.sleep(random.uniform(1, 2))
                if _is_bypassed(sb):
                    logger.info("Bypass successful after reconnect")
                    return True
            except Exception as e:
                logger.debug(f"Reconnect during no-challenge wait failed: {e}")
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
                time.sleep(1)
            time.sleep(wait_time - int(wait_time))

        try:
            if method(sb):
                logger.info(f"Bypass successful using {method.__name__}")
                return True
        except BypassCancelledException:
            raise
        except Exception as e:
            logger.warning(f"Exception in {method.__name__}: {e}")

        logger.info(f"Bypass method {method.__name__} failed.")

    logger.warning("Exceeded maximum retries. Bypass failed.")
    return False

def _get_chromium_args() -> list[str]:
    """Build Chrome arguments, pre-resolving hostnames via Python's patched DNS.

    Pre-resolves AA hostnames and passes IPs to Chrome via --host-resolver-rules,
    bypassing Chrome's DNS entirely for those hosts.
    """
    arguments = [
        "--ignore-certificate-errors",
        "--ignore-ssl-errors",
        "--allow-running-insecure-content",
        "--ignore-certificate-errors-spki-list",
        "--ignore-certificate-errors-skip-list"
    ]

    if app_config.get("DEBUG", False):
        arguments.extend([
            "--enable-logging",
            "--v=1",
            "--log-file=" + str(LOG_DIR / "chrome_browser.log")
        ])

    proxies = get_proxies()
    if proxies:
        proxy_url = proxies.get('https') or proxies.get('http')
        if proxy_url:
            arguments.append(f'--proxy-server={proxy_url}')

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

DRIVER_RESET_ERRORS = {"WebDriverException", "SessionNotCreatedException", "TimeoutException", "MaxRetryError"}


def _get(url: str, driver: Driver, cancel_flag: Optional[Event] = None) -> str:
    """Fetch URL with Cloudflare bypass using provided driver."""
    _check_cancellation(cancel_flag, "Bypass cancelled before starting")

    logger.debug(f"SB_GET: {url}")

    hostname = urlparse(url).hostname or ""
    if has_valid_cf_cookies(hostname):
        reconnect_time = 1.0
        logger.debug(f"Using fast reconnect ({reconnect_time}s) - valid cookies exist")
    else:
        reconnect_time = app_config.DEFAULT_SLEEP
        logger.debug(f"Using standard reconnect ({reconnect_time}s) - no cached cookies")

    logger.debug("Opening URL with SeleniumBase...")
    driver.uc_open_with_reconnect(url, reconnect_time)

    _check_cancellation(cancel_flag, "Bypass cancelled after page load")

    try:
        logger.debug(f"Page loaded - URL: {driver.get_current_url()}, Title: {driver.get_title()}")
    except Exception as e:
        logger.debug(f"Could not get page info: {e}")

    logger.debug("Starting bypass process...")
    if _bypass(driver, cancel_flag=cancel_flag):
        _extract_cookies_from_driver(driver, url)
        return driver.page_source

    logger.warning("Bypass completed but page still shows protection")
    try:
        body = driver.get_text("body")
        logger.debug(f"Page content: {body[:500]}..." if len(body) > 500 else body)
    except Exception:
        pass

    return ""


def get(url: str, retry: Optional[int] = None, cancel_flag: Optional[Event] = None) -> str:
    """Fetch a URL with protection bypass. Creates fresh Chrome instance for each bypass."""
    retry = retry if retry is not None else app_config.MAX_RETRY

    with LOCKED:
        # Try cookies first - another request may have completed bypass while waiting
        cookies = get_cf_cookies_for_domain(urlparse(url).hostname or "")
        if cookies:
            try:
                response = requests.get(url, cookies=cookies, proxies=get_proxies(url), timeout=(5, 10))
                if response.status_code == 200:
                    logger.debug("Cookies available after lock wait - skipped Chrome")
                    return response.text
            except Exception:
                pass

        # Fresh Chrome for each bypass attempt
        driver = None
        try:
            _ensure_display_initialized()
            driver = _create_driver()

            for attempt in range(retry):
                _check_cancellation(cancel_flag, "Bypass cancelled before attempt")

                try:
                    result = _get(url, driver, cancel_flag)
                    if result:
                        return result
                except BypassCancelledException:
                    raise
                except Exception as e:
                    error_details = f"{type(e).__name__}: {e}"
                    logger.warning(f"Bypass failed (attempt {attempt + 1}/{retry}): {error_details}")
                    logger.debug(f"Stack trace: {traceback.format_exc()}")

                    # On driver errors, quit and create fresh driver
                    if type(e).__name__ in DRIVER_RESET_ERRORS:
                        logger.info("Restarting Chrome due to browser error...")
                        _quit_driver(driver)
                        driver = _create_driver()

            logger.error(f"Bypass failed after {retry} attempts")
            return ""
        finally:
            # Always quit Chrome when done
            if driver:
                _quit_driver(driver)

def _create_driver() -> Driver:
    """Create a fresh Chrome driver instance."""
    chromium_args = _get_chromium_args()
    screen_width, screen_height = get_screen_size()

    logger.debug(f"Creating Chrome driver with args: {chromium_args}")
    logger.debug(f"Browser screen size: {screen_width}x{screen_height}")

    # Start FFmpeg recording if debug mode (record each bypass session)
    if app_config.get("DEBUG", False) and DISPLAY["xvfb"] and not DISPLAY["ffmpeg"]:
        _start_ffmpeg_recording()

    driver = Driver(
        uc=True,
        headless=False,
        incognito=True,
        locale="en",
        ad_block=True,
        size=f"{screen_width},{screen_height}",
        chromium_arg=chromium_args,
    )
    driver.set_page_load_timeout(60)
    time.sleep(app_config.DEFAULT_SLEEP)
    logger.info("Chrome browser ready")
    logger.log_resource_usage()
    return driver


def _start_ffmpeg_recording() -> None:
    """Start FFmpeg screen recording for debug mode."""
    global DISPLAY
    RECORDING_DIR.mkdir(parents=True, exist_ok=True)
    display = DISPLAY["xvfb"]
    timestamp = datetime.now().strftime("%y%m%d-%H%M%S")
    output_file = RECORDING_DIR / f"screen_recording_{timestamp}.mp4"

    screen_width, screen_height = get_screen_size()
    display_width = screen_width + 100
    display_height = screen_height + 150

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-f", "x11grab",
        "-video_size", f"{display_width}x{display_height}",
        "-i", f":{display.display}",
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


def _close_cdp_sockets() -> int:
    """Find and close any sockets connected to CDP port 9222.

    This is a workaround for SeleniumBase not properly closing websocket
    connections when using activate_cdp_mode(). Returns count of closed sockets.
    """
    import os
    closed = 0
    pid = os.getpid()

    try:
        fd_path = f'/proc/{pid}/fd'
        for fd_name in os.listdir(fd_path):
            try:
                fd = int(fd_name)
                link = os.readlink(f'{fd_path}/{fd_name}')
                if 'socket:' not in link:
                    continue

                # Check if this socket is connected to port 9222 (CDP)
                # by reading /proc/net/tcp and matching inode
                inode = link.split('[')[1].rstrip(']')

                with open('/proc/net/tcp', 'r') as f:
                    for line in f:
                        parts = line.split()
                        if len(parts) < 10:
                            continue
                        # Check if this is our socket and connects to port 9222 (0x2406)
                        if parts[9] == inode:
                            remote = parts[2]
                            remote_port = int(remote.split(':')[1], 16)
                            if remote_port == 9222:
                                logger.debug(f"Closing CDP socket fd={fd} inode={inode}")
                                os.close(fd)
                                closed += 1
                                break
            except (ValueError, OSError, IndexError):
                continue
    except Exception as e:
        logger.debug(f"Error scanning for CDP sockets: {e}")

    return closed


def _quit_driver(driver: Driver) -> None:
    """Quit Chrome driver and clean up resources.

    Proper cleanup sequence for SeleniumBase CDP mode:
    1. Stop CDP browser (closes websocket connections)
    2. Reconnect WebDriver
    3. Close window
    4. Quit driver
    5. Force-kill any lingering processes

    The CDP websocket connection must be explicitly closed before Chrome is killed,
    otherwise the sockets end up in CLOSE_WAIT state causing gevent to busy-loop.

    References:
    - https://github.com/seleniumbase/SeleniumBase/discussions/3768
    - https://www.selenium.dev/selenium/docs/api/py/selenium_webdriver_common_bidi/selenium.webdriver.common.bidi.cdp.html
    """
    if driver is None:
        return

    logger.debug("Quitting Chrome driver...")

    # Strategy 1: Stop CDP browser if in CDP mode (closes websocket connections)
    # This is the proper SeleniumBase way to close CDP connections
    try:
        if hasattr(driver, 'cdp') and driver.cdp and hasattr(driver.cdp, 'driver'):
            driver.cdp.driver.stop()
            logger.debug("Stopped CDP browser (closed websocket)")
            time.sleep(0.3)
    except Exception as e:
        logger.debug(f"CDP stop: {e}")

    # Strategy 2: Reconnect to re-establish WebDriver control before quitting
    try:
        driver.reconnect()
        time.sleep(0.2)
    except Exception as e:
        logger.debug(f"Reconnect: {e}")

    # Strategy 3: Close the current window/tab
    try:
        driver.close()
        time.sleep(0.2)
    except Exception as e:
        logger.debug(f"Close window: {e}")

    # Strategy 4: Fallback - explicitly close any remaining CDP sockets
    # This catches any sockets that weren't closed by cdp.driver.stop()
    closed = _close_cdp_sockets()
    if closed:
        logger.debug(f"Closed {closed} remaining CDP socket(s)")

    # Strategy 5: Standard quit
    try:
        driver.quit()
    except Exception as e:
        logger.debug(f"Quit: {e}")

    # Strategy 6: Force garbage collection
    import gc
    gc.collect()

    # Strategy 7: Force-kill any lingering Chrome/chromedriver processes
    if env.DOCKERMODE:
        time.sleep(0.3)
        try:
            subprocess.run(["pkill", "-9", "-f", "chrom"], capture_output=True, timeout=5)
        except Exception as e:
            logger.debug(f"pkill chrome: {e}")

    # Strategy 8: Stop ffmpeg recording if running
    global DISPLAY
    if DISPLAY.get("ffmpeg"):
        try:
            DISPLAY["ffmpeg"].terminate()
            DISPLAY["ffmpeg"].wait(timeout=2)
            logger.debug("Stopped ffmpeg recording")
        except Exception as e:
            logger.debug(f"ffmpeg terminate: {e}")
            try:
                DISPLAY["ffmpeg"].kill()
            except Exception:
                pass
        DISPLAY["ffmpeg"] = None

    logger.log_resource_usage()


def _ensure_display_initialized():
    """Initialize virtual display if needed. Must be called with LOCKED held."""
    global DISPLAY
    if DISPLAY["xvfb"] is not None:
        return
    if not (env.DOCKERMODE and app_config.get("USE_CF_BYPASS", True)):
        return

    from pyvirtualdisplay import Display
    # Get the screen size (generates a random one if not already set)
    screen_width, screen_height = get_screen_size()
    # Add padding for browser chrome (title bar, borders, taskbar space)
    display_width = screen_width + 100
    display_height = screen_height + 150
    display = Display(visible=False, size=(display_width, display_height))
    display.start()
    DISPLAY["xvfb"] = display
    logger.info(f"Virtual display started: {display_width}x{display_height}")
    time.sleep(app_config.DEFAULT_SLEEP)
    _reset_pyautogui_display_state()


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
        response = requests.get(url, cookies=cookies, headers=headers, proxies=get_proxies(url), timeout=(5, 10))
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

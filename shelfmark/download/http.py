"""HTTP download with retry, resume, and Cloudflare bypass support."""

import random
import time
from io import BytesIO
from threading import Event, Thread
from typing import Callable, Optional
from urllib.parse import urlparse, urljoin

import requests
from tqdm import tqdm

from shelfmark.download import network
from shelfmark.download.network import get_proxies, get_ssl_verify
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Bypasser modules are imported lazily to support dynamic selection based on config
_internal_bypasser = None
_external_bypasser = None


def _get_internal_bypasser():
    """Lazy import of internal bypasser module."""
    global _internal_bypasser
    if _internal_bypasser is None:
        try:
            from shelfmark.bypass import internal_bypasser
            _internal_bypasser = internal_bypasser
        except ImportError as e:
            raise RuntimeError(
                f"Failed to import internal bypasser: {e}. "
                "Check that all dependencies are installed. "
                "You may need to disable CF bypass or use the external bypasser."
            ) from e
    return _internal_bypasser


def _get_external_bypasser():
    """Lazy import of external bypasser module."""
    global _external_bypasser
    if _external_bypasser is None:
        try:
            from shelfmark.bypass import external_bypasser
            _external_bypasser = external_bypasser
        except ImportError as e:
            raise RuntimeError(
                f"Failed to import external bypasser: {e}. "
                "Check that the external bypasser is properly configured."
            ) from e
    return _external_bypasser


def _is_using_external_bypasser() -> bool:
    """Check if external bypasser is configured (reads from config, not just env)."""
    return app_config.get("USING_EXTERNAL_BYPASSER", False)


def _is_cf_bypass_enabled() -> bool:
    """Check if Cloudflare bypass is enabled."""
    return app_config.get("USE_CF_BYPASS", True)


def get_bypassed_page(url, selector=None, cancel_flag=None):
    """Wrapper that delegates to the appropriate bypasser based on config."""
    if _is_using_external_bypasser():
        return _get_external_bypasser().get_bypassed_page(url, selector, cancel_flag)
    return _get_internal_bypasser().get_bypassed_page(url, selector, cancel_flag)


def get_cf_cookies_for_domain(domain):
    """Get CF cookies - only available with internal bypasser."""
    if _is_using_external_bypasser():
        logger.debug(f"External bypasser in use, CF cookies not available for {domain}")
        return {}
    return _get_internal_bypasser().get_cf_cookies_for_domain(domain)


def get_cf_user_agent_for_domain(domain):
    """Get CF user agent - only available with internal bypasser."""
    if _is_using_external_bypasser():
        logger.debug(f"External bypasser in use, CF user agent not available for {domain}")
        return None
    return _get_internal_bypasser().get_cf_user_agent_for_domain(domain)


def _apply_cf_bypass(url: str, headers: dict) -> dict:
    """Apply CF bypass cookies and user agent if available.

    Modifies headers in-place with the stored user agent (if available).
    Returns cookies dict to use with the request.
    """
    if not _is_cf_bypass_enabled():
        return {}

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    cookies = get_cf_cookies_for_domain(hostname)
    stored_ua = get_cf_user_agent_for_domain(hostname)
    if stored_ua:
        headers['User-Agent'] = stored_ua
    return cookies


# Network settings
REQUEST_TIMEOUT = (5, 10)  # (connect, read)
MAX_DOWNLOAD_RETRIES = 2
MAX_RESUME_ATTEMPTS = 3

RETRYABLE_CODES = (429, 500, 502, 503, 504)
CONNECTION_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                     requests.exceptions.SSLError, requests.exceptions.ChunkedEncodingError)
DOWNLOAD_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}


def parse_size_string(size: str) -> Optional[float]:
    """Parse a human-readable size string (e.g., '10.5 MB') into bytes."""
    if not size:
        return None
    try:
        normalized = size.strip().replace(" ", "").replace(",", ".").upper()
        multipliers = {"GB": 1024**3, "MB": 1024**2, "KB": 1024}
        for suffix, mult in multipliers.items():
            if normalized.endswith(suffix):
                return float(normalized[:-2]) * mult
        return float(normalized)
    except (ValueError, IndexError):
        return None

def _backoff_delay(attempt: int, base: float = 0.25, cap: float = 3.0) -> float:
    """Exponential backoff with jitter."""
    return min(cap, base * (2 ** (attempt - 1))) + random.random() * base


def _get_status_code(e: Exception) -> Optional[int]:
    """Extract HTTP status code from an exception, or None if not applicable."""
    if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
        return e.response.status_code
    return None

def _is_retryable_error(e: Exception) -> bool:
    """Check if error is retryable (connection error or retryable HTTP status)."""
    if isinstance(e, CONNECTION_ERRORS):
        return True
    status = _get_status_code(e)
    return status is not None and status in RETRYABLE_CODES


def _try_rotation(original_url: str, current_url: str, selector: network.AAMirrorSelector) -> Optional[str]:
    """Try mirror/DNS rotation. Returns new URL or None."""
    if current_url.startswith(network.get_aa_base_url()):
        new_base, action = selector.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            new_url = selector.rewrite(original_url)
            logger.info(f"[{action}] switching to: {new_url}")
            return new_url
    elif network.should_rotate_dns_for_url(current_url) and network.rotate_dns_provider():
        logger.info(f"[dns-rotate] retrying: {original_url}")
        return original_url
    return None


def html_get_page(
    url: str,
    retry: Optional[int] = None,
    use_bypasser: bool = False,
    selector: Optional[network.AAMirrorSelector] = None,
    cancel_flag: Optional[Event] = None,
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None,
    allow_bypasser_fallback: bool = True,
    include_response_url: bool = False,
    success_delay: float = 1.0,
    session: Optional[requests.Session] = None,
) -> str | tuple[str, str]:
    """Fetch HTML content from a URL with retry mechanism.

    Args:
        allow_bypasser_fallback: If False, 403 errors will trigger mirror rotation
            instead of switching to the bypasser. Use for search operations.
        include_response_url: If True, return `(html, final_url)` to expose the
            resolved response URL after redirects.
        success_delay: Optional delay (seconds) after successful fetch.
    """
    def _result(html: str, response_url: str) -> str | tuple[str, str]:
        if include_response_url:
            return html, response_url
        return html

    retry = retry if retry is not None else app_config.MAX_RETRY
    selector = selector or network.AAMirrorSelector()
    original_url = url
    current_url = selector.rewrite(original_url)
    use_bypasser_now = use_bypasser

    for attempt in range(1, retry + 1):
        # Check for cancellation before each attempt
        if cancel_flag and cancel_flag.is_set():
            logger.info(f"html_get_page cancelled before attempt {attempt}")
            return _result("", current_url)

        try:
            if use_bypasser_now and _is_cf_bypass_enabled():
                logger.debug(f"GET (bypasser): {current_url}")
                if status_callback:
                    status_callback("resolving", "Bypassing protection...")
                heartbeat_stop = Event()
                heartbeat_thread: Optional[Thread] = None
                if status_callback:
                    def _heartbeat() -> None:
                        # Keep the download "alive" during long bypass operations so the orchestrator
                        # doesn't flag it as stalled.
                        while not heartbeat_stop.wait(timeout=30):
                            if cancel_flag and cancel_flag.is_set():
                                return
                            try:
                                status_callback("resolving", "Bypassing protection...")
                            except Exception:
                                return
                    heartbeat_thread = Thread(target=_heartbeat, daemon=True, name="BypassHeartbeat")
                    heartbeat_thread.start()
                try:
                    result = get_bypassed_page(current_url, selector, cancel_flag)
                    return _result(result or "", current_url)
                except Exception as e:
                    logger.warning(f"Bypasser error: {type(e).__name__}: {e}")
                    return _result("", current_url)
                finally:
                    heartbeat_stop.set()
                    if heartbeat_thread:
                        heartbeat_thread.join(timeout=1)

            logger.debug(f"GET: {current_url}")

            # Use a browser-like UA by default (AA can behave differently for python-requests UA).
            headers = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"]}

            # AA mirrors sometimes redirect to other (seized/dead) mirror domains. If we let
            # requests follow those redirects, the request fails on DNS and we rotate away
            # from an otherwise working mirror. Handle AA redirects manually instead.
            is_aa_url = network.should_rotate_dns_for_url(current_url)
            allow_redirects = not is_aa_url

            redirects_followed = 0
            while True:
                # Try with CF cookies/UA if available (from previous bypass)
                cookies = _apply_cf_bypass(current_url, headers)
                request_client = session or requests
                response = request_client.get(
                    current_url,
                    proxies=get_proxies(current_url),
                    timeout=REQUEST_TIMEOUT,
                    cookies=cookies,
                    headers=headers,
                    allow_redirects=allow_redirects,
                    verify=get_ssl_verify(current_url),
                )

                if is_aa_url and response.is_redirect:
                    location = response.headers.get("Location", "")
                    if not location:
                        raise requests.exceptions.TooManyRedirects(f"Redirect with no Location header: {current_url}")

                    redirect_url = urljoin(current_url, location)
                    current_host = urlparse(current_url).hostname or ""
                    redirect_host = urlparse(redirect_url).hostname or ""

                    # If an AA mirror redirects to a different hostname, treat that as a mirror
                    # failure and rotate rather than following the redirect (auto mode only).
                    if current_host and redirect_host and current_host != redirect_host:
                        if not network.is_aa_auto_mode():
                            logger.warning(
                                "AA mirror locked to %s but redirected to %s: %s",
                                current_host,
                                redirect_host,
                                current_url,
                            )
                            return _result("", current_url)

                        new_url = _try_rotation(original_url, current_url, selector)
                        if new_url:
                            current_url = new_url
                            # Reset per-request state for the new host.
                            headers = {"User-Agent": DOWNLOAD_HEADERS["User-Agent"]}
                            is_aa_url = network.should_rotate_dns_for_url(current_url)
                            allow_redirects = not is_aa_url
                            redirects_followed = 0
                            continue

                        logger.warning(
                            "AA redirect from %s to %s but mirrors exhausted: %s",
                            current_host,
                            redirect_host,
                            current_url,
                        )
                        return _result("", current_url)

                    # Same-host redirect (relative or absolute) - follow manually.
                    redirects_followed += 1
                    if redirects_followed > 5:
                        raise requests.exceptions.TooManyRedirects(f"Too many redirects for {current_url}")
                    current_url = redirect_url
                    continue

                response.raise_for_status()
                if success_delay > 0:
                    time.sleep(success_delay)
                return _result(response.text, response.url)

        except Exception as e:
            status = _get_status_code(e)

            # 403 = Cloudflare/DDoS-Guard protection
            if status == 403:
                # If bypasser fallback is disabled, try mirrors instead
                if not allow_bypasser_fallback:
                    new_url = _try_rotation(original_url, current_url, selector)
                    if new_url:
                        current_url = new_url
                        continue
                    logger.warning(f"403 error, mirrors exhausted: {current_url}")
                    return _result("", current_url)

                if _is_cf_bypass_enabled() and not use_bypasser_now:
                    # Before switching to bypasser, check if cookies have become available
                    # (another concurrent download may have completed bypass and extracted cookies)
                    parsed = urlparse(current_url)
                    fresh_cookies = get_cf_cookies_for_domain(parsed.hostname or "")
                    if fresh_cookies and not cookies:
                        # Cookies are now available - retry with cookies before using bypasser
                        logger.debug(f"403 but cookies now available - retrying with cookies: {current_url}")
                        continue
                    logger.info(f"403 detected; switching to bypasser: {current_url}")
                    if status_callback:
                        status_callback("resolving", "Bypassing protection...")
                    use_bypasser_now = True
                    continue
                logger.warning(f"403 error, giving up: {current_url}")
                return _result("", current_url)

            # 404 = Not found
            if status == 404:
                logger.warning(f"404 error: {current_url}")
                return _result("", current_url)

            # Try mirror/DNS rotation on retryable errors
            if _is_retryable_error(e):
                new_url = _try_rotation(original_url, current_url, selector)
                if new_url:
                    current_url = new_url
                    continue

            # Retry with backoff
            if attempt < retry:
                logger.warning(f"Retry {attempt}/{retry} for {current_url}: {type(e).__name__}: {e}")
                time.sleep(_backoff_delay(attempt))
            else:
                logger.error(f"Giving up after {retry} attempts: {current_url}")

    return _result("", current_url)


def download_url(
    link: str,
    size: str = "",
    progress_callback: Optional[Callable[[float], None]] = None,
    cancel_flag: Optional[Event] = None,
    _selector: Optional[network.AAMirrorSelector] = None,
    status_callback: Optional[Callable[[str, Optional[str]], None]] = None,
    referer: Optional[str] = None,
) -> Optional[BytesIO]:
    """Download content from URL with automatic retry and resume support."""
    selector = _selector or network.AAMirrorSelector()
    current_url = selector.rewrite(link)

    # Build headers with optional referer
    headers = DOWNLOAD_HEADERS.copy()
    if referer:
        headers['Referer'] = referer
    total_size = parse_size_string(size) or 0

    attempt = 0
    zlib_cookie_refresh_attempted = False

    while attempt < MAX_DOWNLOAD_RETRIES:
        if cancel_flag and cancel_flag.is_set():
            return None

        buffer = BytesIO()
        bytes_downloaded = 0

        try:
            if attempt > 0 and status_callback:
                status_callback("resolving", f"Connecting (Attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})")

            logger.info(f"Downloading: {current_url} (attempt {attempt + 1}/{MAX_DOWNLOAD_RETRIES})")
            # Try with CF cookies/UA if available
            cookies = _apply_cf_bypass(current_url, headers)
            response = requests.get(current_url, stream=True, proxies=get_proxies(current_url), timeout=REQUEST_TIMEOUT, cookies=cookies, headers=headers, verify=get_ssl_verify(current_url))
            response.raise_for_status()

            if status_callback:
                status_callback("downloading", "")

            total_size = total_size or float(response.headers.get('content-length', 0))
            pbar = tqdm(total=total_size, unit='B', unit_scale=True, desc='Downloading')

            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    buffer.write(chunk)
                    bytes_downloaded += len(chunk)
                    pbar.update(len(chunk))
                    if progress_callback and total_size > 0:
                        progress_callback(bytes_downloaded * 100.0 / total_size)
                    if cancel_flag and cancel_flag.is_set():
                        pbar.close()
                        return None
            pbar.close()

            # Validate - check we didn't get HTML instead of file
            if total_size > 0 and bytes_downloaded < total_size * 0.9:
                if response.headers.get('content-type', '').startswith('text/html'):
                    logger.warning(f"Received HTML instead of file: {current_url}")
                    return None

            logger.debug(f"Download completed: {bytes_downloaded} bytes")
            return buffer

        except requests.exceptions.RequestException as e:
            status = _get_status_code(e)
            retryable = _is_retryable_error(e)

            # Z-Library 403 - try refreshing cookies via bypasser once before giving up
            if status == 403 and _is_cf_bypass_enabled() and not zlib_cookie_refresh_attempted:
                parsed = urlparse(current_url)
                if parsed.hostname and 'z-lib' in parsed.hostname and referer:
                    zlib_cookie_refresh_attempted = True
                    logger.info(f"Z-Library 403 - refreshing cookies via referer: {referer}")
                    try:
                        get_bypassed_page(referer, selector, cancel_flag)
                        time.sleep(0.5)
                        # Retry with fresh cookies (don't increment attempt)
                        continue
                    except Exception as cookie_err:
                        logger.warning(f"Z-Library cookie refresh failed: {cookie_err}")

            # Non-retryable errors
            if status in (403, 404):
                logger.warning(f"Download failed ({status}): {current_url}")
                return None

            # Rate limited - skip to next source immediately
            # (waiting doesn't help with concurrent downloads hitting the same server)
            if status == 429:
                logger.info(f"Rate limited (429) - trying next source")
                if status_callback:
                    status_callback("resolving", "Server busy, trying next")
                return None

            # Timeout - don't retry, server likely overloaded
            if isinstance(e, requests.exceptions.Timeout):
                logger.warning(f"Timeout: {current_url} - skipping to next source")
                if status_callback:
                    status_callback("resolving", "Server timed out, trying next")
                return None

            # Try to resume if we got some data
            if bytes_downloaded > 0 and retryable:
                resumed = _try_resume(current_url, buffer, bytes_downloaded, total_size, progress_callback, cancel_flag, headers)
                if resumed:
                    return resumed

            # Try mirror/DNS rotation if nothing downloaded yet
            if bytes_downloaded == 0 and retryable:
                new_url = _try_rotation(link, current_url, selector)
                if new_url:
                    current_url = new_url
                    attempt += 1
                    continue

            logger.warning(f"Download error: {type(e).__name__}: {e}")
            if attempt < MAX_DOWNLOAD_RETRIES - 1:
                time.sleep(_backoff_delay(attempt + 1))
            attempt += 1

    logger.error(f"Download failed after {MAX_DOWNLOAD_RETRIES} attempts: {link}")
    return None


def _try_resume(
    url: str,
    buffer: BytesIO,
    start_byte: int,
    total_size: float,
    progress_callback: Optional[Callable[[float], None]],
    cancel_flag: Optional[Event],
    base_headers: Optional[dict] = None,
) -> Optional[BytesIO]:
    """Try to resume an interrupted download."""
    for attempt in range(MAX_RESUME_ATTEMPTS):
        logger.info(f"Resuming from {start_byte} bytes (attempt {attempt + 1}/{MAX_RESUME_ATTEMPTS})")
        time.sleep(_backoff_delay(attempt + 1, base=0.5, cap=5.0))

        try:
            # Try with CF cookies/UA if available
            resume_headers = {**(base_headers or DOWNLOAD_HEADERS), 'Range': f'bytes={start_byte}-'}
            cookies = _apply_cf_bypass(url, resume_headers)
            response = requests.get(
                url, stream=True, proxies=get_proxies(url), timeout=REQUEST_TIMEOUT,
                headers=resume_headers, cookies=cookies, verify=get_ssl_verify(url)
            )
            
            # Check resume support
            if response.status_code == 200:  # Server doesn't support resume
                logger.info("Server doesn't support resume")
                return None
            if response.status_code == 416:  # Range not satisfiable
                logger.warning("Range not satisfiable")
                return None
            if response.status_code != 206:
                response.raise_for_status()
            
            pbar = tqdm(total=total_size, initial=start_byte, unit='B', unit_scale=True, desc='Resuming')
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    buffer.write(chunk)
                    start_byte += len(chunk)
                    pbar.update(len(chunk))
                    if progress_callback and total_size > 0:
                        progress_callback(start_byte * 100.0 / total_size)
                    if cancel_flag and cancel_flag.is_set():
                        pbar.close()
                        return None
            pbar.close()
            
            logger.info(f"Resume completed: {start_byte} bytes")
            return buffer
            
        except requests.exceptions.RequestException as e:
            logger.debug(f"Resume attempt {attempt + 1} failed: {e}")
    
    logger.warning(f"Resume failed after {MAX_RESUME_ATTEMPTS} attempts")
    return None


def get_absolute_url(base_url: str, url: str) -> str:
    """Convert a relative URL to absolute using the base URL."""
    url = url.strip()
    if not url or url == "#" or url.startswith("http"):
        return url if url.startswith("http") else ""
    parsed = urlparse(url)
    base = urlparse(base_url)
    if not parsed.netloc or not parsed.scheme:
        parsed = parsed._replace(netloc=base.netloc, scheme=base.scheme)
    return parsed.geturl()

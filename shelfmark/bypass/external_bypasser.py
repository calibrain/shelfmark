"""External Cloudflare bypasser using FlareSolverr."""

import random
import time
from threading import Event
from typing import TYPE_CHECKING, Optional

import requests

from shelfmark.bypass import BypassCancelledException
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify

if TYPE_CHECKING:
    from shelfmark.download import network

logger = setup_logger(__name__)

# Timeout constants (seconds)
CONNECT_TIMEOUT = 10
MAX_READ_TIMEOUT = 120
READ_TIMEOUT_BUFFER = 15

# Retry settings
MAX_RETRY = 5
BACKOFF_BASE = 1.0
BACKOFF_CAP = 10.0


def _fetch_via_bypasser(target_url: str) -> str | None:
    """Make a single request to the external bypasser service. Returns HTML or None."""
    raw_bypasser_url = config.get("EXT_BYPASSER_URL", "http://flaresolverr:8191")
    bypasser_path = config.get("EXT_BYPASSER_PATH", "/v1")
    bypasser_timeout = config.get("EXT_BYPASSER_TIMEOUT", 60000)

    bypasser_url = normalize_http_url(raw_bypasser_url)
    if not bypasser_url or not bypasser_path:
        logger.error(
            "External bypasser not configured. Check EXT_BYPASSER_URL and EXT_BYPASSER_PATH."
        )
        return None

    read_timeout = min((bypasser_timeout / 1000) + READ_TIMEOUT_BUFFER, MAX_READ_TIMEOUT)

    try:
        response = requests.post(
            f"{bypasser_url}{bypasser_path}",
            headers={"Content-Type": "application/json"},
            json={
                "cmd": "request.get",
                "url": target_url,
                "maxTimeout": bypasser_timeout,
            },
            timeout=(CONNECT_TIMEOUT, read_timeout),
            verify=get_ssl_verify(bypasser_url),
        )
        response.raise_for_status()
        result = response.json()

        status = result.get("status", "unknown")
        message = result.get("message", "")
        logger.debug("External bypasser response for '%s': %s - %s", target_url, status, message)

        if status != "ok":
            logger.warning(
                "External bypasser failed for '%s': %s - %s",
                target_url,
                status,
                message,
            )
            return None

        solution = result.get("solution")
        html = solution.get("response", "") if solution else ""

        if not html:
            logger.warning("External bypasser returned empty response for '%s'", target_url)
            return None

    except requests.exceptions.Timeout:
        logger.warning(
            "External bypasser timed out for '%s' (connect: %ss, read: %.0fs)",
            target_url,
            CONNECT_TIMEOUT,
            read_timeout,
        )
    except requests.exceptions.RequestException as e:
        logger.warning("External bypasser request failed for '%s': %s", target_url, e)
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("External bypasser returned malformed response for '%s': %s", target_url, e)
    else:
        return html

    return None


def _check_cancelled(cancel_flag: Event | None, context: str) -> None:
    """Check if operation was cancelled and raise exception if so."""
    if cancel_flag and cancel_flag.is_set():
        logger.info("External bypasser cancelled %s", context)
        msg = "Bypass cancelled"
        raise BypassCancelledException(msg)


def _sleep_with_cancellation(seconds: float, cancel_flag: Event | None) -> None:
    """Sleep for the specified duration, checking for cancellation each second."""
    for _ in range(int(seconds)):
        _check_cancelled(cancel_flag, "during backoff")
        time.sleep(1)
    remaining = seconds - int(seconds)
    if remaining > 0:
        time.sleep(remaining)


def get_bypassed_page(
    url: str,
    selector: Optional["network.AAMirrorSelector"] = None,
    cancel_flag: Event | None = None,
) -> str | None:
    """Fetch HTML via external bypasser with retries and mirror rotation."""
    from shelfmark.download import network as network_module

    sel = selector or network_module.AAMirrorSelector()

    for attempt in range(1, MAX_RETRY + 1):
        _check_cancelled(cancel_flag, "by user")

        attempt_url = sel.rewrite(url)
        result = _fetch_via_bypasser(attempt_url)
        if result:
            return result

        if attempt == MAX_RETRY:
            break

        delay = min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))) + random.random()
        logger.info(
            "External bypasser attempt %s/%s failed, retrying in %.1fs",
            attempt,
            MAX_RETRY,
            delay,
        )

        _sleep_with_cancellation(delay, cancel_flag)

        new_base, action = sel.next_mirror_or_rotate_dns()
        if action in ("mirror", "dns") and new_base:
            logger.info("Rotated %s for retry", action)

    return None

"""Web scraping functions for AudiobookBay."""

import re
import time
from typing import List, Optional, Dict
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download import http as downloader

logger = setup_logger(__name__)

# Default trackers if none found on page
DEFAULT_TRACKERS = [
    "udp://tracker.openbittorrent.com:80",
    "udp://opentor.org:2710",
    "udp://tracker.ccc.de:80",
    "udp://tracker.blackunicorn.xyz:6969",
    "udp://tracker.coppersurfer.tk:6969",
    "udp://tracker.leechers-paradise.org:6969",
]

# ABB request behavior tuning
SEARCH_PAGE_RETRY_ATTEMPTS = 2
DETAIL_PAGE_RETRY_ATTEMPTS = 2
FIRST_PAGE_SESSION_REFRESH_ATTEMPTS = 2

# Legacy search parameter used by older ABB flows
LEGACY_CATEGORY_QUERY = "undefined%2Cundefined"

# Precompiled patterns used while parsing result cards
LANGUAGE_PATTERN = re.compile(r"Language:\s*([A-Za-z]+)")
POSTED_PATTERN = re.compile(r"Posted:\s*(\d+\s+[A-Za-z]+\s+\d{4})")
FORMAT_PATTERN = re.compile(r"Format:\s*([A-Za-z0-9]+)")
BITRATE_PATTERN = re.compile(r"Bitrate:\s*([\d]+\s*[A-Za-z/]+)")
SIZE_PATTERN = re.compile(r"File Size:\s*([\d.]+)\s*([A-Za-z]+)")
INFO_HASH_LABEL_PATTERN = re.compile(r"Info Hash", re.IGNORECASE)


def _build_search_url(
    hostname: str,
    page: int,
    query_encoded: str,
    *,
    include_legacy_category: bool = False,
) -> str:
    """Build an ABB search URL, optionally including legacy category params."""
    # Page 1 uses ABB's root search endpoint; pagination continues via /page/{n}/.
    if page <= 1:
        url = f"https://{hostname}/?s={query_encoded}"
    else:
        url = f"https://{hostname}/page/{page}/?s={query_encoded}"
    if include_legacy_category:
        return f"{url}&cat={LEGACY_CATEGORY_QUERY}"
    return url


def _is_homepage_redirect(final_url: str, hostname: str) -> bool:
    """Detect whether ABB redirected a search request to its homepage."""
    normalized_final = (final_url or "").rstrip("/")
    normalized_home = f"https://{hostname}".rstrip("/")
    return normalized_final in {normalized_home, f"{normalized_home}/"}


def _encode_search_query(query: str, exact_phrase: bool) -> str:
    """Encode search query using ABB's space-plus style and optional exact phrase wrapping."""
    search_query = query.strip()
    if exact_phrase and search_query and not (search_query.startswith('"') and search_query.endswith('"')):
        search_query = f"\"{search_query}\""
    # Keep ABB-friendly encoding style (spaces as '+') while percent-encoding quotes.
    return search_query.replace('"', "%22").replace(" ", "+")


def _normalize_result_url(url: str, hostname: str) -> str:
    """Normalize ABB result URLs to absolute HTTPS URLs."""
    normalized_url = (url or "").strip()
    if not normalized_url:
        return ""
    if normalized_url.startswith(("http://", "https://")):
        return normalized_url
    if normalized_url.startswith("//"):
        return f"https:{normalized_url}"
    if normalized_url.startswith("/"):
        return f"https://{hostname}{normalized_url}"
    return f"https://{hostname}/{normalized_url.lstrip('/')}"


def _bootstrap_abb_session(
    hostname: str,
    session: requests.Session,
    retry_attempts: int,
) -> None:
    """Warm up ABB session cookies (best effort)."""
    downloader.html_get_page(
        f"https://{hostname}/",
        retry=retry_attempts,
        use_bypasser=False,
        allow_bypasser_fallback=False,
        include_response_url=True,
        success_delay=0,
        session=session,
    )


def search_audiobookbay(
    query: str,
    max_pages: int = 1,
    hostname: str = "audiobookbay.lu",
    exact_phrase: bool = False,
) -> List[Dict[str, str]]:
    """Search AudiobookBay for audiobooks matching the query.
    
    Args:
        query: Search query string
        max_pages: Maximum number of pages to fetch
        hostname: AudiobookBay hostname (e.g., "audiobookbay.lu")
        exact_phrase: Wrap query in quotes for exact phrase matching
        
    Returns:
        List of dicts with keys: title, link, cover, language, format, bitrate, size, posted_date
    """
    results = []
    rate_limit_delay = config.get("ABB_RATE_LIMIT_DELAY", 1.0)
    session = requests.Session()

    # Bootstrap ABB session cookie (PHPSESSID). ABB increasingly serves reliable
    # search/detail pages only after session initialization, similar to browsers.
    _bootstrap_abb_session(hostname, session, SEARCH_PAGE_RETRY_ATTEMPTS)
    
    # Iterate through pages
    for page in range(1, max_pages + 1):
        # Construct URL - use + for spaces (matching audiobookbay-automated implementation)
        # This avoids aggressive encoding that PHP-based sites may reject.
        query_encoded = _encode_search_query(query, exact_phrase)
        # ABB search expects the legacy category query parameter.
        primary_url = _build_search_url(
            hostname,
            page,
            query_encoded,
            include_legacy_category=True,
        )
        
        try:
            # Reuse shared HTTP fetch logic (without bypasser)
            page_html, final_url = downloader.html_get_page(
                primary_url,
                retry=SEARCH_PAGE_RETRY_ATTEMPTS,
                use_bypasser=False,
                allow_bypasser_fallback=False,
                include_response_url=True,
                success_delay=0,
                session=session,
            )

            was_home_redirect = _is_homepage_redirect(final_url, hostname)

            # ABB can intermittently fail even with a valid URL.
            # If page 1 fails, refresh the session and retry the exact same URL.
            if page == 1 and (not page_html or was_home_redirect):
                for refresh_attempt in range(1, FIRST_PAGE_SESSION_REFRESH_ATTEMPTS + 1):
                    session = requests.Session()
                    _bootstrap_abb_session(hostname, session, SEARCH_PAGE_RETRY_ATTEMPTS)
                    page_html, final_url = downloader.html_get_page(
                        primary_url,
                        retry=SEARCH_PAGE_RETRY_ATTEMPTS,
                        use_bypasser=False,
                        allow_bypasser_fallback=False,
                        include_response_url=True,
                        success_delay=0,
                        session=session,
                    )
                    was_home_redirect = _is_homepage_redirect(final_url, hostname)
                    if page_html and not was_home_redirect:
                        break
                    logger.debug(
                        "ABB page 1 session refresh %d/%d failed",
                        refresh_attempt,
                        FIRST_PAGE_SESSION_REFRESH_ATTEMPTS,
                    )

            if not page_html:
                logger.warning(f"Failed to fetch page {page}")
                break
            
            # Check if we were redirected to the homepage (search was rejected/blocked)
            if was_home_redirect:
                # Search was redirected to homepage - this means the search failed
                # This can happen due to geo-blocking, rate limiting, or invalid query format
                if page == 1:
                    logger.warning(f"Search query '{query}' was redirected to homepage - search may be blocked or invalid")
                break
            
            # Parse HTML
            soup = BeautifulSoup(page_html, 'html.parser')
            
            # Extract book entries
            posts = soup.select('.post')
            if not posts:
                # No more results
                break
            
            for post in posts:
                try:
                    # Extract title
                    title_elem = post.select_one('.postTitle > h2 > a')
                    if not title_elem:
                        continue
                    
                    title = title_elem.text.strip()
                    
                    # Extract link (relative, needs hostname prefix)
                    href = title_elem.get('href', '')
                    if not href:
                        continue
                    
                    link = _normalize_result_url(href, hostname)
                    if not link:
                        continue
                    
                    # Extract cover image (try .postContent .center img first, then fallback to any img)
                    cover = None
                    cover_elem = post.select_one('.postContent .center img') or post.select_one('img')
                    if cover_elem:
                        cover = _normalize_result_url(cover_elem.get('src', ''), hostname) or None
                    
                    # Extract language from .postInfo
                    language = None
                    post_info = post.select_one('.postInfo')
                    if post_info:
                        info_text = post_info.get_text(separator=' ', strip=True).replace('\xa0', ' ')
                        lang_match = LANGUAGE_PATTERN.search(info_text)
                        if lang_match:
                            language = lang_match.group(1).strip()
                    
                    # Extract format, bitrate, size, and posted date from .postContent
                    posted_date = None
                    format_type = None
                    bitrate = None
                    size_str = None
                    
                    post_content = post.select_one('.postContent')
                    if post_content:
                        content_text = post_content.get_text(separator=' ', strip=True).replace('\xa0', ' ')
                        
                        # Extract posted date
                        posted_match = POSTED_PATTERN.search(content_text)
                        if posted_match:
                            posted_date = posted_match.group(1).strip()
                        
                        # Extract format (e.g., "M4B", "MP3")
                        format_match = FORMAT_PATTERN.search(content_text)
                        if format_match:
                            format_type = format_match.group(1).strip()
                        
                        # Extract bitrate (e.g., "256 Kbps")
                        bitrate_match = BITRATE_PATTERN.search(content_text)
                        if bitrate_match:
                            bitrate = bitrate_match.group(1).strip()
                        
                        # Extract file size (e.g., "11.68 GBs" -> normalized to "11.68 GB")
                        size_match = SIZE_PATTERN.search(content_text)
                        if size_match:
                            size_value = size_match.group(1)
                            size_unit = size_match.group(2).strip()
                            if size_unit.lower().endswith("s"):
                                size_unit = size_unit[:-1]
                            size_unit = size_unit.upper()
                            size_str = f"{size_value} {size_unit}"
                    
                    results.append({
                        'title': title,
                        'link': link,
                        'cover': cover or None,
                        'language': language,
                        'format': format_type,
                        'bitrate': bitrate,
                        'size': size_str,
                        'posted_date': posted_date,
                    })
                except Exception as e:
                    logger.debug(f"Skipping post due to error: {e}")
                    continue
            
            # Rate limiting delay between pages
            if page < max_pages and rate_limit_delay > 0:
                time.sleep(rate_limit_delay)
        except Exception as e:
            logger.error(f"Unexpected error on page {page}: {e}")
            break
    
    logger.info(f"Found {len(results)} results for query '{query}'")
    return results


def extract_magnet_link(
    details_url: str,
    hostname: str = "audiobookbay.lu"
) -> Optional[str]:
    """Extract info hash and trackers from book detail page, then construct magnet link.
    
    Args:
        details_url: URL of the book's detail page
        hostname: AudiobookBay hostname (for logging)
        
    Returns:
        Magnet link, or None if extraction fails
    """
    try:
        session = requests.Session()
        _bootstrap_abb_session(hostname, session, DETAIL_PAGE_RETRY_ATTEMPTS)

        # Fetch detail page
        detail_html = downloader.html_get_page(
            details_url,
            retry=DETAIL_PAGE_RETRY_ATTEMPTS,
            use_bypasser=False,
            allow_bypasser_fallback=False,
            success_delay=0,
            session=session,
        )

        if not detail_html:
            session = requests.Session()
            _bootstrap_abb_session(hostname, session, DETAIL_PAGE_RETRY_ATTEMPTS)
            detail_html = downloader.html_get_page(
                details_url,
                retry=DETAIL_PAGE_RETRY_ATTEMPTS,
                use_bypasser=False,
                allow_bypasser_fallback=False,
                success_delay=0,
                session=session,
            )
        
        if not detail_html:
            logger.warning("Failed to fetch details page")
            return None
        
        soup = BeautifulSoup(detail_html, 'html.parser')
        
        # 1. Extract Info Hash
        # Look for <td>Info Hash</td> and get next sibling value
        info_hash = None
        info_hash_rows = soup.find_all('td')
        for td in info_hash_rows:
            if td.text.strip().lower() == 'info hash':
                next_td = td.find_next_sibling('td')
                if next_td:
                    info_hash = next_td.text.strip()
                    break
        
        # Alternative: search for text containing "Info Hash" and get next element
        if not info_hash:
            for elem in soup.find_all(string=INFO_HASH_LABEL_PATTERN):
                parent = elem.parent
                if parent and parent.name == 'td':
                    next_td = parent.find_next_sibling('td')
                    if next_td:
                        info_hash = next_td.text.strip()
                        break
        
        if not info_hash:
            logger.warning("Info Hash not found on the page.")
            return None
        
        # Clean up info hash (remove whitespace, ensure uppercase)
        info_hash = re.sub(r'\s+', '', info_hash).upper()
        
        # 2. Extract Trackers
        # Find all <td> containing udp:// or http://
        trackers = []
        for td in soup.find_all('td'):
            text = td.text.strip()
            if text.startswith(('udp://', 'http://', 'https://')):
                trackers.append(text)
        
        # 3. Use default trackers if none found
        if not trackers:
            logger.debug("No trackers found on the page. Using default trackers.")
            trackers = DEFAULT_TRACKERS
        
        # 4. Construct Magnet Link
        # Format: magnet:?xt=urn:btih:{INFO_HASH}&tr={TRACKER1}&tr={TRACKER2}...
        tracker_params = "&".join(
            f"tr={quote(tracker)}"
            for tracker in trackers
        )
        magnet_link = f"magnet:?xt=urn:btih:{info_hash}&{tracker_params}"
        
        logger.debug(f"Generated Magnet Link: {magnet_link[:100]}...")
        return magnet_link
        
    except Exception as e:
        logger.error(f"Failed to extract magnet link: {e}")
        return None

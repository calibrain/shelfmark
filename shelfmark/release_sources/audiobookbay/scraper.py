"""Web scraping functions for AudiobookBay."""

import re
import time
from typing import List, Optional, Dict
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download import network

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

# Required headers to avoid blocking
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
}


def search_audiobookbay(
    query: str,
    max_pages: int = 5,
    hostname: str = "audiobookbay.lu"
) -> List[Dict[str, str]]:
    """Search AudiobookBay for audiobooks matching the query.
    
    Args:
        query: Search query string
        max_pages: Maximum number of pages to fetch
        hostname: AudiobookBay hostname (e.g., "audiobookbay.lu")
        
    Returns:
        List of dicts with keys: title, link, cover, language, format, bitrate, size, posted_date
    """
    results = []
    rate_limit_delay = config.get("ABB_RATE_LIMIT_DELAY", 1.0)
    
    # Iterate through pages
    for page in range(1, max_pages + 1):
        # Construct URL - use + for spaces (matching audiobookbay-automated implementation)
        # This avoids aggressive encoding that PHP-based sites may reject
        query_encoded = query.replace(' ', '+')
        url = f"https://{hostname}/page/{page}/?s={query_encoded}&cat=undefined%2Cundefined"
        
        try:
            # Make request with proxy support
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                proxies=network.get_proxies(url),
                timeout=30,
                allow_redirects=True
            )
            
            if response.status_code != 200:
                logger.warning(f"Failed to fetch page {page}. Status Code: {response.status_code}")
                break
            
            # Check if we were redirected to the homepage (search was rejected/blocked)
            final_url = response.url.rstrip('/')
            base_url = f"https://{hostname}".rstrip('/')
            if final_url == base_url or final_url == f"{base_url}/":
                # Search was redirected to homepage - this means the search failed
                # This can happen due to geo-blocking, rate limiting, or invalid query format
                if page == 1:
                    logger.warning(f"Search query '{query}' was redirected to homepage - search may be blocked or invalid")
                break
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
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
                    
                    if href.startswith('http'):
                        link = href
                    else:
                        link = f"https://{hostname}{href}"
                    
                    # Extract cover image (try .postContent .center img first, then fallback to any img)
                    cover = None
                    cover_elem = post.select_one('.postContent .center img') or post.select_one('img')
                    if cover_elem:
                        cover = cover_elem.get('src', '')
                        if cover and not cover.startswith('http'):
                            cover = f"https://{hostname}{cover}"
                    
                    # Extract language from .postInfo
                    language = None
                    post_info = post.select_one('.postInfo')
                    if post_info:
                        info_text = post_info.get_text(separator=' ', strip=True).replace('\xa0', ' ')
                        lang_match = re.search(r'Language:\s*([A-Za-z]+)', info_text)
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
                        posted_match = re.search(r'Posted:\s*(\d+\s+[A-Za-z]+\s+\d{4})', content_text)
                        if posted_match:
                            posted_date = posted_match.group(1).strip()
                        
                        # Extract format (e.g., "M4B", "MP3")
                        format_match = re.search(r'Format:\s*([A-Za-z0-9]+)', content_text)
                        if format_match:
                            format_type = format_match.group(1).strip()
                        
                        # Extract bitrate (e.g., "256 Kbps")
                        bitrate_match = re.search(r'Bitrate:\s*([\d]+\s*[A-Za-z/]+)', content_text)
                        if bitrate_match:
                            bitrate = bitrate_match.group(1).strip()
                        
                        # Extract file size (e.g., "11.68 GBs")
                        size_match = re.search(r'File Size:\s*([\d.]+)\s*([A-Za-z]+)', content_text)
                        if size_match:
                            size_str = f"{size_match.group(1)} {size_match.group(2)}"
                    
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
                
        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error on page {page}: {e}")
            break
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
        # Fetch detail page
        response = requests.get(
            details_url,
            headers=REQUEST_HEADERS,
            proxies=network.get_proxies(details_url),
            timeout=30
        )
        
        if response.status_code != 200:
            logger.warning(f"Failed to fetch details page. Status Code: {response.status_code}")
            return None
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
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
            for elem in soup.find_all(string=re.compile(r'Info Hash', re.IGNORECASE)):
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
        
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error extracting magnet link: {e}")
        return None
    except Exception as e:
        logger.error(f"Failed to extract magnet link: {e}")
        return None

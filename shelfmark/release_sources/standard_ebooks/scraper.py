"""Standard Ebooks catalogue scraping: search results and download-link resolution.

This module provides fetch and parse functions for searching the public Standard Ebooks
catalogue and constructing or resolving direct EPUB download links.
"""

from http import HTTPStatus
import re
from typing import TYPE_CHECKING
from urllib.parse import quote_plus, urljoin, urlsplit

from bs4 import BeautifulSoup
import requests

from shelfmark.core.logger import setup_logger
from shelfmark.download import http as downloader
from shelfmark.download import network
from shelfmark.release_sources import BrowseRecord

if TYPE_CHECKING:
    pass

logger = setup_logger(__name__)

BASE_URL = "https://standardebooks.org"


def fetch_page(url: str, timeout: tuple[int, int] = (5, 15)) -> str | None:
    """GET a Standard Ebooks page, returning text on HTTP 200 or None on failure."""
    headers = {**downloader.DOWNLOAD_HEADERS, "Referer": BASE_URL}
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
            proxies=network.get_proxies(url),
            verify=network.get_ssl_verify(url),
        )
    except requests.exceptions.RequestException as exc:
        logger.debug("Standard Ebooks fetch failed for %s: %s", url, exc)
        return None
    if response.status_code != HTTPStatus.OK:
        logger.debug("Standard Ebooks fetch %s returned %s", url, response.status_code)
        return None
    return response.text


def build_epub_url(book_url_or_slug: str, base_url: str = BASE_URL) -> str:
    """Construct the direct compatible EPUB download URL from a book slug or URL.

    Standard Ebooks generates deterministic distribution filenames by taking all path
    segments after ``/ebooks/`` and joining them with underscores:
    - 2-segment slug ``mary-shelley/frankenstein`` -> ``mary-shelley_frankenstein.epub``
    - 3-segment slug ``maurice-leblanc/the-hollow-needle/alexander-teixeira-de-mattos``
      -> ``maurice-leblanc_the-hollow-needle_alexander-teixeira-de-mattos.epub``
    """
    clean = book_url_or_slug.strip()
    if clean.startswith("http://") or clean.startswith("https://"):
        parsed = urlsplit(clean)
        path = parsed.path
        origin = f"{parsed.scheme}://{parsed.netloc}"
    else:
        path = clean if clean.startswith("/ebooks/") else f"/ebooks/{clean.lstrip('/')}"
        origin = base_url.rstrip("/")

    # Strip /downloads/ or /downloads if present
    if "/downloads" in path:
        path = path.split("/downloads")[0]

    segments = [s for s in path.strip("/").split("/") if s and s != "ebooks"]
    if not segments:
        return f"{origin}/ebooks"

    filename = f"{'_'.join(segments)}.epub"
    relative_path = f"/ebooks/{'/'.join(segments)}/downloads/{filename}"
    return f"{origin}{relative_path}"


def search_standard_ebooks(
    query: str,
    *,
    max_results: int = 12,
    timeout: tuple[int, int] = (5, 15),
    base_url: str = BASE_URL,
) -> list[BrowseRecord]:
    """Search Standard Ebooks for titles matching ``query``."""
    trimmed = query.strip()
    if not trimmed:
        return []

    url = f"{base_url.rstrip('/')}/ebooks?query={quote_plus(trimmed)}&per-page={max_results}"
    html = fetch_page(url, timeout)
    if html is None:
        return []
    return _parse_results(html, base_url, max_results=max_results)


def _cell_text(text: str) -> str:
    """Collapse runs of whitespace to a single space."""
    return re.sub(r"\s+", " ", text).strip()


def _parse_results(
    html: str,
    base_url: str = BASE_URL,
    max_results: int | None = None,
) -> list[BrowseRecord]:
    """Parse a Standard Ebooks search/browse HTML page into a list of BrowseRecords."""
    soup = BeautifulSoup(html, "html.parser")
    # Container can be ol.ebooks-list or ol.grid
    container = soup.select_one("ol.ebooks-list, ol.grid")
    if not container:
        # Fallback to any ol with list items having links to /ebooks/
        for ol in soup.find_all("ol"):
            if ol.select_one('a[href*="/ebooks/"]'):
                container = ol
                break
    if not container:
        return []

    records: list[BrowseRecord] = []
    items = container.find_all("li")
    for li in items:
        if max_results is not None and len(records) >= max_results:
            break

        # 1. Book URL & slug
        link = li.select_one('[property="schema:url"], a[href^="/ebooks/"], a[href*="/ebooks/"]')
        if not link or not link.get("href"):
            continue
        href = str(link.get("href")).strip()
        parsed_href = urlsplit(href).path.rstrip("/")
        if not parsed_href or parsed_href == "/ebooks":
            continue

        segments = [s for s in parsed_href.strip("/").split("/") if s and s != "ebooks"]
        if not segments:
            continue
        slug = "/".join(segments)
        book_url = urljoin(base_url, f"/ebooks/{slug}")

        # 2. Title
        title_elem = li.select_one('[property="schema:name"], p.title a, p.title, h3, h2')
        if title_elem:
            title = _cell_text(title_elem.get_text())
        else:
            title = link.get_text(strip=True) or slug.replace("-", " ").title()

        if not title:
            continue

        # 3. Author
        author_elem = li.select_one('[property="schema:author"], p.author a, p.author')
        author = _cell_text(author_elem.get_text()) if author_elem else None

        # 4. Cover thumbnail
        img_elem = li.select_one('img[property="schema:image"], img[src]')
        cover_url = None
        if img_elem and img_elem.get("src"):
            cover_url = urljoin(base_url, str(img_elem["src"]))

        # 5. Direct download URL
        epub_url = build_epub_url(book_url, base_url)

        records.append(
            BrowseRecord(
                id=slug,
                title=title,
                source="standard_ebooks",
                author=author,
                format="epub",
                language="en",
                preview=cover_url,
                source_url=book_url,
                download_urls=[epub_url],
            )
        )

    return records


def resolve_download_url(book_page_html: str, book_url: str) -> str | None:
    """Extract a compatible .epub download link from a book detail page."""
    soup = BeautifulSoup(book_page_html, "html.parser")

    # Find all anchor tags pointing to .epub files
    epub_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"]).strip()
        if href.lower().endswith(".epub"):
            epub_links.append(urljoin(book_url, href))

    if not epub_links:
        return None

    # Prefer the compatible epub (does not contain _advanced.epub or .kepub.epub)
    for link in epub_links:
        lower = link.lower()
        if not lower.endswith("_advanced.epub") and not lower.endswith(".kepub.epub"):
            return link

    # Fallback to the first epub link found
    return epub_links[0]

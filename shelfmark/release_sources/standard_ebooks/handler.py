"""Standard Ebooks download handler - downloads EPUBs from standardebooks.org."""

import hashlib
import io
import zipfile
from typing import TYPE_CHECKING

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import build_filename, normalize_format, resolve_staged_path
from shelfmark.download import http as downloader
from shelfmark.release_sources import DownloadHandler, register_handler
from shelfmark.release_sources.standard_ebooks import scraper

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask

logger = setup_logger(__name__)

# Files under 1 KB are almost certainly empty error stubs rather than valid EPUB archives.
_MIN_VALID_FILE_SIZE = 1024


def _is_valid_epub(data: io.BytesIO) -> bool:
    """Return True only if data is a real EPUB archive.

    A valid EPUB must be a ZIP file containing a ``mimetype`` entry equal to
    ``application/epub+zip`` and ``META-INF/container.xml``. Never raises.
    """
    try:
        position = data.tell()
        data.seek(0)
        if not zipfile.is_zipfile(data):
            return False
        data.seek(0)
        with zipfile.ZipFile(data) as archive:
            try:
                mimetype = archive.read("mimetype")
            except KeyError:
                return False
            if mimetype.strip() != b"application/epub+zip":
                return False
            try:
                archive.getinfo("META-INF/container.xml")
            except KeyError:
                return False
        return True
    except Exception:
        return False
    finally:
        try:
            data.seek(position)
        except Exception:
            pass


@register_handler("standard_ebooks")
class StandardEbooksHandler(DownloadHandler):
    """Download handler for Standard Ebooks releases."""

    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Download an EPUB release from Standard Ebooks and stage it in TMP_DIR."""
        try:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            slug = task.task_id.split(":", 1)[-1].strip("/")

            file_org = config.get("FILE_ORGANIZATION", "rename")
            if file_org == "none":
                book_name = f"{slug.replace('/', '_')}.{normalize_format(task.format) or 'epub'}"
            else:
                book_name = build_filename(task.title, task.author, task.year, task.format or "epub")

            suffix = hashlib.sha1(task.task_id.encode("utf-8")).hexdigest()[:12]
            if "." in book_name:
                stem, ext = book_name.rsplit(".", 1)
                book_name = f"{stem}-{suffix}.{ext}"
            else:
                book_name = f"{book_name}-{suffix}"

            book_path = resolve_staged_path(TMP_DIR, book_name)
            if book_path is None:
                logger.error("Could not resolve staged path for %s in TMP_DIR", book_name)
                status_callback("error", "Invalid download staging destination")
                return None

            # Primary download URL: constructed deterministic EPUB URL
            if task.retry_download_url:
                primary_url = task.retry_download_url
            elif task.source_url:
                primary_url = scraper.build_epub_url(task.source_url)
            else:
                primary_url = scraper.build_epub_url(slug)

            status_callback("resolving", "Connecting to Standard Ebooks")
            data = downloader.download_url(
                primary_url,
                task.size or "",
                progress_callback,
                cancel_flag,
                None,
                status_callback,
                referer=task.source_url,
            )

            if data and (data.tell() < _MIN_VALID_FILE_SIZE or not _is_valid_epub(data)):
                logger.warning(
                    "Standard Ebooks download from primary URL was invalid, trying fallback"
                )
                data = None

            # Fallback: if primary download failed or was invalid, inspect the book landing page
            if not data and not cancel_flag.is_set():
                landing_url = task.source_url or f"{scraper.BASE_URL}/ebooks/{slug}"
                status_callback("resolving", "Checking book page for download link")
                page_html = scraper.fetch_page(landing_url)
                if page_html:
                    fallback_url = scraper.resolve_download_url(page_html, landing_url)
                    if fallback_url:
                        logger.info("Retrying with download URL from book page: %s", fallback_url)
                        status_callback("downloading", None)
                        data = downloader.download_url(
                            fallback_url,
                            task.size or "",
                            progress_callback,
                            cancel_flag,
                            None,
                            status_callback,
                            referer=landing_url,
                        )
                        if data and (data.tell() < _MIN_VALID_FILE_SIZE or not _is_valid_epub(data)):
                            logger.warning(
                                "Standard Ebooks download from fallback URL was invalid"
                            )
                            data = None

            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            if not data:
                status_callback("error", "Failed to download from Standard Ebooks")
                return None

            data.seek(0)
            with book_path.open("wb") as file:
                file.write(data.getbuffer())
            return str(book_path)

        except Exception as exc:
            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
            else:
                logger.exception("Error downloading from Standard Ebooks")
                status_callback("error", str(exc))
            return None

    def cancel(self, task_id: str) -> bool:
        """Cancellation is handled by the orchestrator via cancel_flag."""
        return False

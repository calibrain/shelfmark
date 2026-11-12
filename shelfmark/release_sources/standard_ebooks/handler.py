"""Standard Ebooks download handler - downloads EPUBs from standardebooks.org."""

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

            # Fallback: if constructed URL failed, inspect the book landing page
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

            if cancel_flag.is_set():
                status_callback("cancelled", "Cancelled")
                return None

            if not data:
                status_callback("error", "Failed to download from Standard Ebooks")
                return None

            if data.tell() < _MIN_VALID_FILE_SIZE:
                logger.warning("Standard Ebooks file too small (%s bytes), treating as failure", data.tell())
                status_callback("error", "Standard Ebooks download was incomplete or invalid")
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

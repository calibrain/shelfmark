"""
Example custom source plugin for Shelfmark.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  HOW TO USE THIS FILE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. Copy this file to:   $CONFIG_DIR/custom_sources/my_source.py
2. Edit it to point at your actual source.
3. Restart Shelfmark — your source appears in Settings > Custom Sources.

$CONFIG_DIR is wherever you mounted the Shelfmark config volume, e.g.
  ./data/config/  (Docker Compose default)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  DEPENDENCIES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If your plugin needs a third-party library (e.g. beautifulsoup4, lxml),
create a requirements.txt in the same folder:

  $CONFIG_DIR/custom_sources/requirements.txt

Shelfmark installs those packages automatically at startup.
The `requests` library is always available without adding it.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠ SECURITY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Plugin files run as full Python code with the same access as Shelfmark
itself. Only install plugins from sources you trust — treat them the
same as any program you run on your machine.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WHAT A PLUGIN MUST PROVIDE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  @register_source("your_name")   → ReleaseSource subclass  (search)
  @register_handler("your_name")  → DownloadHandler subclass (download)

The name must be unique and must not clash with built-ins:
  direct_download, prowlarr, newznab, irc, audiobookbay

Everything else in this file is optional — remove what you don't need.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  DOWNLOAD PROTOCOLS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Three download styles are supported:

  HTTP    — your handler downloads directly to a temp file (default)
  TORRENT — your handler hands a magnet/torrent URL to qBittorrent etc.
  NZB     — your handler hands an NZB URL to SABnzbd/NZBGet etc.

For torrent and NZB, use ExternalClientHandler (see the bottom of
this file) instead of writing download logic yourself.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  AUDIOBOOK ROUTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Set content_type="audiobook" on your Release objects and Shelfmark
automatically routes downloads to the audiobook destination folder.
Set content_type="ebook" (or leave blank) for regular books.

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import requests

from shelfmark.release_sources import (
    DownloadHandler,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    SourceUnavailableError,
    register_handler,
    register_source,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from threading import Event

    from shelfmark.core.models import DownloadTask
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

# ─────────────────────────────────────────────────────────────────────────────
# Identity — pick a short stable name and never change it.
# It is stored in the database against every download from this source.
# ─────────────────────────────────────────────────────────────────────────────
SOURCE_NAME = "my_private_tracker"


# ─────────────────────────────────────────────────────────────────────────────
# 1.  ReleaseSource  —  responsible for SEARCHING
# ─────────────────────────────────────────────────────────────────────────────


@register_source(SOURCE_NAME)
class MyTrackerSource(ReleaseSource):
    name = SOURCE_NAME
    display_name = "My Private Tracker"

    # Which content types this source supports.
    # Remove "audiobook" or "ebook" if your source only carries one type.
    supported_content_types: list[str] = ["ebook", "audiobook"]  # noqa: RUF012

    # Set False to hide this source from the "Default Release Source" dropdown.
    # Leave True for most sources.
    can_be_default: bool = True

    def is_available(self) -> bool:
        """Return True only when the source is properly configured and usable."""
        from shelfmark.core.config import config

        return bool(config.get("MY_TRACKER_URL", "") and config.get("MY_TRACKER_API_KEY", ""))

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        if not self.is_available():
            raise SourceUnavailableError(f"{self.display_name} is not configured")

        from shelfmark.core.config import config

        base_url = config.get("MY_TRACKER_URL", "") or ""
        api_key = config.get("MY_TRACKER_API_KEY", "") or ""

        query = book.search_title or book.title
        if book.search_author:
            query = f"{query} {book.search_author}"

        try:
            resp = requests.get(
                f"{base_url}/api/search",
                params={"q": query, "key": api_key, "type": content_type},
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except requests.RequestException as exc:
            raise SourceUnavailableError(f"{self.display_name} search failed: {exc}") from exc

        releases: list[Release] = []
        for item in results:
            releases.append(  # noqa: PERF401
                Release(
                    source=SOURCE_NAME,
                    source_id=str(item["id"]),
                    title=item.get("title", ""),
                    format=item.get("format"),  # "epub", "pdf", "mp3", …
                    language=item.get("language"),  # ISO 639-1 code: "en", "de", …
                    size=item.get("size_human"),  # human-readable: "12 MB"
                    size_bytes=item.get("size_bytes"),
                    download_url=item.get("download_url"),
                    info_url=item.get("page_url"),  # makes the title a clickable link
                    protocol=ReleaseProtocol.HTTP,  # or TORRENT / NZB — see bottom
                    indexer=self.display_name,
                    content_type=content_type,  # "ebook" or "audiobook" — drives destination routing
                    extra={},
                )
            )
        return releases

    # ── Optional: customise the columns shown in the release list ──────────
    # Delete this method to use the default layout (language / format / size).

    def get_column_config(self) -> ReleaseColumnConfig:
        from shelfmark.release_sources import (
            ColumnAlign,
            ColumnColorHint,
            ColumnRenderType,
            ColumnSchema,
        )

        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="language",
                    label="Language",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="60px",
                    color_hint=ColumnColorHint(type="map", value="language"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0,2fr) 60px 80px 80px",
            supported_filters=["format", "language"],
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2.  DownloadHandler  —  responsible for DOWNLOADING (HTTP version)
#
#     Use this style when you fetch the file directly.
#     For torrent / NZB hand-off, see the ExternalClientHandler section below.
# ─────────────────────────────────────────────────────────────────────────────


@register_handler(SOURCE_NAME)
class MyTrackerHandler(DownloadHandler):
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """
        Download the file and return its local path, or None if cancelled.

        ── YOU must drive the status label ──────────────────────────
        Shelfmark starts every download as "pending" and waits for you.
        If you never call status_callback, the queue shows "pending"
        the whole time — even while the file is actually downloading.

        Call it at least twice during a typical download:
          status_callback("resolving", "Connecting…")  # before any network call
          status_callback("downloading", None)          # once bytes start flowing

        Valid values: "resolving", "downloading"
        Do NOT call "complete", "failed", or "cancelled" — those are set
        automatically by Shelfmark when your method returns or raises.

        ── YOU must drive the progress bar ──────────────────────────
        progress_callback(0–100) moves the bar. Call it inside your
        download loop — the bar stays at 0 if you never call it.
        If the server doesn't send a content-length, estimate:
          progress_callback(min(downloaded / 500_000 * 80, 90))
          progress_callback(100)  # always finish at 100

        ── task fields ───────────────────────────────────────────────
          task.task_id    — source_id from the Release (e.g. a book ID or GUID)
          task.source_url — download_url from the Release
          task.title      — display title
          task.author     — author name (may be None)
          task.format     — file format (may be None)

        ── cancel_flag ───────────────────────────────────────────────
        Check cancel_flag.is_set() inside every loop. Delete partial
        files and return None. None = cancelled cleanly (not failed).

        ── error messages ────────────────────────────────────────────
        Any exception message is shown in the failed-download UI, so
        make it descriptive:
          raise RuntimeError("No file found for this book")
          raise RuntimeError(f"Tracker returned 403 — check your API key")

        ── return value ──────────────────────────────────────────────
        Return the local path to the downloaded file (string or Path).
        Shelfmark handles everything after: moving to the library,
        extraction, notifications, and history recording.
        """
        from shelfmark.config.env import TMP_DIR

        if not task.source_url:
            msg = "No download URL on task"
            raise RuntimeError(msg)

        status_callback("resolving", "Contacting tracker…")

        from shelfmark.core.config import config

        base_url = config.get("MY_TRACKER_URL", "") or ""
        api_key = config.get("MY_TRACKER_API_KEY", "") or ""

        # Optional: resolve a signed/temporary URL before downloading.
        # If your URLs don't expire, skip this block and use task.source_url directly.
        try:
            resp = requests.get(
                f"{base_url}/api/resolve/{task.task_id}",
                params={"key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            url = resp.json().get("url", task.source_url)
        except requests.RequestException:
            url = task.source_url  # fall back to the URL on the task

        status_callback("downloading", None)

        dest = TMP_DIR / f"{task.task_id}.tmp"
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            with requests.get(url, stream=True, timeout=60) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                with dest.open("wb") as fh:
                    for chunk in r.iter_content(chunk_size=65536):
                        if cancel_flag.is_set():
                            dest.unlink(missing_ok=True)
                            return None
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            progress_callback(downloaded / total * 100)
        except requests.RequestException as exc:
            dest.unlink(missing_ok=True)
            raise RuntimeError(f"Download failed: {exc}") from exc

        return str(dest)

    def cancel(self, task_id: str) -> bool:
        # cancel_flag in download() handles cancellation; nothing extra needed here.
        return True

    # ── Optional: support restart-safe retry for expiring URLs ────────────
    # If your download URLs expire (e.g. signed S3 links), override this method.
    # Shelfmark calls it at queue time and stores the result so it can re-resolve
    # the URL if a download is interrupted and needs to restart.
    #
    # def build_retry_resolution_fields(self, release_data: dict) -> dict:
    #     return {
    #         "retry_download_url": release_data.get("download_url"),
    #         "retry_download_protocol": "http",
    #     }


# ─────────────────────────────────────────────────────────────────────────────
# 3.  Settings fields  —  OPTIONAL
#
#     Shelfmark always creates a settings tab for your plugin containing at
#     minimum an Enable/Disable toggle. Define get_settings_fields() to add
#     extra fields below it — URLs, API keys, checkboxes, etc.
#
#     Values are stored in $CONFIG_DIR/plugins/custom_<filename>.json
#     and readable anywhere via:
#       from shelfmark.core.config import config
#       config.get("MY_KEY", "default_value")
#
#     ⚠ IMPORTANT: field keys are GLOBAL across all plugins. Two plugins
#     both using key="API_KEY" will share one config slot and overwrite each
#     other. Always prefix your keys with a unique source identifier:
#       key="MY_TRACKER_API_KEY"  ✓
#       key="API_KEY"             ✗ (conflicts with any other plugin using API_KEY)
#
#     Also: do NOT call config.get() inside get_settings_fields(). This function
#     defines the field schema only — current values are loaded by Shelfmark.
#     Calling config.get() here during startup can cause a deadlock.
#
#     ActionButton: when clicked, Shelfmark runs the callback server-side.
#     The function must return {"success": True/False, "message": "..."}.
#     The message is shown as a green (success) or red (failure) notice.
# ─────────────────────────────────────────────────────────────────────────────


def get_settings_fields() -> list:
    """Return extra settings fields shown under this source's settings tab."""
    from shelfmark.core.settings_registry import (
        ActionButton,
        CheckboxField,
        PasswordField,
        TextField,
    )

    return [
        TextField(
            key="MY_TRACKER_URL",
            label="Tracker URL",
            description="Base URL of your tracker (e.g. https://tracker.example.com)",
            placeholder="https://tracker.example.com",
            default="",
        ),
        PasswordField(
            key="MY_TRACKER_API_KEY",
            label="API Key",
            description="Your tracker API key. Stored securely and never shown in the UI again.",
            placeholder="paste your key here",
            default="",
        ),
        CheckboxField(
            key="MY_TRACKER_PREFER_EPUB",
            label="Prefer EPUB",
            description="Download EPUB when both EPUB and PDF are available.",
            default=True,
        ),
        # Optional: add a test-connection button.
        # The callback runs server-side when the user clicks the button.
        ActionButton(
            key="MY_TRACKER_TEST",
            label="Test connection",
            description="Check that Shelfmark can reach the tracker with the URL and key above.",
            style="default",
            callback=_test_tracker_connection,
        ),
    ]


def _test_tracker_connection() -> dict:
    """Called when the user clicks the Test Connection button."""
    from shelfmark.core.config import config

    url = config.get("MY_TRACKER_URL", "")
    key = config.get("MY_TRACKER_API_KEY", "")
    if not url or not key:
        return {"success": False, "message": "URL and API key are required."}
    try:
        resp = requests.get(f"{url}/api/ping", params={"key": key}, timeout=10)
        resp.raise_for_status()
        return {"success": True, "message": "Connected successfully."}
    except requests.RequestException as exc:
        return {"success": False, "message": f"Connection failed: {exc}"}


# ─────────────────────────────────────────────────────────────────────────────
# 4.  Torrent / NZB hand-off  (alternative to the HTTP handler above)
#
#     If your source returns torrents or NZBs, use ExternalClientHandler.
#     Shelfmark hands the torrent/NZB to whatever download client the user
#     has configured (qBittorrent, Deluge, SABnzbd, NZBGet, etc.).
#     You do NOT write a download() method at all — just resolve the URL.
#
#     You also do NOT call status_callback or progress_callback.
#     Shelfmark polls the download client directly for speed and progress
#     and reports everything to the UI automatically.
#
#     To use this: replace MyTrackerHandler above with the class below,
#     and set protocol=ReleaseProtocol.TORRENT (or NZB) on your Release objects.
# ─────────────────────────────────────────────────────────────────────────────

# @register_handler(SOURCE_NAME)
# class MyTrackerTorrentHandler(ExternalClientHandler):
#
#     def _resolve_download(self, task, status_callback):
#         """Return a DownloadRequest describing what to send to the download client."""
#         from shelfmark.download.clients.base_handler import DownloadRequest
#
#         from shelfmark.core.config import config
#         base_url = config.get("MY_TRACKER_URL", "") or ""
#         api_key = config.get("MY_TRACKER_API_KEY", "") or ""
#
#         status_callback("resolving", "Fetching torrent info…")
#
#         try:
#             resp = requests.get(
#                 f"{base_url}/api/torrent/{task.task_id}",
#                 params={"key": api_key},
#                 timeout=15,
#             )
#             resp.raise_for_status()
#             data = resp.json()
#         except requests.RequestException as exc:
#             raise RuntimeError(f"Could not resolve torrent: {exc}") from exc
#
#         return DownloadRequest(
#             url=data["magnet"],           # magnet link or .torrent URL
#             protocol="torrent",           # "torrent" or "usenet"
#             release_name=task.title,
#             expected_hash=data.get("info_hash"),   # optional, for dedup
#         )
#
#     def cancel(self, task_id):
#         return True

# To use ExternalClientHandler, uncomment the import:
# from shelfmark.download.clients.base_handler import ExternalClientHandler, DownloadRequest

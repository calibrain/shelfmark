"""Debrid-Link debrid service client for Shelfmark.

Routes magnet links through the Debrid-Link REST API (v2) to download torrent
content via Debrid-Link's seedbox infrastructure.

Unlike the other debrid services, a completed Debrid-Link torrent already
carries a direct ``downloadUrl`` on every file, so there is no per-file
unrestrict or link-request round trip.
"""

from __future__ import annotations

import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, NoReturn

import requests

from shelfmark.config.env import TMP_DIR
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.download.clients import (
    DownloadClient,
    DownloadState,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients._coercion import config_text
from shelfmark.download.clients.torrent_utils import (
    DebridMagnet,
    DebridUpload,
    resolve_debrid_upload,
)
from shelfmark.download.http import download_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

_API_BASE = "https://debrid-link.com/api/v2"

_DEBRIDLINK_CLIENT_ERRORS = (
    AttributeError,
    OSError,
    requests.exceptions.RequestException,
    RuntimeError,
    TypeError,
    ValueError,
)

# Timeouts for API calls.
_API_TIMEOUT = 30
_STATUS_TIMEOUT = 15

# A torrent reports 0-100 in downloadPercent; 100 means Debrid-Link holds every file.
_COMPLETE_PERCENT = 100

# File extensions recognised as book or audiobook content.
_BOOK_EXTENSIONS = (
    ".aac",
    ".azw",
    ".azw3",
    ".cbr",
    ".cbz",
    ".djvu",
    ".doc",
    ".docx",
    ".epub",
    ".fb2",
    ".flac",
    ".lit",
    ".m4a",
    ".m4b",
    ".mobi",
    ".mp3",
    ".mp4",
    ".ogg",
    ".opus",
    ".pdf",
    ".rtf",
    ".txt",
    ".wma",
)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


@dataclass
class _DownloadState:
    """Internal mutable state for an in-progress Debrid-Link download."""

    torrent_id: str
    name: str
    target_dir: Path
    phase: str = "uploading"
    error_message: str | None = None
    progress: float = 0.0
    download_thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@register_client("torrent")
class DebridLinkClient(DownloadClient):
    """Debrid-Link debrid service client.

    Downloads torrent content by handing a magnet to Debrid-Link's seedbox,
    polling until it holds every file, then fetching each file over HTTP from
    the ``downloadUrl`` the seedbox already returned.

    API documentation: https://debrid-link.com/api_doc/v2/introduction
    """

    protocol = "torrent"
    name = "debridlink"

    _downloads: ClassVar[dict[str, _DownloadState]] = {}
    _downloads_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_key = config_text(config.get("DEBRIDLINK_API_KEY", ""))

    def _auth_headers(self) -> dict[str, str]:
        """Return Authorization header dict for API requests."""
        return {"Authorization": f"Bearer {self._api_key}"}

    # ------------------------------------------------------------------
    # DownloadClient interface
    # ------------------------------------------------------------------

    @staticmethod
    def is_configured() -> bool:
        """Return True when Debrid-Link is selected and an API key exists."""
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        api_key = config_text(config.get("DEBRIDLINK_API_KEY", ""))
        return client == "debridlink" and bool(api_key)

    def test_connection(self) -> tuple[bool, str]:
        """Validate the API key and check the account still has premium time."""
        if not self._api_key:
            return False, "Debrid-Link API Key is required"
        try:
            account = self._request_value("GET", "/account/infos", timeout=_STATUS_TIMEOUT)
            if not isinstance(account, dict):
                return False, "Unexpected response from Debrid-Link account endpoint"
            username = str(account.get("username") or account.get("email") or "Unknown")
            # premiumLeft is seconds of premium remaining; 0 means the account expired.
            if not account.get("premiumLeft"):
                return (
                    False,
                    f"Debrid-Link user '{username}' does not have an active Premium subscription",
                )
        except _DEBRIDLINK_CLIENT_ERRORS as e:
            return False, f"Connection failed: {e}"
        else:
            return True, f"Connected to Debrid-Link as '{username}' (Premium)"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Send a torrent to Debrid-Link's seedbox.

        Accepts a magnet link, a .torrent URL, or an indexer proxy URL. Anything
        that is not already a magnet is resolved first, then posted as a file,
        because the seedbox endpoint takes a magnet or hash in ``url`` and a
        .torrent only as multipart form data.
        """
        if not self._api_key:
            msg = "Debrid-Link API key is not configured"
            raise RuntimeError(msg)

        try:
            upload = resolve_debrid_upload(url, expected_hash=expected_hash)
            data = self._send_torrent(upload)

            torrent_id = str(data.get("id", ""))
            if not torrent_id:
                msg = "No torrent ID returned from Debrid-Link"
                _raise_runtime_error(msg)

            target_dir = TMP_DIR / f"debridlink_{torrent_id}"
            target_dir.mkdir(parents=True, exist_ok=True)

            state = _DownloadState(
                torrent_id=torrent_id,
                name=name,
                target_dir=target_dir,
                phase="waiting_dl",
            )
            with self._downloads_lock:
                self._downloads[torrent_id] = state

            logger.info(
                "Added torrent to Debrid-Link: ID %s (%s)",
                torrent_id,
                name,
            )

        except Exception:
            logger.exception("Failed to add torrent to Debrid-Link")
            raise

        else:
            return torrent_id

    def _send_torrent(self, upload: DebridUpload) -> dict[str, Any]:
        """Hand the torrent to the seedbox, as a magnet or as a file upload."""
        if isinstance(upload, DebridMagnet):
            data = self._request_value(
                "POST",
                "/seedbox/add",
                json={"url": upload.magnet_url},
                timeout=_API_TIMEOUT,
            )
        else:
            # A .torrent must go as multipart/form-data under the "file" field;
            # the JSON body only accepts a magnet, a hash or a torrent URL.
            data = self._request_value(
                "POST",
                "/seedbox/add",
                files={"file": ("upload.torrent", upload.torrent_data, "application/x-bittorrent")},
                timeout=_API_TIMEOUT,
            )

        if not isinstance(data, dict):
            msg = "Unexpected response when adding a torrent to Debrid-Link"
            _raise_runtime_error(msg)
        return data

    def get_status(self, download_id: str) -> DownloadStatus:
        """Poll Debrid-Link for torrent status and drive the download."""
        state = self._ensure_state(download_id)

        # Return cached terminal / in-flight states immediately.
        with state.lock:
            if state.phase == "error":
                return DownloadStatus.error(
                    state.error_message or "Debrid-Link error",
                )
            if state.phase == "complete":
                return DownloadStatus(
                    progress=100.0,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(state.target_dir),
                )
            if state.phase == "downloading_http":
                return DownloadStatus(
                    progress=state.progress,
                    state=DownloadState.DOWNLOADING,
                    message="Downloading files via HTTP...",
                    complete=False,
                    file_path=None,
                )

        try:
            torrent = self._fetch_torrent(download_id)
            if torrent is None:
                error_txt = f"Torrent {download_id} is no longer on Debrid-Link"
                with state.lock:
                    state.phase = "error"
                    state.error_message = error_txt
                return DownloadStatus.error(error_txt)
            return self._handle_torrent_info(torrent, state)

        except Exception as e:
            logger.exception(
                "Error checking Debrid-Link status for %s",
                download_id,
            )
            return DownloadStatus.error(str(e))

    def remove(
        self,
        download_id: str,
        *,
        delete_files: bool = False,
    ) -> bool:
        """Delete the torrent from Debrid-Link and clean up local files."""
        try:
            self._request_value(
                "DELETE",
                f"/seedbox/{download_id}/remove",
                timeout=_STATUS_TIMEOUT,
            )
        except _DEBRIDLINK_CLIENT_ERRORS as e:
            logger.warning("Failed to delete torrent from Debrid-Link: %s", e)

        with self._downloads_lock:
            state = self._downloads.pop(download_id, None)

        if state and state.target_dir.exists():
            shutil.rmtree(state.target_dir, ignore_errors=True)
        return True

    def get_download_path(self, download_id: str) -> str | None:
        """Return the local directory containing downloaded files."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state and state.phase == "complete":
            return str(state.target_dir)
        target_dir = TMP_DIR / f"debridlink_{download_id}"
        if target_dir.exists():
            return str(target_dir)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_value(
        self,
        method: str,
        path: str,
        *,
        timeout: int = _API_TIMEOUT,
        **kwargs: Any,
    ) -> Any:
        """Call the API and unwrap Debrid-Link's ``{success, value}`` envelope.

        Every v2 endpoint answers with that envelope, and a failure can arrive
        with an HTTP 200, so the body decides the outcome rather than the status
        code alone.
        """
        url = f"{_API_BASE}{path}"
        resp = requests.request(
            method,
            url,
            headers=self._auth_headers(),
            timeout=timeout,
            verify=get_ssl_verify(url),
            **kwargs,
        )
        resp.raise_for_status()
        payload = resp.json()

        if not isinstance(payload, dict):
            msg = f"Unexpected Debrid-Link response for {path}"
            _raise_runtime_error(msg)

        if not payload.get("success", False):
            detail = payload.get("error_description") or payload.get("error") or "unknown error"
            msg = f"Debrid-Link API error: {detail}"
            _raise_runtime_error(msg)

        return payload.get("value")

    def _fetch_torrent(self, download_id: str) -> dict[str, Any] | None:
        """Return the seedbox record for one torrent, or None when it is gone."""
        value = self._request_value(
            "GET",
            "/seedbox/list",
            params={"ids": download_id},
            timeout=_STATUS_TIMEOUT,
        )
        if not isinstance(value, list):
            return None
        for entry in value:
            if isinstance(entry, dict) and str(entry.get("id", "")) == download_id:
                return entry
        return None

    def _ensure_state(self, download_id: str) -> _DownloadState:
        """Get or create download state for the given torrent ID."""
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state:
            return state

        target_dir = TMP_DIR / f"debridlink_{download_id}"
        state = _DownloadState(
            torrent_id=download_id,
            name=f"Download {download_id}",
            target_dir=target_dir,
            phase="waiting_dl",
        )
        with self._downloads_lock:
            self._downloads[download_id] = state
        return state

    def _handle_torrent_info(
        self,
        info: dict[str, Any],
        state: _DownloadState,
    ) -> DownloadStatus:
        """Map a Debrid-Link seedbox torrent to a DownloadStatus."""
        if info.get("error"):
            error_txt = "Debrid-Link torrent error: " + str(
                info.get("errorString") or info.get("error"),
            )
            with state.lock:
                state.phase = "error"
                state.error_message = error_txt
            return DownloadStatus.error(error_txt)

        percent = _as_float(info.get("downloadPercent"))
        name = str(info.get("name") or state.name)

        if info.get("downloaded") or percent >= _COMPLETE_PERCENT:
            files = [f for f in info.get("files", []) if isinstance(f, dict)]
            self._maybe_start_download_thread(state, files)
            return DownloadStatus(
                progress=50.0,
                state=DownloadState.DOWNLOADING,
                message="Debrid-Link ready, retrieving files...",
                complete=False,
                file_path=None,
            )

        # Still being fetched by the seedbox: the first half of the progress bar.
        return DownloadStatus(
            progress=percent * 0.5,
            state=DownloadState.DOWNLOADING,
            message=f"Debrid-Link downloading torrent ({name})",
            complete=False,
            file_path=None,
            download_speed=int(_as_float(info.get("downloadSpeed"))),
        )

    def _maybe_start_download_thread(
        self,
        state: _DownloadState,
        files: list[dict[str, Any]],
    ) -> None:
        """Spawn a background thread to download the seedbox's files."""
        with state.lock:
            already_running = state.phase in (
                "downloading_http",
                "complete",
            )
            thread_alive = state.download_thread is not None and state.download_thread.is_alive()
            if already_running or thread_alive:
                return
            state.phase = "downloading_http"
            t = threading.Thread(
                target=self._process_and_download,
                args=(state, files),
                daemon=True,
            )
            state.download_thread = t
            t.start()

    # ------------------------------------------------------------------
    # File download pipeline
    # ------------------------------------------------------------------

    def _process_and_download(
        self,
        state: _DownloadState,
        files: list[dict[str, Any]],
    ) -> None:
        """Download each file over HTTP.

        Runs in a background thread spawned by ``_maybe_start_download_thread``.
        Debrid-Link puts a ready-to-use ``downloadUrl`` on every file, so unlike
        the other debrid clients there is nothing to unrestrict here.
        """
        try:
            downloadable = [f for f in files if f.get("downloadUrl")]
            if not downloadable:
                msg = "No download links returned by Debrid-Link"
                _raise_runtime_error(msg)

            relevant = [
                f for f in downloadable if str(f.get("name", "")).lower().endswith(_BOOK_EXTENSIONS)
            ]
            if not relevant:
                relevant = downloadable

            total = len(relevant)
            for idx, file_info in enumerate(relevant):
                direct_url = str(file_info.get("downloadUrl", ""))
                rel_path = Path(str(file_info.get("name") or f"file_{idx + 1}").lstrip("/"))

                dest = state.target_dir / rel_path
                dest.parent.mkdir(parents=True, exist_ok=True)

                logger.info(
                    "Downloading Debrid-Link file %d/%d: %s",
                    idx + 1,
                    total,
                    rel_path,
                )

                buf = download_url(
                    direct_url,
                    referer="https://debrid-link.com/",
                )
                if not buf:
                    msg = f"Failed to download from {direct_url}"
                    _raise_runtime_error(msg)

                with dest.open("wb") as fh:
                    fh.write(buf.getvalue())

                with state.lock:
                    state.progress = 50.0 + (idx + 1) / total * 50.0

            with state.lock:
                state.phase = "complete"
                state.progress = 100.0

            logger.info(
                "Debrid-Link download complete for ID %s at %s",
                state.torrent_id,
                state.target_dir,
            )

        except Exception as e:
            logger.exception(
                "Error in Debrid-Link download for ID %s",
                state.torrent_id,
            )
            with state.lock:
                state.phase = "error"
                state.error_message = str(e) or "Download failed"


def _as_float(value: object) -> float:
    """Coerce an API numeric field to a float, treating anything odd as zero."""
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0

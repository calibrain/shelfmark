"""Torbox debrid service client for Shelfmark."""

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
from shelfmark.download.http import download_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

_API_BASE = "https://api.torbox.app/v1/api"
_API_TIMEOUT = 30
_STATUS_TIMEOUT = 15
_READY_STATES = frozenset({"cached", "completed"})
_BOOK_EXTENSIONS = (
    ".aac", ".azw", ".azw3", ".cbr", ".cbz", ".djvu", ".doc", ".docx",
    ".epub", ".fb2", ".flac", ".lit", ".m4a", ".m4b", ".mobi", ".mp3",
    ".ogg", ".opus", ".pdf", ".rtf", ".txt", ".wma",
)


def _raise_runtime_error(message: str) -> NoReturn:
    raise RuntimeError(message)


def _raise_type_error(message: str) -> NoReturn:
    raise TypeError(message)


@dataclass
class _DownloadState:
    """Internal mutable state for an in-progress Torbox download."""

    torrent_id: str
    name: str
    target_dir: Path
    phase: str = "uploading"
    error_message: str | None = None
    progress: float = 0.0
    download_thread: threading.Thread | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


@register_client("torrent")
class TorboxClient(DownloadClient):
    """Download torrent content through Torbox's CDN.

    API documentation: https://api.torbox.app/docs
    """

    protocol = "torrent"
    name = "torbox"

    _downloads: ClassVar[dict[str, _DownloadState]] = {}
    _downloads_lock = threading.Lock()

    def __init__(self) -> None:
        self._api_key = config_text(config.get("TORBOX_API_KEY", ""))

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @staticmethod
    def is_configured() -> bool:
        client = config_text(config.get("PROWLARR_TORRENT_CLIENT", ""))
        api_key = config_text(config.get("TORBOX_API_KEY", ""))
        return client == "torbox" and bool(api_key)

    def test_connection(self) -> tuple[bool, str]:
        """Validate credentials and confirm an active Torbox subscription."""
        if not self._api_key:
            return False, "Torbox API Key is required"
        try:
            url = f"{_API_BASE}/user/me"
            response = requests.get(
                url,
                headers=self._auth_headers(),
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(url),
            )
            response.raise_for_status()
            data = self._response_data(response.json())
            if not isinstance(data, dict):
                msg = "Unexpected Torbox user response"
                _raise_type_error(msg)
            email = data.get("email", "Unknown")
            if not data.get("is_subscribed", False) or not data.get("plan", 0):
                return False, f"Torbox user '{email}' does not have an active subscription"
        except (requests.exceptions.RequestException, RuntimeError, TypeError, ValueError) as error:
            return False, f"Connection failed: {error}"
        else:
            return True, f"Connected to Torbox as '{email}'"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        """Add a magnet to Torbox and return its torrent ID."""
        if not self._api_key:
            msg = "Torbox API key is not configured"
            raise RuntimeError(msg)

        magnet_link = url
        if not magnet_link.startswith("magnet:") and expected_hash:
            magnet_link = f"magnet:?xt=urn:btih:{expected_hash}"

        create_url = f"{_API_BASE}/torrents/createtorrent"
        try:
            response = requests.post(
                create_url,
                headers=self._auth_headers(),
                data={"magnet": magnet_link, "name": name},
                timeout=_API_TIMEOUT,
                verify=get_ssl_verify(create_url),
            )
            response.raise_for_status()
            data = self._response_data(response.json())
            torrent_id = str(data.get("torrent_id", data.get("id", "")))
            if not torrent_id:
                msg = "No torrent ID returned from Torbox"
                _raise_runtime_error(msg)

            target_dir = TMP_DIR / f"torbox_{torrent_id}"
            target_dir.mkdir(parents=True, exist_ok=True)
            state = _DownloadState(torrent_id, name, target_dir, phase="waiting_torbox")
            with self._downloads_lock:
                self._downloads[torrent_id] = state
        except Exception:
            logger.exception("Failed to add magnet to Torbox")
            raise
        else:
            logger.info("Added torrent to Torbox: ID %s (%s)", torrent_id, name)
            return torrent_id

    def get_status(self, download_id: str) -> DownloadStatus:
        """Poll Torbox and start the local CDN download when ready."""
        state = self._ensure_state(download_id)
        with state.lock:
            if state.phase == "error":
                return DownloadStatus.error(state.error_message or "Torbox error")
            if state.phase == "complete":
                return DownloadStatus(100.0, DownloadState.COMPLETE, "Complete", True, str(state.target_dir))
            if state.phase == "downloading_http":
                return DownloadStatus(state.progress, DownloadState.DOWNLOADING, "Downloading files via HTTP...", False, None)

        try:
            list_url = f"{_API_BASE}/torrents/mylist"
            response = requests.get(
                list_url,
                headers=self._auth_headers(),
                params={"id": download_id, "bypass_cache": "true"},
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(list_url),
            )
            response.raise_for_status()
            torrent = self._response_data(response.json())
            if isinstance(torrent, list):
                torrent = torrent[0] if torrent else {}
            if not isinstance(torrent, dict):
                msg = "Unexpected Torbox torrent response"
                _raise_type_error(msg)
            return self._handle_torrent(torrent, state)
        except Exception as error:
            logger.exception("Error checking Torbox status for %s", download_id)
            return DownloadStatus.error(str(error))

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        """Delete the torrent from Torbox and clean up local files."""
        try:
            url = f"{_API_BASE}/torrents/controltorrent"
            requests.post(
                url,
                headers=self._auth_headers(),
                json={"torrent_id": int(download_id), "operation": "delete"},
                timeout=_STATUS_TIMEOUT,
                verify=get_ssl_verify(url),
            ).raise_for_status()
        except (requests.exceptions.RequestException, ValueError) as error:
            logger.warning("Failed to delete Torbox torrent %s: %s", download_id, error)

        with self._downloads_lock:
            state = self._downloads.pop(download_id, None)
        target_dir = state.target_dir if state else TMP_DIR / f"torbox_{download_id}"
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        return True

    def get_download_path(self, download_id: str) -> str | None:
        with self._downloads_lock:
            state = self._downloads.get(download_id)
        if state and state.phase == "complete":
            return str(state.target_dir)
        target_dir = TMP_DIR / f"torbox_{download_id}"
        return str(target_dir) if target_dir.exists() else None

    @staticmethod
    def _response_data(payload: dict[str, Any]) -> Any:
        """Return Torbox's data envelope or surface its API error detail."""
        if not payload.get("success", False):
            msg = str(payload.get("detail") or payload.get("error") or "Torbox API error")
            raise RuntimeError(msg)
        return payload.get("data")

    def _ensure_state(self, download_id: str) -> _DownloadState:
        with self._downloads_lock:
            state = self._downloads.get(download_id)
            if state:
                return state
            state = _DownloadState(download_id, f"Download {download_id}", TMP_DIR / f"torbox_{download_id}", phase="waiting_torbox")
            self._downloads[download_id] = state
            return state

    def _handle_torrent(self, torrent: dict[str, Any], state: _DownloadState) -> DownloadStatus:
        status = str(torrent.get("download_state", "")).lower()
        progress = float(torrent.get("progress", 0.0)) * 100.0
        if status in _READY_STATES:
            files = torrent.get("files", [])
            self._maybe_start_download_thread(state, files if isinstance(files, list) else [])
            return DownloadStatus(50.0, DownloadState.DOWNLOADING, "Torbox ready, retrieving files...", False, None)
        if status == "paused":
            return DownloadStatus(progress * 0.5, DownloadState.PAUSED, "Torbox torrent is paused", False, None)
        if status == "stalled (no seeds)":
            return DownloadStatus(progress * 0.5, DownloadState.DOWNLOADING, "Torbox torrent stalled (no seeds)", False, None)
        return DownloadStatus(
            progress * 0.5,
            DownloadState.DOWNLOADING,
            f"Torbox downloading torrent ({torrent.get('name', state.name)})",
            False,
            None,
            download_speed=int(torrent.get("download_speed", 0)),
            eta=torrent.get("eta"),
        )

    def _maybe_start_download_thread(self, state: _DownloadState, files: list[dict[str, Any]]) -> None:
        with state.lock:
            if state.phase in {"downloading_http", "complete"} or (state.download_thread and state.download_thread.is_alive()):
                return
            state.phase = "downloading_http"
            state.download_thread = threading.Thread(target=self._download_files, args=(state, files), daemon=True)
            state.download_thread.start()

    def _download_files(self, state: _DownloadState, files: list[dict[str, Any]]) -> None:
        try:
            relevant = [file for file in files if str(file.get("name", "")).lower().endswith(_BOOK_EXTENSIONS)] or files
            if not relevant:
                msg = "No files found in Torbox torrent"
                _raise_runtime_error(msg)
            for index, file_info in enumerate(relevant, start=1):
                file_id = file_info.get("id")
                if file_id is None:
                    msg = "Torbox torrent file is missing an ID"
                    _raise_runtime_error(msg)
                filename = self._safe_filename(str(file_info.get("name") or f"file_{index}"))
                direct_url = self._request_download_link(state.torrent_id, file_id)
                destination = state.target_dir / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                buffer = download_url(direct_url, referer="https://torbox.app/")
                if not buffer:
                    msg = f"Failed to download from {direct_url}"
                    _raise_runtime_error(msg)
                with destination.open("wb") as output:
                    output.write(buffer.getvalue())
                with state.lock:
                    state.progress = 50.0 + index / len(relevant) * 50.0
            with state.lock:
                state.phase = "complete"
                state.progress = 100.0
        except Exception as error:
            logger.exception("Error downloading Torbox torrent %s", state.torrent_id)
            with state.lock:
                state.phase = "error"
                state.error_message = str(error)

    def _request_download_link(self, torrent_id: str, file_id: int | str) -> str:
        url = f"{_API_BASE}/torrents/requestdl"
        response = requests.get(
            url,
            params={"token": self._api_key, "torrent_id": torrent_id, "file_id": file_id},
            timeout=_API_TIMEOUT,
            verify=get_ssl_verify(url),
        )
        response.raise_for_status()
        data = self._response_data(response.json())
        if not isinstance(data, str) or not data:
            msg = "Torbox did not return a download link"
            _raise_runtime_error(msg)
        return data

    @staticmethod
    def _safe_filename(filename: str) -> Path:
        path = Path(filename.lstrip("/"))
        if not path.parts or ".." in path.parts:
            return Path(path.name or "download")
        return path

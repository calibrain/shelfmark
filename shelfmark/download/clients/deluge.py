"""Deluge download client for Prowlarr integration.

This implementation talks to Deluge via the Web UI JSON-RPC API (``/json``).

Why Web UI API instead of daemon RPC (port 58846)?
- Matches the approach used by common automation apps
- Avoids requiring Deluge daemon ``auth`` file credentials (username/password)

Requirements:
- ``deluge-web`` must be enabled and reachable from Shelfmark
- Deluge Web UI must be connected (or connectable) to a Deluge daemon
"""

import base64
from typing import Any, Optional, Tuple
from urllib.parse import urlparse

import requests

from shelfmark.core.config import config
from shelfmark.download.network import get_ssl_verify
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.clients import (
    DownloadClient,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients.torrent_utils import (
    extract_torrent_info,
)

logger = setup_logger(__name__)


class DelugeRpcError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def _get_error_message(error: Any) -> Tuple[str, int | None]:
    if isinstance(error, dict):
        return str(error.get("message") or error), error.get("code")
    return str(error), None


@register_client("torrent")
class DelugeClient(DownloadClient):
    """Deluge download client using Deluge Web UI JSON-RPC."""

    protocol = "torrent"
    name = "deluge"

    def __init__(self):
        raw_host = str(config.get("DELUGE_HOST", "localhost") or "")
        raw_port = str(config.get("DELUGE_PORT", "8112") or "8112")
        password = str(config.get("DELUGE_PASSWORD", "") or "")

        if not raw_host:
            raise ValueError("DELUGE_HOST is required")
        if not password:
            raise ValueError("DELUGE_PASSWORD is required")

        scheme = "http"
        base_path = ""

        # Allow DELUGE_HOST to be either a hostname OR a full URL
        # (useful when Deluge is behind a reverse proxy path).
        raw_host = normalize_http_url(raw_host, strip_trailing_slash=False) if raw_host else ""
        if not raw_host:
            raise ValueError("DELUGE_HOST is invalid")

        host = raw_host
        port = int(raw_port)

        if raw_host.startswith(("http://", "https://")):
            parsed = urlparse(raw_host)
            scheme = parsed.scheme or "http"
            host = parsed.hostname or "localhost"
            if parsed.port is not None:
                port = parsed.port
            base_path = (parsed.path or "").rstrip("/")
        else:
            # Allow "host:port" in DELUGE_HOST for convenience.
            if ":" in raw_host and raw_host.count(":") == 1:
                host_part, port_part = raw_host.split(":", 1)
                if host_part and port_part.isdigit():
                    host = host_part
                    port = int(port_part)

        self._rpc_url = f"{scheme}://{host}:{port}{base_path}/json"
        self._password = password
        self._session = requests.Session()

        self._authenticated = False
        self._connected = False
        self._rpc_id = 0

        self._category = str(config.get("DELUGE_CATEGORY", "books") or "books")
        self._download_dir = str(config.get("DELUGE_DOWNLOAD_DIR", "") or "")

    def _next_rpc_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _rpc_call(self, method: str, *params: Any, timeout: int = 15) -> Any:
        payload = {
            "id": self._next_rpc_id(),
            "method": method,
            "params": list(params),
        }

        response = self._session.post(self._rpc_url, json=payload, timeout=timeout, verify=get_ssl_verify(self._rpc_url))
        response.raise_for_status()

        data = response.json()
        if data.get("error"):
            message, code = _get_error_message(data["error"])
            raise DelugeRpcError(message, code)

        return data.get("result")

    def _login(self) -> None:
        result = self._rpc_call("auth.login", self._password)
        if result is not True:
            raise DelugeRpcError("Deluge Web UI authentication failed")
        self._authenticated = True

    def _select_daemon_host_id(self, hosts: list) -> str:
        # Hosts returned by web.get_hosts look like:
        #   [[host_id, host, port, status], ...]
        preferred_hosts = {"127.0.0.1", "localhost"}

        for entry in hosts:
            if isinstance(entry, list) and len(entry) >= 2 and entry[1] in preferred_hosts:
                return str(entry[0])

        for entry in hosts:
            if isinstance(entry, list) and len(entry) >= 4 and str(entry[3]).lower() == "online":
                return str(entry[0])

        return str(hosts[0][0])

    def _ensure_connected(self) -> None:
        if not self._authenticated:
            self._login()

        if self._connected:
            return

        if self._rpc_call("web.connected") is True:
            self._connected = True
            return

        hosts = self._rpc_call("web.get_hosts") or []
        if not hosts:
            raise DelugeRpcError(
                "Deluge Web UI isn't connected to Deluge core (no hosts configured). "
                "Add/connect a daemon in Deluge Web UI → Connection Manager."
            )

        host_id = self._select_daemon_host_id(hosts)
        self._rpc_call("web.connect", host_id)

        if self._rpc_call("web.connected") is not True:
            raise DelugeRpcError(
                "Deluge Web UI couldn't connect to Deluge core. "
                "Check daemon status in Deluge Web UI → Connection Manager."
            )

        self._connected = True

    def _get_daemon_version(self) -> Any:
        """Fetch daemon version, preferring daemon.get_version when available."""
        try:
            methods = self._rpc_call("system.listMethods")
            if isinstance(methods, list) and "daemon.get_version" in methods:
                return self._rpc_call("daemon.get_version")
        except Exception:
            # Fall back to daemon.info to preserve existing behavior.
            pass

        return self._rpc_call("daemon.info")

    def _try_set_label(self, torrent_id: str, label: str) -> None:
        """Best-effort label assignment (requires Deluge Label plugin)."""
        if not label:
            return

        try:
            # label.add will error if the plugin is unavailable or the label exists.
            try:
                self._rpc_call("label.add", label)
            except Exception:
                pass

            self._rpc_call("label.set_torrent", torrent_id, label)
        except Exception as e:
            logger.debug(f"Could not set Deluge label '{label}' for {torrent_id}: {e}")

    @staticmethod
    def is_configured() -> bool:
        client = config.get("PROWLARR_TORRENT_CLIENT", "")
        host = config.get("DELUGE_HOST", "")
        password = config.get("DELUGE_PASSWORD", "")
        return client == "deluge" and bool(host) and bool(password)

    def test_connection(self) -> Tuple[bool, str]:
        try:
            self._ensure_connected()
            version = self._get_daemon_version()
            return True, f"Connected to Deluge {version}"
        except Exception as e:
            self._authenticated = False
            self._connected = False
            return False, f"Connection failed: {str(e)}"

    def add_download(
        self,
        url: str,
        name: str,
        category: Optional[str] = None,
        expected_hash: Optional[str] = None,
        **kwargs,
    ) -> str:
        try:
            self._ensure_connected()

            category_value = str(category or self._category)

            torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
            if not torrent_info.is_magnet and not torrent_info.torrent_data:
                raise Exception("Failed to fetch torrent file")

            options: dict[str, Any] = {}
            if self._download_dir:
                options["download_location"] = self._download_dir

            if torrent_info.is_magnet:
                magnet_url = torrent_info.magnet_url or url
                torrent_id = self._rpc_call("core.add_torrent_magnet", magnet_url, options)
            else:
                torrent_data = torrent_info.torrent_data
                if torrent_data is None:
                    raise Exception("Failed to fetch torrent file")

                torrent_data_bytes: bytes = torrent_data
                filedump = base64.b64encode(torrent_data_bytes).decode("ascii")
                torrent_id = self._rpc_call(
                    "core.add_torrent_file",
                    f"{name}.torrent",
                    filedump,
                    options,
                )

            if not torrent_id:
                raise Exception("Deluge returned no torrent ID")

            torrent_id = str(torrent_id).lower()
            self._try_set_label(torrent_id, category_value)

            logger.info(f"Added torrent to Deluge: {torrent_id}")
            return torrent_id

        except Exception as e:
            self._authenticated = False
            self._connected = False
            logger.error(f"Deluge add failed: {e}")
            raise

    def get_status(self, download_id: str) -> DownloadStatus:
        try:
            self._ensure_connected()

            status = self._rpc_call(
                "core.get_torrent_status",
                download_id,
                ["state", "progress", "download_payload_rate", "eta", "save_path", "name"],
            )

            if not status:
                return DownloadStatus.error("Torrent not found")

            # Deluge states: Downloading, Seeding, Paused, Checking, Queued, Error, Moving
            state_map = {
                "Downloading": ("downloading", None),
                "Seeding": ("seeding", "Seeding"),
                "Paused": ("paused", "Paused"),
                "Checking": ("checking", "Checking files"),
                "Queued": ("queued", "Queued"),
                "Error": ("error", "Error"),
                "Moving": ("processing", "Moving files"),
                "Allocating": ("downloading", "Allocating space"),
            }

            deluge_state = status.get("state", "Unknown")
            state, message = state_map.get(str(deluge_state), ("unknown", str(deluge_state)))

            progress = float(status.get("progress", 0))
            # Don't mark complete while files are being moved
            complete = progress >= 100 and deluge_state != "Moving"

            if complete:
                message = "Complete"

            eta = status.get("eta")
            if eta is not None:
                try:
                    eta = int(eta)
                except Exception:
                    eta = None

            if eta is not None and (eta < 0 or eta > 604800):
                eta = None

            file_path = None
            if complete:
                # Output path is save_path + torrent name
                file_path = self._build_path(
                    str(status.get("save_path", "")),
                    str(status.get("name", "")),
                )

            return DownloadStatus(
                progress=progress,
                state="complete" if complete else state,
                message=message,
                complete=complete,
                file_path=file_path,
                download_speed=status.get("download_payload_rate"),
                eta=eta,
            )

        except Exception as e:
            return DownloadStatus.error(self._log_error("get_status", e))

    def remove(self, download_id: str, delete_files: bool = False) -> bool:
        try:
            self._ensure_connected()

            result = self._rpc_call("core.remove_torrent", download_id, delete_files)
            if result:
                logger.info(
                    f"Removed torrent from Deluge: {download_id}"
                    + (" (with files)" if delete_files else "")
                )
                return True
            return False

        except Exception as e:
            self._log_error("remove", e)
            return False

    def get_download_path(self, download_id: str) -> Optional[str]:
        try:
            self._ensure_connected()

            status = self._rpc_call(
                "core.get_torrent_status",
                download_id,
                ["save_path", "name"],
            )

            if status:
                return self._build_path(
                    str(status.get("save_path", "")),
                    str(status.get("name", "")),
                )
            return None

        except Exception as e:
            self._log_error("get_download_path", e, level="debug")
            return None

    def find_existing(
        self, url: str, category: Optional[str] = None
    ) -> Optional[Tuple[str, DownloadStatus]]:
        try:
            self._ensure_connected()

            torrent_info = extract_torrent_info(url)
            if not torrent_info.info_hash:
                return None

            status = self._rpc_call(
                "core.get_torrent_status",
                torrent_info.info_hash,
                ["state"],
            )

            if status:
                full_status = self.get_status(torrent_info.info_hash)
                return (torrent_info.info_hash, full_status)

            return None

        except Exception as e:
            self._authenticated = False
            self._connected = False
            logger.debug(f"Error checking for existing torrent: {e}")
            return None

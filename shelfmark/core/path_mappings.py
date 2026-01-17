"""Remote path mapping utilities.

Used when an external download client reports a completed download path that does
not exist inside the Shelfmark runtime environment (commonly different Docker
volume mounts).

A mapping rewrites a remote path prefix into a local path prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class RemotePathMapping:
    host: str
    remote_path: str
    local_path: str


def _normalize_prefix(path: str) -> str:
    normalized = str(path or "").strip()
    if not normalized:
        return ""

    normalized = normalized.replace("\\", "/")

    if normalized != "/":
        normalized = normalized.rstrip("/")

    return normalized


def _normalize_host(host: str) -> str:
    return str(host or "").strip().lower()


def parse_remote_path_mappings(value: Any) -> list[RemotePathMapping]:
    if not value or not isinstance(value, list):
        return []

    mappings: list[RemotePathMapping] = []

    for row in value:
        if not isinstance(row, dict):
            continue

        host = _normalize_host(row.get("host", ""))
        remote_path = _normalize_prefix(row.get("remotePath", ""))
        local_path = _normalize_prefix(row.get("localPath", ""))

        if not host or not remote_path or not local_path:
            continue

        mappings.append(RemotePathMapping(host=host, remote_path=remote_path, local_path=local_path))

    mappings.sort(key=lambda m: len(m.remote_path), reverse=True)
    return mappings


def remap_remote_to_local(*, mappings: Iterable[RemotePathMapping], host: str, remote_path: str | Path) -> Path:
    host_normalized = _normalize_host(host)
    remote_normalized = _normalize_prefix(str(remote_path))

    if not remote_normalized:
        return Path(str(remote_path))

    for mapping in mappings:
        if _normalize_host(mapping.host) != host_normalized:
            continue

        remote_prefix = _normalize_prefix(mapping.remote_path)
        if not remote_prefix:
            continue

        if remote_normalized == remote_prefix or remote_normalized.startswith(remote_prefix + "/"):
            remainder = remote_normalized[len(remote_prefix) :]
            local_prefix = _normalize_prefix(mapping.local_path)

            if remainder.startswith("/"):
                remainder = remainder[1:]

            return Path(local_prefix) / remainder if remainder else Path(local_prefix)

    return Path(remote_normalized)


def get_client_host_identifier(client: Any) -> Optional[str]:
    """Return a stable identifier used by the mapping UI.

    Sonarr uses the download client's configured host. Shelfmark currently uses
    the download client 'name' (e.g. qbittorrent, sabnzbd).
    """

    name = getattr(client, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip().lower()

    return None

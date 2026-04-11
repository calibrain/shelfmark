from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from shelfmark.core.models import DownloadTask

CWA_SIDECAR_MANIFEST_SETTING = "ENABLE_CWA_SIDECAR_MANIFEST"


def _normalize_optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_identifier(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        value = str(value)
    return _normalize_optional_text(value)


def _extract_hardcover_slug_from_url(value: Any) -> str | None:
    raw_url = _normalize_optional_text(value)
    if raw_url is None:
        return None
    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return None

    hostname = (parsed.hostname or "").lower()
    if hostname not in {"hardcover.app", "www.hardcover.app"}:
        return None

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) >= 2 and path_parts[0] == "books":
        return _normalize_optional_text(path_parts[1])
    return None


def sidecar_path_for(delivered_path: Path) -> Path:
    """Return the CWA sidecar path for a delivered file path."""
    return delivered_path.with_name(f"{delivered_path.name}.cwa.json")


def cwa_sidecar_manifest_enabled() -> bool:
    """Return whether additive `.cwa.json` sidecars are enabled for CWA ingest."""
    from shelfmark.core.config import config

    return bool(config.get(CWA_SIDECAR_MANIFEST_SETTING, False))


def build_cwa_manifest(task: DownloadTask) -> Optional[dict[str, Any]]:
    """Build a `.cwa.json` manifest from exact task provenance.

    Only exact Hardcover provenance is emitted. If the task was not queued from a
    trusted Hardcover-backed request flow, return ``None``.
    """
    metadata_provider = _normalize_optional_text(getattr(task, "metadata_provider", None))
    metadata_provider_id = _normalize_identifier(getattr(task, "metadata_provider_id", None))
    if metadata_provider != "hardcover" or metadata_provider_id is None:
        return None

    hardcover_edition = _normalize_identifier(getattr(task, "hardcover_edition", None))
    hardcover_slug = _normalize_optional_text(getattr(task, "hardcover_slug", None))
    if hardcover_slug is None:
        hardcover_slug = _extract_hardcover_slug_from_url(getattr(task, "metadata_source_url", None))

    provenance: dict[str, Any] = {
        "provider": "hardcover",
        "provider_id": metadata_provider_id,
    }
    identifiers: dict[str, str] = {
        "hardcover-id": metadata_provider_id,
    }

    if hardcover_edition is not None:
        provenance["hardcover_edition"] = hardcover_edition
        identifiers["hardcover-edition"] = hardcover_edition
    if hardcover_slug is not None:
        provenance["hardcover_slug"] = hardcover_slug
        identifiers["hardcover-slug"] = hardcover_slug

    return {
        "provenance": provenance,
        "identifiers": identifiers,
    }


def write_cwa_sidecar(delivered_path: Path, task: DownloadTask) -> Path | None:
    """Write a `.cwa.json` sidecar next to *delivered_path* when provenance is exact."""
    manifest = build_cwa_manifest(task)
    if manifest is None:
        return None

    sidecar_path = sidecar_path_for(delivered_path)
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(sidecar_path.parent),
            prefix=f".{sidecar_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, sidecar_path)
        temp_path = None
        return sidecar_path
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

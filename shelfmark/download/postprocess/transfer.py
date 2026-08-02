"""Transfer the immutable output paths selected for a Book import."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import shelfmark.core.config as core_config
from shelfmark.download.fs import atomic_copy, atomic_hardlink, run_blocking_io

if TYPE_CHECKING:
    from shelfmark.core.models import DownloadTask


def should_hardlink(task: DownloadTask) -> bool:
    """Hardlink only retained torrent sources when explicitly enabled."""
    return bool(task.original_download_path) and bool(
        core_config.config.get("HARDLINK_TORRENTS", False)
    )


def is_torrent_source(source_path: Path, task: DownloadTask) -> bool:
    """Check whether a path is the retained torrent source."""
    return bool(task.original_download_path) and Path(task.original_download_path) == source_path


def transfer_selected_source_members(
    selections: list[tuple[Path, Path]], *, use_hardlink: bool
) -> tuple[list[Path], str | None, dict[str, int]]:
    """Copy or link explicitly selected source members to their planned paths."""
    if not selections:
        return [], "No source members selected", {"hardlink": 0, "copy": 0}

    destinations = [destination for _, destination in selections]
    if len(set(destinations)) != len(destinations):
        msg = "selected source members have duplicate planned output paths"
        raise ValueError(msg)
    for source, destination in selections:
        if not run_blocking_io(source.is_file):
            msg = f"selected source member is unavailable: {source}"
            raise FileNotFoundError(msg)
        if run_blocking_io(destination.exists):
            msg = f"planned output already exists: {destination}"
            raise FileExistsError(msg)

    final_paths: list[Path] = []
    op_counts = {"hardlink": 0, "copy": 0}
    for source, destination in selections:
        run_blocking_io(destination.parent.mkdir, parents=True, exist_ok=True)
        if use_hardlink:
            final_path = atomic_hardlink(source, destination)
            op = "hardlink"
        else:
            final_path = atomic_copy(source, destination)
            op = "copy"
        final_paths.append(final_path)
        op_counts[op] += 1

    return final_paths, None, op_counts

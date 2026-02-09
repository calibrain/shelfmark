from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import shelfmark.core.config as core_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import DownloadTask
from shelfmark.core.naming import (
    assign_part_numbers,
    build_library_path,
    parse_naming_template,
    same_filesystem,
    sanitize_filename,
)
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.fs import atomic_copy, atomic_hardlink, atomic_move, run_blocking_io
from shelfmark.download.postprocess.policy import get_file_organization, get_template

from .scan import collect_directory_files, scan_directory_tree
from .types import TransferPlan
from .workspace import safe_cleanup_path

logger = setup_logger("shelfmark.download.postprocess.pipeline")


def should_hardlink(task: DownloadTask) -> bool:
    """Check if hardlinking is enabled for this task (Prowlarr torrents only)."""

    if task.source != "prowlarr":
        return False

    if not task.original_download_path:
        return False

    is_audiobook = check_audiobook(task.content_type)
    key = "HARDLINK_TORRENTS_AUDIOBOOK" if is_audiobook else "HARDLINK_TORRENTS"

    hardlink_enabled = core_config.config.get(key)
    if hardlink_enabled is None:
        hardlink_enabled = core_config.config.get("TORRENT_HARDLINK", False)

    return bool(hardlink_enabled)



def build_metadata_dict(task: DownloadTask) -> dict:
    return {
        "Author": task.author,
        "Title": task.title,
        "Subtitle": task.subtitle,
        "Year": task.year,
        "Series": task.series_name,
        "SeriesPosition": task.series_position,
    }


def resolve_hardlink_source(
    temp_file: Path,
    task: DownloadTask,
    destination: Optional[Path],
    status_callback=None,
) -> TransferPlan:
    """Resolve hardlink eligibility and source path for transfers."""

    use_hardlink = False
    source_path = temp_file
    hardlink_enabled = should_hardlink(task)

    if hardlink_enabled and task.original_download_path:
        hardlink_source = Path(task.original_download_path)
        hardlink_source_exists = run_blocking_io(hardlink_source.exists)
        if destination and hardlink_source_exists and run_blocking_io(same_filesystem, hardlink_source, destination):
            use_hardlink = True
            source_path = hardlink_source
        elif hardlink_source_exists:
            logger.warning(
                f"Cannot hardlink: {hardlink_source} and {destination} are on different filesystems. "
                "Falling back to copy. To fix: ensure torrent client downloads to same filesystem as destination."
            )
            if status_callback:
                status_callback("resolving", "Cannot hardlink (different filesystems), using copy")

    return TransferPlan(
        source_path=source_path,
        use_hardlink=use_hardlink,
        allow_archive_extraction=not hardlink_enabled,
        hardlink_enabled=hardlink_enabled,
    )


def is_torrent_source(source_path: Path, task: DownloadTask) -> bool:
    """Check if source is the torrent client path (needs copy to preserve seeding)."""

    if not task.original_download_path:
        return False

    original_path = Path(task.original_download_path)
    try:
        return run_blocking_io(source_path.resolve) == run_blocking_io(original_path.resolve)
    except (OSError, ValueError):
        try:
            return os.path.normpath(str(source_path)) == os.path.normpath(str(original_path))
        except Exception:
            return False


def _max_attempts_for_batch(file_count: int, default: int = 100) -> int:
    if file_count <= 1:
        return default
    return max(default, file_count + default)


def _transfer_single_file(
    source_path: Path,
    dest_path: Path,
    use_hardlink: bool,
    is_torrent: bool,
    preserve_source: bool = False,
    max_attempts: int = 100,
) -> Tuple[Path, str]:
    if use_hardlink:
        final_path = atomic_hardlink(source_path, dest_path, max_attempts=max_attempts)
        try:
            if run_blocking_io(source_path.stat).st_ino == run_blocking_io(final_path.stat).st_ino:
                return final_path, "hardlink"
        except OSError:
            return final_path, "hardlink"
        return final_path, "copy"

    if is_torrent or preserve_source:
        return atomic_copy(source_path, dest_path, max_attempts=max_attempts), "copy"

    return atomic_move(source_path, dest_path, max_attempts=max_attempts), "move"


def transfer_book_files(
    book_files: List[Path],
    destination: Path,
    task: DownloadTask,
    use_hardlink: bool,
    is_torrent: bool,
    preserve_source: bool = False,
    organization_mode: Optional[str] = None,
) -> Tuple[List[Path], Optional[str], Dict[str, int]]:
    if not book_files:
        return [], "No book files found", {"hardlink": 0, "copy": 0, "move": 0}

    is_audiobook = check_audiobook(task.content_type)
    organization_mode = organization_mode or get_file_organization(is_audiobook)
    max_attempts = _max_attempts_for_batch(len(book_files))

    final_paths: List[Path] = []
    op_counts: Dict[str, int] = {"hardlink": 0, "copy": 0, "move": 0}

    if organization_mode == "organize":
        template = get_template(is_audiobook, "organize")
        metadata = build_metadata_dict(task)

        if len(book_files) == 1:
            source_file = book_files[0]
            ext = source_file.suffix.lstrip(".") or task.format or ""
            dest_path = run_blocking_io(
                build_library_path,
                str(destination),
                template,
                metadata,
                extension=ext or None,
            )
            run_blocking_io(dest_path.parent.mkdir, parents=True, exist_ok=True)

            final_path, op = _transfer_single_file(
                source_file,
                dest_path,
                use_hardlink,
                is_torrent,
                preserve_source=preserve_source,
                max_attempts=max_attempts,
            )
            final_paths.append(final_path)
            op_counts[op] = op_counts.get(op, 0) + 1
            logger.debug(f"{op.capitalize()} to destination: {final_path.name}")
        else:
            zero_pad_width = max(len(str(len(book_files))), 2)
            files_with_parts = assign_part_numbers(book_files, zero_pad_width)

            for source_file, part_number in files_with_parts:
                ext = source_file.suffix.lstrip(".") or task.format or ""
                file_metadata = {**metadata, "PartNumber": part_number}
                dest_path = run_blocking_io(
                    build_library_path,
                    str(destination),
                    template,
                    file_metadata,
                    extension=ext or None,
                )
                run_blocking_io(dest_path.parent.mkdir, parents=True, exist_ok=True)

                final_path, op = _transfer_single_file(
                    source_file,
                    dest_path,
                    use_hardlink,
                    is_torrent,
                    preserve_source=preserve_source,
                    max_attempts=max_attempts,
                )
                final_paths.append(final_path)
                op_counts[op] = op_counts.get(op, 0) + 1
                logger.debug(f"{op.capitalize()} to destination: {final_path.name}")

        return final_paths, None, op_counts

    for book_file in book_files:
        if len(book_files) == 1 and organization_mode != "none":
            if not task.format:
                task.format = book_file.suffix.lower().lstrip(".")

            template = get_template(is_audiobook, "rename")
            metadata = build_metadata_dict(task)
            extension = book_file.suffix.lstrip(".") or task.format or ""

            filename = parse_naming_template(template, metadata, allow_path_separators=False)
            filename = Path(filename).name if filename else ""
            if filename and extension:
                filename = f"{sanitize_filename(filename)}.{extension}"
            else:
                filename = book_file.name
        else:
            filename = book_file.name

        dest_path = destination / filename
        final_path, op = _transfer_single_file(
            book_file,
            dest_path,
            use_hardlink,
            is_torrent,
            preserve_source=preserve_source,
            max_attempts=max_attempts,
        )
        final_paths.append(final_path)
        op_counts[op] = op_counts.get(op, 0) + 1
        logger.debug(f"{op.capitalize()} to destination: {final_path.name}")

    return final_paths, None, op_counts


def process_directory(
    directory: Path,
    ingest_dir: Path,
    task: DownloadTask,
    allow_archive_extraction: bool = True,
    use_hardlink: Optional[bool] = None,
) -> Tuple[List[Path], Optional[str]]:
    """Process staged directory: find book files, extract archives, move to ingest."""

    try:
        is_torrent = is_torrent_source(directory, task)
        book_files, _, cleanup_paths, error = collect_directory_files(
            directory,
            task,
            allow_archive_extraction=allow_archive_extraction,
            status_callback=None,
            cleanup_archives=not is_torrent,
        )

        if error:
            if not is_torrent:
                safe_cleanup_path(directory, task)
                for cleanup_path in cleanup_paths:
                    safe_cleanup_path(cleanup_path, task)
            return [], error

        if use_hardlink is None:
            use_hardlink = should_hardlink(task)

        final_paths, error, _op_counts = transfer_book_files(
            book_files,
            destination=ingest_dir,
            task=task,
            use_hardlink=use_hardlink,
            is_torrent=is_torrent,
        )

        if error:
            return [], error

        if not is_torrent:
            safe_cleanup_path(directory, task)
            for cleanup_path in cleanup_paths:
                safe_cleanup_path(cleanup_path, task)

        return final_paths, None

    except Exception as exc:
        logger.error_trace("Task %s: error processing directory %s: %s", task.task_id, directory, exc)
        if not is_torrent_source(directory, task):
            safe_cleanup_path(directory, task)
        return [], str(exc)


def transfer_file_to_library(
    source_path: Path,
    library_base: str,
    template: str,
    metadata: dict,
    task: DownloadTask,
    temp_file: Optional[Path],
    status_callback,
    use_hardlink: bool,
) -> Optional[str]:
    extension = source_path.suffix.lstrip(".") or task.format
    dest_path = run_blocking_io(build_library_path, library_base, template, metadata, extension)
    run_blocking_io(dest_path.parent.mkdir, parents=True, exist_ok=True)

    is_torrent = is_torrent_source(source_path, task)
    final_path, op = _transfer_single_file(
        source_path,
        dest_path,
        use_hardlink,
        is_torrent,
        max_attempts=_max_attempts_for_batch(1),
    )
    logger.info(f"Library {op}: {final_path}")
    if use_hardlink and op != "hardlink":
        logger.warning(
            "Library hardlink requested but %s used instead for %s",
            op,
            final_path,
        )

    if use_hardlink and temp_file and not is_torrent_source(temp_file, task):
        safe_cleanup_path(temp_file, task)

    status_callback("complete", "Complete")
    return str(final_path)


def transfer_directory_to_library(
    source_dir: Path,
    library_base: str,
    template: str,
    metadata: dict,
    task: DownloadTask,
    temp_file: Optional[Path],
    status_callback,
    use_hardlink: bool,
) -> Optional[str]:
    content_type = task.content_type.lower() if task.content_type else None
    source_files, _, _, scan_error = scan_directory_tree(source_dir, content_type)
    if scan_error:
        logger.warning(scan_error)
        status_callback("error", scan_error)
        if temp_file:
            safe_cleanup_path(temp_file, task)
        return None

    if not source_files:
        logger.warning(f"No supported files in {source_dir.name}")
        status_callback("error", "No supported file formats found")
        if temp_file:
            safe_cleanup_path(temp_file, task)
        return None

    base_library_path = run_blocking_io(
        build_library_path,
        library_base,
        template,
        metadata,
        extension=None,
    )
    run_blocking_io(base_library_path.parent.mkdir, parents=True, exist_ok=True)

    is_torrent = is_torrent_source(source_dir, task)
    transferred_paths: List[Path] = []
    op_counts: Dict[str, int] = {"hardlink": 0, "copy": 0, "move": 0}
    max_attempts = _max_attempts_for_batch(len(source_files))

    if len(source_files) == 1:
        source_file = source_files[0]
        ext = source_file.suffix.lstrip(".")
        dest_path = base_library_path.with_suffix(f".{ext}")
        final_path, op = _transfer_single_file(
            source_file,
            dest_path,
            use_hardlink,
            is_torrent,
            max_attempts=max_attempts,
        )
        logger.debug(f"Library {op}: {source_file.name} -> {final_path}")
        transferred_paths.append(final_path)
        op_counts[op] = op_counts.get(op, 0) + 1
    else:
        zero_pad_width = max(len(str(len(source_files))), 2)
        files_with_parts = assign_part_numbers(source_files, zero_pad_width)

        for source_file, part_number in files_with_parts:
            ext = source_file.suffix.lstrip(".")
            file_metadata = {**metadata, "PartNumber": part_number}
            file_path = run_blocking_io(build_library_path, library_base, template, file_metadata, extension=ext)
            run_blocking_io(file_path.parent.mkdir, parents=True, exist_ok=True)

            final_path, op = _transfer_single_file(
                source_file,
                file_path,
                use_hardlink,
                is_torrent,
                max_attempts=max_attempts,
            )
            logger.debug(f"Library {op}: {source_file.name} -> {final_path}")
            transferred_paths.append(final_path)
            op_counts[op] = op_counts.get(op, 0) + 1

    op_summary = ", ".join(
        f"{op}={count}" for op, count in op_counts.items() if count
    ) or "none"
    logger.info(
        "Created %d library file(s) in %s (ops: %s)",
        len(transferred_paths),
        base_library_path.parent,
        op_summary,
    )
    if use_hardlink and op_counts.get("copy", 0):
        logger.warning(
            "Library hardlink requested but %d of %d file(s) copied (fallback)",
            op_counts.get("copy", 0),
            len(transferred_paths),
        )

    if use_hardlink and temp_file and not is_torrent_source(temp_file, task):
        safe_cleanup_path(temp_file, task)
    elif not is_torrent:
        safe_cleanup_path(temp_file, task)
        safe_cleanup_path(source_dir, task)

    message = f"Complete ({len(transferred_paths)} files)" if len(transferred_paths) > 1 else "Complete"
    status_callback("complete", message)

    return str(transferred_paths[0])

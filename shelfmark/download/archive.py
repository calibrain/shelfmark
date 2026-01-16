"""Archive extraction utilities for downloaded book archives."""

import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

from shelfmark.core.logger import setup_logger
from shelfmark.download.postprocess.policy import (
    get_supported_audiobook_formats,
    get_supported_formats,
)
from shelfmark.core.utils import is_audiobook as check_audiobook
from shelfmark.download.fs import atomic_write

logger = setup_logger(__name__)


# Check for rarfile availability at module load
try:
    import rarfile

    RAR_AVAILABLE = True
except ImportError:
    RAR_AVAILABLE = False
    logger.warning("rarfile not installed - RAR extraction disabled")


class ArchiveExtractionError(Exception):
    """Raised when archive extraction fails."""

    pass


class PasswordProtectedError(ArchiveExtractionError):
    """Raised when archive requires a password."""

    pass


class CorruptedArchiveError(ArchiveExtractionError):
    """Raised when archive is corrupted."""

    pass


def is_archive(file_path: Path) -> bool:
    """Check if file is a supported archive format."""
    suffix = file_path.suffix.lower().lstrip(".")
    return suffix in ("zip", "rar")


def _is_supported_file(file_path: Path, content_type: Optional[str] = None) -> bool:
    """Check if file matches user's supported formats setting based on content type."""
    ext = file_path.suffix.lower().lstrip(".")
    if check_audiobook(content_type):
        supported_formats = get_supported_audiobook_formats()
    else:
        supported_formats = get_supported_formats()
    return ext in supported_formats


# All known ebook extensions (superset of what user might enable)
ALL_EBOOK_EXTENSIONS = {'.pdf', '.epub', '.mobi', '.azw', '.azw3', '.fb2', '.djvu', '.cbz', '.cbr', '.doc', '.docx', '.rtf', '.txt'}

# All known audio extensions (superset of what user might enable for audiobooks)
ALL_AUDIO_EXTENSIONS = {'.m4b', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.wma', '.wav', '.opus'}


def _filter_files(
    extracted_files: List[Path],
    content_type: Optional[str] = None,
) -> Tuple[List[Path], List[Path], List[Path]]:
    """Filter files by content type. Returns (matched, rejected_format, other)."""
    is_audiobook = check_audiobook(content_type)
    known_extensions = ALL_AUDIO_EXTENSIONS if is_audiobook else ALL_EBOOK_EXTENSIONS

    matched_files = []
    rejected_format_files = []
    other_files = []

    for file_path in extracted_files:
        if _is_supported_file(file_path, content_type):
            matched_files.append(file_path)
        elif file_path.suffix.lower() in known_extensions:
            rejected_format_files.append(file_path)
        else:
            other_files.append(file_path)

    return matched_files, rejected_format_files, other_files


def extract_archive(
    archive_path: Path,
    output_dir: Path,
    content_type: Optional[str] = None,
) -> Tuple[List[Path], List[str], List[Path]]:
    """Extract archive and filter by content type. Returns (matched, warnings, rejected)."""
    suffix = archive_path.suffix.lower().lstrip(".")

    if suffix == "zip":
        extracted_files, warnings = _extract_zip(archive_path, output_dir)
    elif suffix == "rar":
        extracted_files, warnings = _extract_rar(archive_path, output_dir)
    else:
        raise ArchiveExtractionError(f"Unsupported archive format: {suffix}")

    is_audiobook = check_audiobook(content_type)
    file_type_label = "audiobook" if is_audiobook else "book"

    # Filter files based on content type
    matched_files, rejected_files, other_files = _filter_files(extracted_files, content_type)

    # Delete rejected files (valid formats but not enabled by user)
    for rejected_file in rejected_files:
        try:
            rejected_file.unlink()
            logger.debug(f"Deleted rejected {file_type_label} file: {rejected_file.name}")
        except OSError as e:
            logger.warning(f"Failed to delete rejected {file_type_label} file {rejected_file}: {e}")

    if rejected_files:
        rejected_exts = sorted(set(f.suffix.lower() for f in rejected_files))
        warnings.append(f"Skipped {len(rejected_files)} {file_type_label}(s) with unsupported format: {', '.join(rejected_exts)}")

    # Delete other files (images, html, etc)
    for other_file in other_files:
        try:
            other_file.unlink()
            logger.debug(f"Deleted non-{file_type_label} file: {other_file.name}")
        except OSError as e:
            logger.warning(f"Failed to delete non-{file_type_label} file {other_file}: {e}")

    if other_files:
        warnings.append(f"Skipped {len(other_files)} non-{file_type_label} file(s)")

    return matched_files, warnings, rejected_files


def extract_archive_raw(
    archive_path: Path,
    output_dir: Path,
) -> Tuple[List[Path], List[str]]:
    """Extract archive without filtering (returns all extracted files)."""
    suffix = archive_path.suffix.lower().lstrip(".")

    if suffix == "zip":
        return _extract_zip(archive_path, output_dir)
    if suffix == "rar":
        return _extract_rar(archive_path, output_dir)

    raise ArchiveExtractionError(f"Unsupported archive format: {suffix}")


def _extract_files_from_archive(archive, output_dir: Path) -> List[Path]:
    """Extract files from ZipFile or RarFile to output_dir with security checks."""
    extracted_files = []

    for info in archive.infolist():
        if info.is_dir():
            continue

        # Use only filename, strip directory path (security: prevent path traversal)
        filename = Path(info.filename).name
        if not filename:
            continue

        # Security: reject filenames with null bytes or path separators
        # Check both / and \ since archives may be created on different OSes
        if "\x00" in filename or "/" in filename or "\\" in filename:
            logger.warning(f"Skipping suspicious filename in archive: {info.filename!r}")
            continue

        # Extract to output_dir with flat structure
        target_path = output_dir / filename

        # Security: verify resolved path stays within output directory (defense-in-depth)
        try:
            target_path.resolve().relative_to(output_dir.resolve())
        except ValueError:
            logger.warning(f"Path traversal attempt blocked: {info.filename!r}")
            continue

        with archive.open(info) as src:
            data = src.read()
        final_path = atomic_write(target_path, data)
        extracted_files.append(final_path)
        logger.debug(f"Extracted: {filename}")

    return extracted_files


def _extract_zip(archive_path: Path, output_dir: Path) -> Tuple[List[Path], List[str]]:
    """Extract files from a ZIP archive."""
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Check for password protection
            for info in zf.infolist():
                if info.flag_bits & 0x1:  # Encrypted flag
                    raise PasswordProtectedError("ZIP archive is password protected")

            # Test archive integrity
            bad_file = zf.testzip()
            if bad_file:
                raise CorruptedArchiveError(f"Corrupted file in archive: {bad_file}")

            return _extract_files_from_archive(zf, output_dir), []

    except zipfile.BadZipFile as e:
        raise CorruptedArchiveError(f"Invalid or corrupted ZIP: {e}")
    except PermissionError as e:
        raise ArchiveExtractionError(f"Permission denied: {e}")


def _extract_rar(archive_path: Path, output_dir: Path) -> Tuple[List[Path], List[str]]:
    """Extract files from a RAR archive."""
    if not RAR_AVAILABLE:
        raise ArchiveExtractionError("RAR extraction not available - rarfile library not installed")

    try:
        with rarfile.RarFile(archive_path, "r") as rf:
            # Check for password protection
            if rf.needs_password():
                raise PasswordProtectedError("RAR archive is password protected")

            # Test archive integrity
            rf.testrar()

            return _extract_files_from_archive(rf, output_dir), []

    except rarfile.BadRarFile as e:
        raise CorruptedArchiveError(f"Invalid or corrupted RAR: {e}")
    except rarfile.RarCannotExec:
        raise ArchiveExtractionError("unrar binary not found - install unrar package")
    except PermissionError as e:
        raise ArchiveExtractionError(f"Permission denied: {e}")



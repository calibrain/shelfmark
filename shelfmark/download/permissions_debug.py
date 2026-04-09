"""Permission/ownership diagnostics for filesystem operations.

This module centralizes best-effort debug logging used by download post-processing
and atomic filesystem operations.

It is intentionally defensive: failures collecting context should never mask the
original error.
"""

from __future__ import annotations

import os
from pathlib import Path

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)


def _log_path_permissions(probe: Path, label: str) -> None:
    """Best-effort logging for one path probe."""
    try:
        st = _run_io(probe.stat)
        logger.debug(
            "Path permissions (%s): path=%s mode=%s owner=%s(%d) group=%s(%d) exists=%s dir=%s",
            label,
            probe,
            oct(st.st_mode & 0o777),
            _format_uid(st.st_uid),
            st.st_uid,
            _format_gid(st.st_gid),
            st.st_gid,
            _run_io(probe.exists),
            _run_io(probe.is_dir),
        )
    except Exception as stat_error:  # noqa: BLE001
        logger.debug(
            "Path permissions (%s): stat failed for %s: %s", label, probe, stat_error
        )


def _run_io(func, *args, **kwargs):
    """Best-effort offload for potentially blocking filesystem calls.

    Keep this module import-cycle safe: `shelfmark.download.fs` imports this module,
    so we only import `run_blocking_io` lazily at call-time.
    """
    try:
        from shelfmark.download.fs import run_blocking_io as _run_blocking_io  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return func(*args, **kwargs)

    try:
        return _run_blocking_io(func, *args, **kwargs)
    except Exception:  # noqa: BLE001
        # Fall back to direct call if threadpool offload is unavailable.
        return func(*args, **kwargs)


def _format_uid(uid: int) -> str:
    try:
        import pwd  # noqa: PLC0415

        return pwd.getpwuid(uid).pw_name
    except Exception:  # noqa: BLE001
        return str(uid)


def _format_gid(gid: int) -> str:
    try:
        import grp  # noqa: PLC0415

        return grp.getgrgid(gid).gr_name
    except Exception:  # noqa: BLE001
        return str(gid)


def log_path_permission_context(label: str, path: Path) -> None:
    """Log useful permission/ownership context for a path.

    Only call this from failure paths.
    """
    try:
        euid = os.geteuid() if hasattr(os, "geteuid") else None
        egid = os.getegid() if hasattr(os, "getegid") else None
        groups = os.getgroups() if hasattr(os, "getgroups") else []

        if euid is not None and egid is not None:
            logger.debug(
                "Permission context (%s): euid=%s(%d) egid=%s(%d) groups=%s",
                label,
                _format_uid(euid),
                euid,
                _format_gid(egid),
                egid,
                [f"{_format_gid(g)}({g})" for g in groups],
            )

        for probe in [path, path.parent]:
            try:
                resolved = _run_io(probe.resolve)
            except Exception:  # noqa: BLE001
                resolved = probe

            try:
                st = _run_io(probe.stat)
                logger.debug(
                    "Path permissions (%s): path=%s resolved=%s mode=%s owner=%s(%d) group=%s(%d) dir=%s symlink=%s",  # noqa: E501
                    probe,
                    resolved,
                    oct(st.st_mode & 0o777),
                    _format_uid(st.st_uid),
                    st.st_uid,
                    _format_gid(st.st_gid),
                    st.st_gid,
                    _run_io(probe.is_dir),
                    _run_io(probe.is_symlink),
                )
            except Exception as stat_error:  # noqa: BLE001
                logger.debug(
                    "Path permissions (%s): stat failed for %s: %s",
                    label,
                    probe,
                    stat_error,
                )
    except Exception as context_error:  # noqa: BLE001
        logger.debug(
            "Permission context (%s): failed to collect: %s", label, context_error
        )


def log_transfer_permission_context(
    label: str, source: Path, dest: Path, error: Exception
) -> None:
    """Log useful permission/ownership context when a file transfer fails."""
    try:
        euid = os.geteuid() if hasattr(os, "geteuid") else None
        egid = os.getegid() if hasattr(os, "getegid") else None
        groups = os.getgroups() if hasattr(os, "getgroups") else []

        if euid is not None and egid is not None:
            logger.debug(
                "Permission context (%s): euid=%s(%d) egid=%s(%d) groups=%s error=%s",
                label,
                _format_uid(euid),
                euid,
                _format_gid(egid),
                egid,
                [f"{_format_gid(g)}({g})" for g in groups],
                error,
            )

        for probe in [source, dest, dest.parent]:
            _log_path_permissions(probe, label)
    except Exception as context_error:  # noqa: BLE001
        logger.debug(
            "Permission context (%s): failed to collect: %s", label, context_error
        )

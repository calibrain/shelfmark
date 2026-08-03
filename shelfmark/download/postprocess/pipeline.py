"""Post-download processing pipeline.

This module is the public API surface for post-download processing.

Implementation lives in submodules in this package:

- `types`: dataclasses used across the pipeline
- `workspace`: managed workspace + cleanup rules
- `scan`: directory scanning + archive extraction
- `transfer`: hardlink/copy/move + naming/organization
- `prepare`: staging plan + prepared file selection
- `steps`: lightweight plan logging helpers

Keeping this file as a facade avoids churn in call sites while letting the
implementation stay modular.
"""

from __future__ import annotations

from .custom_script import (
    CustomScriptContext,
    CustomScriptExecution,
    CustomScriptTransferSummary,
    maybe_run_custom_script,
    prepare_custom_script_execution,
    resolve_custom_script_target,
    run_custom_script,
)
from .destination import validate_destination
from .scan import (
    collect_directory_files,
    collect_staged_files,
    extract_archive_files,
    get_supported_formats,
    scan_directory_tree,
)
from .transfer import (
    is_torrent_source,
    should_hardlink,
)
from .workspace import (
    safe_cleanup_path,
)

__all__ = [
    "CustomScriptContext",
    "CustomScriptExecution",
    "CustomScriptTransferSummary",
    "collect_directory_files",
    "collect_staged_files",
    "extract_archive_files",
    "get_supported_formats",
    "is_torrent_source",
    "maybe_run_custom_script",
    "prepare_custom_script_execution",
    "resolve_custom_script_target",
    "run_custom_script",
    "safe_cleanup_path",
    "scan_directory_tree",
    "should_hardlink",
    "validate_destination",
]

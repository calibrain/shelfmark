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
    CustomScriptExecution,
    CustomScriptContext,
    CustomScriptTransferSummary,
    maybe_run_custom_script,
    prepare_custom_script_execution,
    resolve_custom_script_target,
    run_custom_script,
)
from .destination import get_final_destination, validate_destination
from .prepare import build_output_plan, prepare_output_files
from .scan import (
    collect_directory_files,
    collect_staged_files,
    extract_archive_files,
    get_supported_formats,
    scan_directory_tree,
)
from .steps import log_plan_steps, record_step
from .transfer import (
    build_metadata_dict,
    is_torrent_source,
    process_directory,
    resolve_hardlink_source,
    should_hardlink,
    transfer_book_files,
    transfer_directory_to_library,
    transfer_file_to_library,
)
from .types import OutputPlan, PlanStep, PreparedFiles, TransferPlan
from .workspace import (
    cleanup_output_staging,
    is_managed_workspace_path,
    is_within_tmp_dir,
    safe_cleanup_path,
)

__all__ = [
    "OutputPlan",
    "PlanStep",
    "PreparedFiles",
    "TransferPlan",
    "CustomScriptExecution",
    "CustomScriptContext",
    "CustomScriptTransferSummary",
    "build_metadata_dict",
    "build_output_plan",
    "cleanup_output_staging",
    "collect_directory_files",
    "collect_staged_files",
    "extract_archive_files",
    "get_final_destination",
    "get_supported_formats",
    "is_managed_workspace_path",
    "is_torrent_source",
    "is_within_tmp_dir",
    "log_plan_steps",
    "maybe_run_custom_script",
    "prepare_output_files",
    "prepare_custom_script_execution",
    "process_directory",
    "record_step",
    "resolve_hardlink_source",
    "resolve_custom_script_target",
    "safe_cleanup_path",
    "scan_directory_tree",
    "should_hardlink",
    "transfer_book_files",
    "transfer_directory_to_library",
    "transfer_file_to_library",
    "validate_destination",
    "run_custom_script",
]

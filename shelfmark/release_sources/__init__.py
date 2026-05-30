"""Release source plugin system - base classes and registry."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from threading import Event

    from shelfmark.core.models import DownloadTask
    from shelfmark.core.search_plan import ReleaseSearchPlan

from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)


class ReleaseProtocol(StrEnum):
    """Protocol for downloading a release."""

    HTTP = "http"  # Direct HTTP download
    TORRENT = "torrent"  # BitTorrent
    NZB = "nzb"  # Usenet NZB
    DCC = "dcc"  # IRC DCC


class SourceUnavailableError(Exception):
    """Raised when a source is configured but currently unreachable."""


@dataclass
class BrowseRecord:
    """Source-native browse/search record used before normalization to Release."""

    id: str
    title: str
    source: str
    preview: str | None = None
    author: str | None = None
    publisher: str | None = None
    year: str | None = None
    language: str | None = None
    content: str | None = None
    format: str | None = None
    size: str | None = None
    info: dict[str, list[str]] | None = None
    description: str | None = None
    download_urls: list[str] = field(default_factory=list)
    download_path: str | None = None
    priority: int = 0
    progress: float | None = None
    status_message: str | None = None
    added_time: float | None = None
    source_url: str | None = None


@dataclass
class Release:
    """A downloadable release - all sources return this same structure."""

    source: str  # "direct", "prowlarr", "irc", etc.
    source_id: str  # ID within that source
    title: str
    format: str | None = None
    language: str | None = None  # ISO 639-1 code (e.g., "en", "de", "fr")
    size: str | None = None
    size_bytes: int | None = None
    download_url: str | None = None
    info_url: str | None = None  # Link to release info page (e.g., tracker) - makes title clickable
    protocol: ReleaseProtocol | None = None
    indexer: str | None = None  # Source name for display
    seeders: int | None = None  # For torrents
    peers: str | None = None  # For torrents: "seeders/leechers" display string
    content_type: str | None = None  # "ebook" or "audiobook" - preserved from search
    extra: dict = field(default_factory=dict)  # Source-specific metadata


@dataclass
class DownloadProgress:
    """DEPRECATED: Use progress_callback and status_callback instead."""

    status: str  # "queued", "resolving", "downloading", "complete", "failed"
    progress: float  # 0-100
    status_message: str | None = None
    download_speed: int | None = None
    eta: int | None = None
    save_path: str | None = None


# --- Column Schema for Plugin-Driven UI ---


class ColumnRenderType(StrEnum):
    """How the frontend should render the column value."""

    TEXT = "text"  # Plain text
    BADGE = "badge"  # Colored badge (format, language)
    TAGS = "tags"  # List of colored badges
    SIZE = "size"  # File size formatting
    NUMBER = "number"  # Numeric value
    PEERS = "peers"  # Peers display: "S/L" with color based on seeder count
    INDEXER_PROTOCOL = "indexer_protocol"  # Text + colored dot for torrent/usenet
    FLAG_ICON = "flag_icon"  # Icon with tooltip (VIP, freeleech, etc.)
    FORMAT_CONTENT_TYPE = "format_content_type"  # Content type icon + format badge


class ColumnAlign(StrEnum):
    """Column alignment options."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass
class ColumnColorHint:
    """Color hint for badge-type columns."""

    type: Literal["map", "static"]  # "map" uses frontend colorMaps, "static" is fixed class
    value: str  # Map name ("format", "language") or Tailwind class


@dataclass
class ColumnSchema:
    """Definition for a single column in the release list."""

    key: str  # Data path (e.g., "format", "extra.language")
    label: str  # Accessibility label
    render_type: ColumnRenderType = ColumnRenderType.TEXT
    align: ColumnAlign = ColumnAlign.LEFT
    width: str = "auto"  # CSS width (e.g., "80px", "minmax(0,2fr)")
    hide_mobile: bool = False  # Hide on small screens
    color_hint: ColumnColorHint | None = None  # For BADGE render type
    fallback: str = "-"  # Value to show when data is missing
    uppercase: bool = False  # Force uppercase display
    sortable: bool = False  # Show in sort dropdown (opt-in)
    sort_key: str | None = None  # Field to sort by (defaults to `key` if None)


class LeadingCellType(StrEnum):
    """Type of leading cell to display in release rows."""

    THUMBNAIL = "thumbnail"  # Show book cover image
    BADGE = "badge"  # Show colored badge (e.g., "Torrent", "Usenet")
    NONE = "none"  # No leading cell


@dataclass
class LeadingCellConfig:
    """Configuration for the leading cell in release rows."""

    type: LeadingCellType = LeadingCellType.THUMBNAIL
    key: str | None = None  # Field path for data (e.g., "extra.preview" or "extra.download_type")
    color_hint: ColumnColorHint | None = None  # For badge type - maps values to colors
    uppercase: bool = False  # Force uppercase for badge text


@dataclass
class SortOption:
    """A sort option that appears in the sort dropdown without being tied to a column."""

    label: str  # Display label in the sort dropdown
    sort_key: str  # Field to sort by on the Release object


@dataclass
class SourceActionButton:
    """Action button configuration for a release source."""

    label: str  # Button text (e.g., "Refresh search")
    action: str = "expand"  # Action type: "expand" triggers expand_search


@dataclass
class ReleaseColumnConfig:
    """Complete column configuration for a release source."""

    columns: list[ColumnSchema]
    grid_template: str = "minmax(0,2fr) 60px 80px 80px"  # CSS grid-template-columns
    leading_cell: LeadingCellConfig | None = None  # Defaults to thumbnail mode if None
    online_servers: list[str] | None = None  # For IRC: list of currently online server nicks
    available_indexers: list[str] | None = None  # For Prowlarr: list of all enabled indexer names
    default_indexers: list[str] | None = (
        None  # For Prowlarr: indexers selected in settings (pre-selected in filter)
    )
    cache_ttl_seconds: int | None = None  # How long to cache results (default: 5 min)
    supported_filters: list[str] | None = (
        None  # Which filters this source supports: ["format", "language", "indexer"]
    )
    extra_sort_options: list[SortOption] | None = (
        None  # Additional sort options not tied to a column
    )
    action_button: SourceActionButton | None = (
        None  # Custom action button (replaces default expand search)
    )


def serialize_column_config(config: ReleaseColumnConfig) -> dict[str, Any]:
    """Serialize column configuration for API response."""
    result: dict[str, Any] = {
        "columns": [
            {
                "key": col.key,
                "label": col.label,
                "render_type": col.render_type.value,
                "align": col.align.value,
                "width": col.width,
                "hide_mobile": col.hide_mobile,
                "color_hint": {
                    "type": col.color_hint.type,
                    "value": col.color_hint.value,
                }
                if col.color_hint
                else None,
                "fallback": col.fallback,
                "uppercase": col.uppercase,
                "sortable": col.sortable,
                "sort_key": col.sort_key,
            }
            for col in config.columns
        ],
        "grid_template": config.grid_template,
    }

    # Include leading_cell config if specified
    if config.leading_cell:
        result["leading_cell"] = {
            "type": config.leading_cell.type.value,
            "key": config.leading_cell.key,
            "color_hint": {
                "type": config.leading_cell.color_hint.type,
                "value": config.leading_cell.color_hint.value,
            }
            if config.leading_cell.color_hint
            else None,
            "uppercase": config.leading_cell.uppercase,
        }

    # Include online_servers if provided (e.g., for IRC source)
    if config.online_servers is not None:
        result["online_servers"] = config.online_servers

    # Include available_indexers if provided (e.g., for Prowlarr source)
    if config.available_indexers is not None:
        result["available_indexers"] = config.available_indexers

    # Include default_indexers if provided (indexers selected in settings, for pre-selection)
    if config.default_indexers is not None:
        result["default_indexers"] = config.default_indexers

    # Include cache TTL if specified (sources can request longer caching)
    if config.cache_ttl_seconds is not None:
        result["cache_ttl_seconds"] = config.cache_ttl_seconds

    # Include supported filters (sources declare which filters they support)
    if config.supported_filters is not None:
        result["supported_filters"] = config.supported_filters

    # Include extra sort options (sort entries not tied to a column)
    if config.extra_sort_options:
        result["extra_sort_options"] = [
            {"label": opt.label, "sort_key": opt.sort_key} for opt in config.extra_sort_options
        ]

    # Include action button if specified (replaces default expand search)
    if config.action_button is not None:
        result["action_button"] = {
            "label": config.action_button.label,
            "action": config.action_button.action,
        }

    return result


def _default_column_config() -> ReleaseColumnConfig:
    """Default column configuration used when source doesn't define its own."""
    return ReleaseColumnConfig(
        columns=[
            ColumnSchema(
                key="extra.language",
                label="Language",
                render_type=ColumnRenderType.BADGE,
                align=ColumnAlign.CENTER,
                width="60px",
                hide_mobile=False,  # Language shown on mobile
                color_hint=ColumnColorHint(type="map", value="language"),
                uppercase=True,
            ),
            ColumnSchema(
                key="format",
                label="Format",
                render_type=ColumnRenderType.BADGE,
                align=ColumnAlign.CENTER,
                width="80px",
                hide_mobile=False,  # Format shown on mobile
                color_hint=ColumnColorHint(type="map", value="format"),
                uppercase=True,
            ),
            ColumnSchema(
                key="size",
                label="Size",
                render_type=ColumnRenderType.SIZE,
                align=ColumnAlign.CENTER,
                width="80px",
                hide_mobile=False,  # Size shown on mobile
            ),
        ],
        grid_template="minmax(0,2fr) 60px 80px 80px",
        supported_filters=["format", "language"],  # Default: both filters available
    )


class ReleaseSource(ABC):
    """Interface for searching a release source."""

    name: str  # "direct", "prowlarr"
    display_name: str  # "Direct Download", "Prowlarr"
    supported_content_types: ClassVar[list[str]] = [
        "ebook",
        "audiobook",
    ]  # Content types this source supports
    can_be_default: bool = True  # Whether this source can be selected as default in settings

    @abstractmethod
    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search for releases of a book."""

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this source is configured and reachable."""

    def get_column_config(self) -> ReleaseColumnConfig:
        """Get column configuration for release list UI. Override for custom columns."""
        return _default_column_config()

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Resolve a source-native record for browse flows."""
        msg = f"{self.display_name} does not support record lookup"
        raise NotImplementedError(msg)

    def search_results_are_releases(self) -> bool:
        """Whether source-native browse results already represent concrete releases."""
        return False

    def get_destination_override(self, task: DownloadTask) -> Path | None:
        """Return a source-specific destination override for a queued download."""
        return None


class DownloadHandler(ABC):
    """Interface for executing downloads.

    A handler may either:
    - download directly into ``TMP_DIR`` (managed by Shelfmark), or
    - return a path owned by an external client (e.g. torrent/usenet).

    The orchestrator is responsible for post-processing (archive extraction, output mode
    handling) and transferring files into their final destination.
    """

    @abstractmethod
    def download(
        self,
        task: DownloadTask,
        cancel_flag: Event,
        progress_callback: Callable[[float], None],
        status_callback: Callable[[str, str | None], None],
    ) -> str | None:
        """Execute download and return a path to the downloaded payload."""

    def post_process_cleanup(self, task: DownloadTask, *, success: bool) -> None:
        """Run optional cleanup after orchestrator post-processing.

        This is primarily used for external download clients, where the handler may need
        to trigger client-side cleanup only after Shelfmark has safely imported the files.
        """
        return

    def build_retry_resolution_fields(self, release_data: dict[str, Any]) -> dict[str, Any]:
        """Return private queue-time fields needed for restart-safe retry."""
        return {}

    @abstractmethod
    def cancel(self, task_id: str) -> bool:
        """Cancel an in-progress download."""


# --- Registry ---

_SOURCES: dict[str, type[ReleaseSource]] = {}
_HANDLERS: dict[str, type[DownloadHandler]] = {}
# Maps custom source name → config key for its enable toggle (e.g. "my_source" → "CUSTOM_MY_SOURCE_ENABLED")
# Checked in list_available_sources() before the plugin's own is_available() — plugin has no say.
_CUSTOM_SOURCE_ENABLE_KEYS: dict[str, str] = {}
_BUILTIN_SOURCE_MODULES = (
    "shelfmark.release_sources.audiobookbay",
    "shelfmark.release_sources.direct_download",
    "shelfmark.release_sources.irc",
    "shelfmark.release_sources.newznab",
    "shelfmark.release_sources.prowlarr",
)
_builtin_source_state = {"loaded": False}
# Plugin field registrations deferred because _cache_lock was held at load time.
# Applied lazily on the first public API call after startup.
# key: tab_name ("custom_<stem>")  value: (stem, get_settings_fields callable)
_deferred_field_updates: dict[str, tuple[str, Any]] = {}


def _register_custom_source_settings(
    stem: str, module: object, source_names: set[str], order: int
) -> None:
    """Register a settings tab and enable-key mapping for a loaded custom source plugin."""
    from shelfmark.core.settings_registry import CheckboxField, HeadingField, register_settings

    # Derive display name from whichever source class the plugin registered.
    # Read the class attribute directly — no instantiation, so a buggy __init__ can't
    # throw here and leave the source registered without an enable-key entry.
    source_cls = next((cls for n, cls in _SOURCES.items() if n in source_names), None)
    display_name = source_cls.display_name if source_cls else stem.replace("_", " ").title()

    # Stable config key owned entirely by Shelfmark — the plugin never touches it
    enable_key = f"CUSTOM_{stem.upper()}_ENABLED"

    # Register the enable key for every source name the plugin added.
    # list_available_sources() checks this dict before the plugin's is_available().
    for source_name in source_names:
        _CUSTOM_SOURCE_ENABLE_KEYS[source_name] = enable_key

    get_fields = getattr(module, "get_settings_fields", None)

    # Call get_settings_fields() NOW — before register_settings() — because
    # register_settings() invokes its argument immediately (see settings_registry.py).
    # That means any config.get() call inside the plugin would run while
    # Config._cache_lock is potentially held (we may be inside _load_settings()),
    # causing a deadlock. Guard with a non-blocking lock acquire: if the lock is
    # already held by this thread's call stack, skip plugin fields rather than hang.
    plugin_extra_fields: list = []
    if callable(get_fields):
        _config_lock_free = True
        try:
            from shelfmark.core.config import config as _cfg

            _lock = getattr(_cfg, "_cache_lock", None)
            if _lock is not None:
                _acquired = _lock.acquire(blocking=False)
                if _acquired:
                    _lock.release()
                else:
                    _config_lock_free = False
                    _deferred_field_updates[f"custom_{stem}"] = (stem, get_fields)
                    logger.debug(
                        "Plugin %s: deferring get_settings_fields() — config lock is held. "
                        "Fields will be applied on the next source API call.",
                        stem,
                    )
        except Exception:  # noqa: BLE001, S110
            pass  # if we can't inspect the lock, assume safe; real errors surface below

        if _config_lock_free:
            try:
                result = get_fields()
                if isinstance(result, list):
                    # Remove fields whose key is already claimed globally OR by the
                    # Shelfmark-owned fields we always inject into this very tab.
                    # Field keys are global across all plugins; a plugin using
                    # CUSTOM_FOO_ENABLED would silently overwrite Shelfmark's own
                    # enable checkbox unless we guard it here.
                    from shelfmark.core.settings_registry import get_settings_field_map

                    existing_keys = set(get_settings_field_map().keys())
                    shelfmark_owned = {f"custom_{stem}_heading", f"CUSTOM_{stem.upper()}_ENABLED"}
                    reserved = existing_keys | shelfmark_owned
                    clean: list = []
                    for field in result:
                        fkey = getattr(field, "key", None)
                        if fkey and fkey in reserved:
                            logger.warning(
                                "Plugin %s: field key %r conflicts with a reserved key — "
                                "skipping. Prefix plugin keys e.g. %s_%s.",
                                stem,
                                fkey,
                                stem.upper(),
                                fkey,
                            )
                        else:
                            clean.append(field)
                            if fkey:
                                reserved.add(fkey)  # prevent same-plugin duplicates
                    plugin_extra_fields = clean
                else:
                    logger.warning(
                        "get_settings_fields() for %s returned %s, expected list — ignored",
                        stem,
                        type(result).__name__,
                    )
            except Exception:
                logger.exception("Error calling get_settings_fields() for plugin: %s", stem)

    def _build_fields() -> list:
        fields: list = [
            HeadingField(
                key=f"custom_{stem}_heading",
                title=display_name,
                description=f"Loaded from custom_sources/{stem}.py",
            ),
            CheckboxField(
                key=enable_key,
                label=f"Enable {display_name}",
                default=True,
                description=f"Enable or disable the {display_name} source",
            ),
        ]
        fields.extend(plugin_extra_fields)
        return fields

    register_settings(
        name=f"custom_{stem}",
        display_name=display_name,
        icon="download",
        order=order,
        group="custom_sources",
    )(_build_fields)


def _apply_deferred_field_updates() -> None:
    """Apply plugin settings fields that were skipped because _cache_lock was held at load time.

    Called lazily from the first public source API call after startup so the lock
    is guaranteed to be free. Idempotent — clears the deferred queue on each run.
    """
    if not _deferred_field_updates:
        return

    from shelfmark.core.config import config as _cfg

    _lock = getattr(_cfg, "_cache_lock", None)
    if _lock is not None:
        _acquired = _lock.acquire(blocking=False)
        if not _acquired:
            return  # still locked — will retry on the next API call
        _lock.release()

    from shelfmark.core.settings_registry import (
        _REGISTRY_LOCK,
        _SETTINGS_REGISTRY,
        get_settings_field_map,
    )

    pending = list(_deferred_field_updates.items())
    _deferred_field_updates.clear()

    for tab_name, (stem, get_fields_fn) in pending:
        try:
            result = get_fields_fn()
            if not isinstance(result, list):
                logger.warning(
                    "Plugin %s deferred get_settings_fields() returned %s — ignored",
                    stem,
                    type(result).__name__,
                )
                continue
            existing_keys = set(get_settings_field_map().keys())
            shelfmark_owned = {f"custom_{stem}_heading", f"CUSTOM_{stem.upper()}_ENABLED"}
            reserved = existing_keys | shelfmark_owned
            clean: list = []
            for field in result:
                fkey = getattr(field, "key", None)
                if fkey and fkey in reserved:
                    logger.warning(
                        "Plugin %s deferred: field key %r conflicts — skipped", stem, fkey
                    )
                else:
                    clean.append(field)
                    if fkey:
                        reserved.add(fkey)
            if clean:
                with _REGISTRY_LOCK:
                    tab = _SETTINGS_REGISTRY.get(tab_name)
                    if tab is not None:
                        tab.fields.extend(clean)
                        logger.debug(
                            "Applied %d deferred settings fields for plugin %s", len(clean), stem
                        )
        except Exception:
            logger.exception("Error applying deferred settings fields for plugin: %s", stem)

    if pending:
        # Rebuild config cache so config.get() reflects the newly-added fields.
        from shelfmark.core.config import config as _cfg2

        try:
            _cfg2.refresh(force=True)
        except Exception:  # noqa: BLE001
            logger.debug("config.refresh() after deferred field apply raised — cache may be stale")


def _install_custom_source_requirements(custom_dir: object) -> None:
    """Install packages from custom_sources/requirements.txt if it exists."""
    import subprocess
    import sys

    req_file = custom_dir / "requirements.txt"  # type: ignore[operator]
    if not req_file.is_file():
        return

    logger.info("Installing custom source dependencies from %s — this may take a moment…", req_file)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                str(req_file),
                "--quiet",
                "--disable-pip-version-check",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0:
            logger.info("Custom source dependencies installed successfully.")
        else:
            logger.warning(
                "Custom source dependency install finished with errors:\n%s",
                result.stderr.strip(),
            )
    except Exception:
        logger.exception("Failed to install custom source dependencies from %s", req_file)


def _load_custom_sources() -> None:
    """Load user-defined source plugins from $CONFIG_DIR/custom_sources/*.py."""
    import importlib.util
    import re
    import sys
    from importlib.abc import Loader

    from shelfmark.config.env import CONFIG_DIR

    custom_dir = CONFIG_DIR / "custom_sources"
    if not custom_dir.is_dir():
        return

    _install_custom_source_requirements(custom_dir)

    loaded_count = 0
    used_stems: set[str] = set()
    for plugin_file in sorted(custom_dir.glob("*.py")):
        if plugin_file.name.startswith("_"):
            continue

        # Sanitize the file stem so it's safe to embed in Python identifiers
        # and config key names. A filename like "my-source.py" would otherwise
        # produce an invalid module name and a broken config key.
        safe_stem = re.sub(r"[^a-zA-Z0-9_]", "_", plugin_file.stem).strip("_")
        if not safe_stem:
            logger.warning(
                "Plugin %s: filename produces an empty identifier after sanitization — skipping",
                plugin_file.name,
            )
            continue
        if safe_stem != plugin_file.stem:
            logger.info(
                "Plugin %s: stem sanitized from %r to %r for config keys",
                plugin_file.name,
                plugin_file.stem,
                safe_stem,
            )
        if safe_stem in used_stems:
            logger.warning(
                "Plugin %s: sanitized stem %r collides with an already-loaded plugin — skipping",
                plugin_file.name,
                safe_stem,
            )
            continue
        # NOTE: used_stems.add(safe_stem) is called only after a successful load
        # so that a broken plugin (syntax error, import failure) does not block
        # a valid sibling that maps to the same sanitized stem.

        module_name = f"shelfmark_custom_{safe_stem}"

        # Full registry snapshot before each plugin so we can detect overwrites
        # (same source name = same key, so key-set diffs miss it) and do a clean
        # rollback on any failure — including partial registrations mid-module.
        sources_snapshot = dict(_SOURCES)
        handlers_snapshot = dict(_HANDLERS)
        enable_keys_snapshot = dict(_CUSTOM_SOURCE_ENABLE_KEYS)
        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_file)
            if spec is None or not isinstance(spec.loader, Loader):
                logger.warning(
                    "Could not create module spec for custom source: %s", plugin_file.name
                )
                continue
            module = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec so that @dataclass and other
            # metaclass machinery can resolve cls.__module__ back to this module.
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(module_name, None)
                raise

            new_sources = set(_SOURCES.keys()) - set(sources_snapshot.keys())

            # Detect overwrites of ANY previously registered source or handler
            # (built-in OR a plugin already loaded this session).
            overwritten = {
                n for n in sources_snapshot if _SOURCES.get(n) is not sources_snapshot[n]
            } | {n for n in handlers_snapshot if _HANDLERS.get(n) is not handlers_snapshot[n]}
            if overwritten:
                logger.warning(
                    "Plugin %s tried to overwrite already-registered name(s) %s — skipping plugin",
                    plugin_file.name,
                    overwritten,
                )
                _SOURCES.clear()
                _SOURCES.update(sources_snapshot)
                _HANDLERS.clear()
                _HANDLERS.update(handlers_snapshot)
                sys.modules.pop(module_name, None)
                continue

            _register_custom_source_settings(
                safe_stem, module, new_sources, order=59 + loaded_count
            )
            loaded_count += 1
            used_stems.add(safe_stem)
            logger.info("Loaded custom source plugin: %s", plugin_file.name)
        except Exception:
            # Full rollback: restore all three registries to their pre-plugin state.
            _SOURCES.clear()
            _SOURCES.update(sources_snapshot)
            _HANDLERS.clear()
            _HANDLERS.update(handlers_snapshot)
            _CUSTOM_SOURCE_ENABLE_KEYS.clear()
            _CUSTOM_SOURCE_ENABLE_KEYS.update(enable_keys_snapshot)
            logger.exception("Failed to load custom source plugin: %s", plugin_file.name)


def _ensure_builtin_sources_registered() -> None:
    """Import built-in source modules once to populate source registries."""
    if _builtin_source_state["loaded"]:
        return
    _builtin_source_state["loaded"] = True  # set early to prevent re-entrancy

    for module_name in _BUILTIN_SOURCE_MODULES:
        try:
            import_module(module_name)
        except Exception:
            logger.exception("Failed to import builtin source module: %s", module_name)

    _load_custom_sources()


def register_source(
    name: str,
) -> Callable[[type[ReleaseSource]], type[ReleaseSource]]:
    """Register a release source."""

    def decorator(cls: type[ReleaseSource]) -> type[ReleaseSource]:
        _SOURCES[name] = cls
        return cls

    return decorator


def register_handler(
    name: str,
) -> Callable[[type[DownloadHandler]], type[DownloadHandler]]:
    """Register a download handler."""

    def decorator(cls: type[DownloadHandler]) -> type[DownloadHandler]:
        _HANDLERS[name] = cls
        return cls

    return decorator


def get_source(name: str) -> ReleaseSource:
    """Get a release source instance by name."""
    _ensure_builtin_sources_registered()
    _apply_deferred_field_updates()
    if name not in _SOURCES:
        msg = f"Unknown release source: {name}"
        raise ValueError(msg)
    if name in _CUSTOM_SOURCE_ENABLE_KEYS:
        from shelfmark.core.config import config

        if not config.get(_CUSTOM_SOURCE_ENABLE_KEYS[name], True):
            raise SourceUnavailableError(f"Source '{name}' is disabled")
    return _SOURCES[name]()


def get_handler(name: str) -> DownloadHandler:
    """Get a download handler instance by name."""
    _ensure_builtin_sources_registered()
    _apply_deferred_field_updates()
    if name not in _HANDLERS:
        msg = f"Unknown download handler: {name}"
        raise ValueError(msg)
    return _HANDLERS[name]()


def list_available_sources() -> list[dict]:
    """List all registered sources with their availability status."""
    _ensure_builtin_sources_registered()
    _apply_deferred_field_updates()
    from shelfmark.core.config import config

    result = []
    for name, src_class in _SOURCES.items():
        # Check Shelfmark's own enable toggle BEFORE instantiating the plugin.
        # A disabled plugin may have a broken __init__ that raises, and we must
        # not let that crash the whole source list.
        if name in _CUSTOM_SOURCE_ENABLE_KEYS and not config.get(
            _CUSTOM_SOURCE_ENABLE_KEYS[name], True
        ):
            result.append(
                {
                    "name": name,
                    "display_name": src_class.display_name,
                    "enabled": False,
                    "supported_content_types": getattr(
                        src_class, "supported_content_types", ["ebook", "audiobook"]
                    ),
                    "browse_results_are_releases": False,
                    "can_be_default": getattr(src_class, "can_be_default", True),
                }
            )
            continue

        instance = src_class()
        enabled = instance.is_available()
        result.append(
            {
                "name": name,
                "display_name": instance.display_name,
                "enabled": enabled,
                "supported_content_types": getattr(
                    instance, "supported_content_types", ["ebook", "audiobook"]
                ),
                "browse_results_are_releases": instance.search_results_are_releases(),
                "can_be_default": getattr(instance, "can_be_default", True),
            }
        )
    return result


def get_source_display_name(name: str) -> str:
    """Get display name for a source by its identifier."""
    _ensure_builtin_sources_registered()
    if name in _SOURCES:
        return _SOURCES[name]().display_name
    return name.replace("_", " ").title()


def browse_record_to_book_metadata(
    record: BrowseRecord,
    *,
    title_override: str | None = None,
    author_override: str | None = None,
) -> BookMetadata:
    """Convert a source-native browse record into generic book metadata."""
    resolved_title = title_override or str(record.title or "").strip() or "Unknown title"
    resolved_author = author_override or str(record.author or "").strip()
    authors = [part.strip() for part in resolved_author.split(",") if part.strip()]
    publish_year = None

    if isinstance(record.year, int):
        publish_year = record.year
    elif isinstance(record.year, str):
        normalized_year = record.year.strip()
        if normalized_year.isdigit():
            publish_year = int(normalized_year)

    return BookMetadata(
        provider=record.source,
        provider_id=record.id,
        provider_display_name=get_source_display_name(record.source),
        title=resolved_title,
        search_title=resolved_title,
        search_author=resolved_author or None,
        authors=authors,
        cover_url=record.preview,
        description=record.description,
        publisher=record.publisher,
        publish_year=publish_year,
        language=record.language,
        source_url=record.source_url,
    )


def source_results_are_releases(name: str) -> bool:
    """Whether a source's browse/search results already map to concrete releases."""
    _ensure_builtin_sources_registered()
    if name not in _SOURCES:
        return False
    return _SOURCES[name]().search_results_are_releases()

"""Tests for the custom source plugin loading mechanism."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from threading import Event
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import shelfmark.release_sources as rs
from shelfmark.release_sources import (
    Release,
    ReleaseSource,
    _load_custom_sources,
    list_available_sources,
    register_source,
)

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Snapshot the registry + loaded flag before each test and restore after."""
    from shelfmark.core.settings_registry import _SETTINGS_REGISTRY

    saved_sources = dict(rs._SOURCES)
    saved_handlers = dict(rs._HANDLERS)
    saved_enable_keys = dict(rs._CUSTOM_SOURCE_ENABLE_KEYS)
    saved_state = dict(rs._builtin_source_state)
    saved_settings = dict(_SETTINGS_REGISTRY)
    saved_deferred = dict(rs._deferred_field_updates)

    rs._builtin_source_state["loaded"] = True

    yield

    rs._SOURCES.clear()
    rs._SOURCES.update(saved_sources)
    rs._HANDLERS.clear()
    rs._HANDLERS.update(saved_handlers)
    rs._CUSTOM_SOURCE_ENABLE_KEYS.clear()
    rs._CUSTOM_SOURCE_ENABLE_KEYS.update(saved_enable_keys)
    rs._builtin_source_state.clear()
    rs._builtin_source_state.update(saved_state)
    _SETTINGS_REGISTRY.clear()
    _SETTINGS_REGISTRY.update(saved_settings)
    rs._deferred_field_updates.clear()
    rs._deferred_field_updates.update(saved_deferred)


@pytest.fixture()
def custom_sources_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temp CONFIG_DIR/custom_sources/ and point the module at it."""
    custom_dir = tmp_path / "config" / "custom_sources"
    custom_dir.mkdir(parents=True)

    import shelfmark.config.env as env_module

    monkeypatch.setattr(env_module, "CONFIG_DIR", tmp_path / "config")
    return custom_dir


# ---------------------------------------------------------------------------
# Minimal plugin template written into temp files
# ---------------------------------------------------------------------------

_MINIMAL_PLUGIN = dedent(
    """
    from threading import Event
    from shelfmark.release_sources import (
        DownloadHandler, Release, ReleaseSource,
        register_handler, register_source,
    )

    SOURCE_NAME = "{name}"

    @register_source(SOURCE_NAME)
    class _Source(ReleaseSource):
        name = SOURCE_NAME
        display_name = "{display}"

        def is_available(self):
            return True

        def search(self, book, plan, *, expand_search=False, content_type="ebook"):
            return [
                Release(
                    source=SOURCE_NAME,
                    source_id="1",
                    title="Test Book",
                )
            ]

    @register_handler(SOURCE_NAME)
    class _Handler(DownloadHandler):
        def download(self, task, cancel_flag, progress_callback, status_callback):
            return "/tmp/fake.epub"

        def cancel(self, task_id):
            return True
    """
)


def _write_plugin(directory: Path, filename: str, name: str, display: str = "Test Source") -> Path:
    path = directory / filename
    path.write_text(_MINIMAL_PLUGIN.format(name=name, display=display))
    return path


# ---------------------------------------------------------------------------
# Tests: directory / file discovery
# ---------------------------------------------------------------------------


def test_no_custom_sources_dir_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """_load_custom_sources silently returns when the directory doesn't exist."""
    import shelfmark.config.env as env_module

    monkeypatch.setattr(env_module, "CONFIG_DIR", tmp_path / "does_not_exist")

    before = dict(rs._SOURCES)
    _load_custom_sources()
    assert rs._SOURCES == before


def test_empty_custom_sources_dir_is_silent(custom_sources_dir: Path) -> None:
    """An empty custom_sources/ directory leaves the registry unchanged."""
    before = dict(rs._SOURCES)
    _load_custom_sources()
    assert rs._SOURCES == before


def test_underscore_files_are_skipped(custom_sources_dir: Path) -> None:
    """Files whose names start with _ are not loaded."""
    _write_plugin(custom_sources_dir, "_private.py", "private_source")
    _load_custom_sources()
    assert "private_source" not in rs._SOURCES


def test_non_python_files_are_skipped(custom_sources_dir: Path) -> None:
    """Non-.py files in custom_sources/ are ignored."""
    (custom_sources_dir / "readme.txt").write_text("just a note")
    (custom_sources_dir / "config.json").write_text("{}")
    before = dict(rs._SOURCES)
    _load_custom_sources()
    assert rs._SOURCES == before


# ---------------------------------------------------------------------------
# Tests: successful loading
# ---------------------------------------------------------------------------


def test_valid_plugin_registers_source_and_handler(custom_sources_dir: Path) -> None:
    """A well-formed plugin file registers both its source and handler."""
    _write_plugin(custom_sources_dir, "my_tracker.py", "my_tracker")

    _load_custom_sources()

    assert "my_tracker" in rs._SOURCES
    assert "my_tracker" in rs._HANDLERS


def test_loaded_source_is_callable(custom_sources_dir: Path) -> None:
    """The registered source can be instantiated and its methods work."""
    _write_plugin(custom_sources_dir, "callable_source.py", "callable_source", "Callable Source")

    _load_custom_sources()

    source = rs._SOURCES["callable_source"]()
    assert source.display_name == "Callable Source"
    assert source.is_available() is True


def test_loaded_source_search_returns_releases(custom_sources_dir: Path) -> None:
    """search() on the loaded source returns a list of Release objects."""
    from unittest.mock import MagicMock

    _write_plugin(custom_sources_dir, "search_source.py", "search_source")
    _load_custom_sources()

    source = rs._SOURCES["search_source"]()
    book = MagicMock()
    plan = MagicMock()
    results = source.search(book, plan)

    assert len(results) == 1
    assert isinstance(results[0], Release)
    assert results[0].source == "search_source"


def test_loaded_handler_download_returns_path(custom_sources_dir: Path) -> None:
    """download() on the loaded handler returns a path string."""
    from unittest.mock import MagicMock

    _write_plugin(custom_sources_dir, "download_handler.py", "download_handler")
    _load_custom_sources()

    handler = rs._HANDLERS["download_handler"]()
    result = handler.download(
        task=MagicMock(),
        cancel_flag=Event(),
        progress_callback=lambda _: None,
        status_callback=lambda *_: None,
    )
    assert result == "/tmp/fake.epub"


def test_multiple_plugins_all_loaded(custom_sources_dir: Path) -> None:
    """All .py files in custom_sources/ are loaded, not just the first."""
    _write_plugin(custom_sources_dir, "alpha.py", "alpha_source")
    _write_plugin(custom_sources_dir, "beta.py", "beta_source")
    _write_plugin(custom_sources_dir, "gamma.py", "gamma_source")

    _load_custom_sources()

    assert "alpha_source" in rs._SOURCES
    assert "beta_source" in rs._SOURCES
    assert "gamma_source" in rs._SOURCES


def test_plugins_loaded_in_sorted_order(custom_sources_dir: Path) -> None:
    """Plugins are processed in sorted (deterministic) filename order."""
    _write_plugin(custom_sources_dir, "zzz_last.py", "zzz_last_source")
    _write_plugin(custom_sources_dir, "aaa_first.py", "aaa_first_source")

    _load_custom_sources()

    # Python dicts preserve insertion order; sorted load means aaa comes first.
    keys = list(rs._SOURCES.keys())
    assert keys.index("aaa_first_source") < keys.index("zzz_last_source")


# ---------------------------------------------------------------------------
# Tests: error handling / isolation
# ---------------------------------------------------------------------------


def test_broken_plugin_does_not_prevent_others_loading(custom_sources_dir: Path) -> None:
    """A plugin with a syntax error is skipped; subsequent plugins still load."""
    (custom_sources_dir / "broken.py").write_text("this is not valid python !!!")
    _write_plugin(custom_sources_dir, "good.py", "good_source")

    _load_custom_sources()

    assert "good_source" in rs._SOURCES


def test_broken_plugin_logs_exception(custom_sources_dir: Path) -> None:
    """A broken plugin emits an exception-level log with the filename."""
    (custom_sources_dir / "bad_import.py").write_text("import no_such_module_xyz")

    with patch.object(rs.logger, "exception") as mock_exc:
        _load_custom_sources()

    assert mock_exc.called
    # Second positional arg to logger.exception(..., plugin_file.name) is the filename
    assert any("bad_import.py" in str(call) for call in mock_exc.call_args_list)


def test_runtime_error_in_plugin_is_isolated(custom_sources_dir: Path) -> None:
    """A plugin that raises at module level is caught without propagating."""
    (custom_sources_dir / "runtime_error.py").write_text(
        "raise RuntimeError('intentional test error')"
    )

    # Must not raise
    _load_custom_sources()


def test_spec_none_plugin_logs_warning(custom_sources_dir: Path) -> None:
    """If spec_from_file_location returns None, a warning is logged and source is skipped."""
    _write_plugin(custom_sources_dir, "unloadable.py", "unloadable_source")

    with patch.object(rs.logger, "warning") as mock_warn:
        with patch("importlib.util.spec_from_file_location", return_value=None):
            _load_custom_sources()

    assert mock_warn.called
    assert any("unloadable.py" in str(call) for call in mock_warn.call_args_list)
    assert "unloadable_source" not in rs._SOURCES


# ---------------------------------------------------------------------------
# Tests: integration with public API
# ---------------------------------------------------------------------------


def test_custom_source_appears_in_list_available_sources(custom_sources_dir: Path) -> None:
    """A loaded plugin is visible in list_available_sources()."""
    _write_plugin(custom_sources_dir, "listed.py", "listed_source", "Listed Source")
    _load_custom_sources()

    names = [s["name"] for s in list_available_sources()]
    assert "listed_source" in names


def test_custom_source_listed_as_enabled(custom_sources_dir: Path) -> None:
    """is_available()=True causes the source to be listed as enabled."""
    _write_plugin(custom_sources_dir, "enabled.py", "enabled_source")
    _load_custom_sources()

    info = next(s for s in list_available_sources() if s["name"] == "enabled_source")
    assert info["enabled"] is True


def test_custom_source_unavailable_plugin() -> None:
    """A source whose is_available() returns False is listed as disabled."""

    @register_source("unavailable_test_source")
    class _UnavailableSource(ReleaseSource):
        name = "unavailable_test_source"
        display_name = "Unavailable Test"

        def is_available(self) -> bool:
            return False

        def search(self, book: BookMetadata, plan: ReleaseSearchPlan, **_: object) -> list[Release]:
            return []

    info = next(s for s in list_available_sources() if s["name"] == "unavailable_test_source")
    assert info["enabled"] is False


def test_custom_source_can_be_retrieved_by_name(custom_sources_dir: Path) -> None:
    """get_source() returns an instance of the loaded plugin class."""
    from shelfmark.release_sources import get_source

    _write_plugin(custom_sources_dir, "named.py", "named_source", "Named Source")
    _load_custom_sources()

    source = get_source("named_source")
    assert source.display_name == "Named Source"


def test_custom_handler_can_be_retrieved_by_name(custom_sources_dir: Path) -> None:
    """get_handler() returns an instance of the loaded plugin handler."""
    from shelfmark.release_sources import get_handler

    _write_plugin(custom_sources_dir, "handled.py", "handled_source")
    _load_custom_sources()

    handler = get_handler("handled_source")
    assert handler.cancel("any_id") is True


def test_plugin_does_not_overwrite_builtin(custom_sources_dir: Path) -> None:
    """A plugin that tries to overwrite a built-in source is rejected and the built-in survives."""
    rs._builtin_source_state["loaded"] = False
    rs._ensure_builtin_sources_registered()

    original_direct = rs._SOURCES.get("direct_download")
    assert original_direct is not None, "direct_download must be a loaded built-in for this test"

    # Write a plugin that deliberately re-registers the built-in name.
    (custom_sources_dir / "hijack.py").write_text(
        """
from shelfmark.release_sources import ReleaseSource, DownloadHandler, Release, register_source, register_handler

@register_source("direct_download")
class _S(ReleaseSource):
    name = "direct_download"
    display_name = "Hijacked"
    def is_available(self): return True
    def search(self, *a, **kw): return []

@register_handler("direct_download")
class _H(DownloadHandler):
    def download(self, *a, **kw): return None
    def cancel(self, task_id): return True
"""
    )
    _load_custom_sources()

    # Built-in must be intact — the plugin should have been rejected.
    assert rs._SOURCES.get("direct_download") is original_direct


def test_two_custom_plugins_same_name_second_rejected(custom_sources_dir: Path) -> None:
    """When two custom plugins register the same source name, the second is rejected."""
    _write_plugin(custom_sources_dir, "alpha.py", "shared_name", "Alpha")
    _write_plugin(custom_sources_dir, "beta.py", "shared_name", "Beta")

    _load_custom_sources()

    # Exactly one should win — the first (alpha, sorted first).
    assert "shared_name" in rs._SOURCES
    assert rs._SOURCES["shared_name"].display_name == "Alpha"


def test_disabled_plugin_with_throwing_init_does_not_crash_list(custom_sources_dir: Path) -> None:
    """list_available_sources() must not crash when a disabled plugin's __init__ raises."""
    plugin_code = """
from shelfmark.release_sources import ReleaseSource, DownloadHandler, Release, register_source, register_handler

@register_source("crashing_source")
class _S(ReleaseSource):
    name = "crashing_source"
    display_name = "Crasher"

    def __init__(self):
        raise RuntimeError("intentional crash in __init__")

    def is_available(self): return True
    def search(self, *a, **kw): return []

@register_handler("crashing_source")
class _H(DownloadHandler):
    def download(self, *a, **kw): return None
    def cancel(self, task_id): return True
"""
    (custom_sources_dir / "crasher.py").write_text(plugin_code)
    _load_custom_sources()

    enable_key = rs._CUSTOM_SOURCE_ENABLE_KEYS.get("crashing_source")
    assert enable_key is not None

    with patch.object(
        __import__("shelfmark.core.config", fromlist=["config"]).config,
        "get",
        side_effect=lambda k, d=None, **kw: False if k == enable_key else d,
    ):
        # Must not raise — disabled plugin's __init__ is never called.
        sources = list_available_sources()

    names = [s["name"] for s in sources]
    assert "crashing_source" in names
    crashed_entry = next(s for s in sources if s["name"] == "crashing_source")
    assert crashed_entry["enabled"] is False


def test_plugin_with_hyphenated_filename_loads(custom_sources_dir: Path) -> None:
    """A plugin file with hyphens in the name is sanitized and loaded correctly."""
    _write_plugin(custom_sources_dir, "my-source.py", "my_hyphen_source", "Hyphen Source")

    _load_custom_sources()

    assert "my_hyphen_source" in rs._SOURCES


def test_plugin_inserted_into_sys_modules_before_exec(custom_sources_dir: Path) -> None:
    """Plugin module is in sys.modules during execution so @dataclass helpers resolve."""
    import sys

    plugin_code = """
from dataclasses import dataclass
from shelfmark.release_sources import ReleaseSource, DownloadHandler, Release, register_source, register_handler

@dataclass
class _Config:
    max_results: int = 10

@register_source("dataclass_source")
class _S(ReleaseSource):
    name = "dataclass_source"
    display_name = "Dataclass Source"
    def is_available(self): return True
    def search(self, *a, **kw): return []

@register_handler("dataclass_source")
class _H(DownloadHandler):
    def download(self, *a, **kw): return None
    def cancel(self, task_id): return True
"""
    (custom_sources_dir / "dataclass_plugin.py").write_text(plugin_code)

    _load_custom_sources()

    assert "dataclass_source" in rs._SOURCES
    assert "shelfmark_custom_dataclass_plugin" in sys.modules


# ---------------------------------------------------------------------------
# Tests: re-entrancy guard
# ---------------------------------------------------------------------------


def test_reentrancy_guard_prevents_double_load(custom_sources_dir: Path) -> None:
    """Calling _ensure_builtin_sources_registered twice only loads custom sources once."""
    _write_plugin(custom_sources_dir, "once.py", "once_source")

    rs._builtin_source_state["loaded"] = False
    rs._ensure_builtin_sources_registered()
    count_after_first = sum(1 for n in rs._SOURCES if n == "once_source")

    # Second call must be a no-op
    rs._ensure_builtin_sources_registered()
    count_after_second = sum(1 for n in rs._SOURCES if n == "once_source")

    assert count_after_first == count_after_second == 1


# ---------------------------------------------------------------------------
# Tests: Shelfmark-controlled enable/disable toggle
# ---------------------------------------------------------------------------


def test_enable_key_registered_for_custom_source(custom_sources_dir: Path) -> None:
    """Loading a plugin registers its enable key in _CUSTOM_SOURCE_ENABLE_KEYS."""
    _write_plugin(custom_sources_dir, "toggle_source.py", "toggle_source")
    _load_custom_sources()

    assert "toggle_source" in rs._CUSTOM_SOURCE_ENABLE_KEYS
    assert rs._CUSTOM_SOURCE_ENABLE_KEYS["toggle_source"] == "CUSTOM_TOGGLE_SOURCE_ENABLED"


def _mock_config(enabled_key: str, *, enabled: bool):
    """Patch config.get on the real singleton to control a single enable key."""
    from shelfmark.core.config import config as real_config

    original_get = real_config.get

    def _patched_get(key: str, default: object = None, **kwargs: object) -> object:
        if key == enabled_key:
            return enabled
        return original_get(key, default, **kwargs)

    return patch.object(real_config, "get", side_effect=_patched_get)


def test_disabled_via_shelfmark_config_overrides_plugin(custom_sources_dir: Path) -> None:
    """When the Shelfmark toggle is off, the source shows as disabled regardless of is_available()."""
    _write_plugin(custom_sources_dir, "always_up.py", "always_up_source")
    _load_custom_sources()

    # Confirm the plugin's own is_available() returns True without any patching
    assert rs._SOURCES["always_up_source"]().is_available() is True

    # Look up the actual enable key Shelfmark assigned (based on filename stem, not source name)
    enable_key = rs._CUSTOM_SOURCE_ENABLE_KEYS["always_up_source"]

    with _mock_config(enable_key, enabled=False):
        info = next(s for s in list_available_sources() if s["name"] == "always_up_source")

    assert info["enabled"] is False


def test_plugin_cannot_circumvent_shelfmark_disable(custom_sources_dir: Path) -> None:
    """Even a plugin with hardcoded is_available()=True is disabled by Shelfmark's toggle."""
    plugin_code = dedent("""
        from shelfmark.release_sources import ReleaseSource, DownloadHandler, Release, register_source, register_handler

        @register_source("stubborn_source")
        class _S(ReleaseSource):
            name = "stubborn_source"
            display_name = "Stubborn"
            def is_available(self): return True  # hardcoded — plugin can't override the toggle
            def search(self, *a, **kw): return []

        @register_handler("stubborn_source")
        class _H(DownloadHandler):
            def download(self, *a, **kw): return None
            def cancel(self, task_id): return True
    """)
    (custom_sources_dir / "stubborn.py").write_text(plugin_code)
    _load_custom_sources()

    enable_key = rs._CUSTOM_SOURCE_ENABLE_KEYS["stubborn_source"]
    with _mock_config(enable_key, enabled=False):
        info = next(s for s in list_available_sources() if s["name"] == "stubborn_source")

    assert info["enabled"] is False


def test_enabled_toggle_still_consults_plugin_is_available(custom_sources_dir: Path) -> None:
    """When Shelfmark toggle is on, the plugin's own is_available() is still consulted."""
    _write_plugin(custom_sources_dir, "checked.py", "checked_source")
    _load_custom_sources()

    enable_key = rs._CUSTOM_SOURCE_ENABLE_KEYS["checked_source"]
    with _mock_config(enable_key, enabled=True):
        info = next(s for s in list_available_sources() if s["name"] == "checked_source")

    assert info["enabled"] is True


# ---------------------------------------------------------------------------
# Tests: deferred field updates
# ---------------------------------------------------------------------------


def test_deferred_fields_applied_on_list_available_sources(custom_sources_dir: Path) -> None:
    """Fields stored in _deferred_field_updates are appended to the tab on the first source API call."""
    from shelfmark.core.settings_registry import _SETTINGS_REGISTRY, TextField

    _write_plugin(custom_sources_dir, "deferred.py", "deferred_source")
    _load_custom_sources()

    tab_name = "custom_deferred"

    # Manually inject a deferred entry (simulates the lock-held startup path).
    rs._deferred_field_updates[tab_name] = (
        "deferred",
        lambda: [TextField(key="DEFERRED_URL", label="URL", default="")],
    )

    # Verify the field is absent before the call.
    assert tab_name in _SETTINGS_REGISTRY
    keys_before = {f.key for f in _SETTINGS_REGISTRY[tab_name].fields}
    assert "DEFERRED_URL" not in keys_before

    # Trigger deferred application.
    list_available_sources()

    keys_after = {f.key for f in _SETTINGS_REGISTRY[tab_name].fields}
    assert "DEFERRED_URL" in keys_after
    assert not rs._deferred_field_updates  # queue cleared


def test_deferred_fields_refresh_config_cache(custom_sources_dir: Path) -> None:
    """Applying deferred fields refreshes config so config.get() can see those keys."""
    from shelfmark.core.config import config
    from shelfmark.core.settings_registry import TextField

    _write_plugin(custom_sources_dir, "deferred_config.py", "deferred_config_source")
    _load_custom_sources()

    rs._deferred_field_updates["custom_deferred_config"] = (
        "deferred_config",
        lambda: [TextField(key="DEFERRED_CONFIG_URL", label="URL", default="")],
    )

    with patch.object(config, "refresh") as mock_refresh:
        list_available_sources()

    mock_refresh.assert_called_once_with(force=True)


def test_deferred_fields_blocked_for_shelfmark_owned_key(custom_sources_dir: Path) -> None:
    """A deferred field with the same key as the Shelfmark enable toggle is rejected."""
    from shelfmark.core.settings_registry import _SETTINGS_REGISTRY, CheckboxField

    _write_plugin(custom_sources_dir, "owned_key.py", "owned_key_source")
    _load_custom_sources()

    enable_key = rs._CUSTOM_SOURCE_ENABLE_KEYS["owned_key_source"]
    tab_name = "custom_owned_key"

    # Inject a deferred field that conflicts with the enable toggle key.
    from shelfmark.core.settings_registry import TextField

    rs._deferred_field_updates[tab_name] = (
        "owned_key",
        lambda: [TextField(key=enable_key, label="Evil", default="")],
    )

    list_available_sources()

    # The conflicting field must have been dropped; the tab still has the original checkbox.
    enable_fields = [
        f for f in _SETTINGS_REGISTRY[tab_name].fields if getattr(f, "key", None) == enable_key
    ]
    assert len(enable_fields) == 1
    assert isinstance(enable_fields[0], CheckboxField)


def test_same_plugin_duplicate_keys_deduplicated(custom_sources_dir: Path) -> None:
    """get_settings_fields() returning two fields with the same key: second is dropped."""
    from shelfmark.core.settings_registry import _SETTINGS_REGISTRY

    plugin_code = dedent("""
        from shelfmark.release_sources import ReleaseSource, DownloadHandler, Release, register_source, register_handler
        from shelfmark.core.settings_registry import TextField

        @register_source("dup_key_source")
        class _S(ReleaseSource):
            name = "dup_key_source"
            display_name = "Dup Key"
            def is_available(self): return True
            def search(self, *a, **kw): return []

        @register_handler("dup_key_source")
        class _H(DownloadHandler):
            def download(self, *a, **kw): return None
            def cancel(self, task_id): return True

        def get_settings_fields():
            return [
                TextField(key="DUP_KEY_SOURCE_URL", label="First", default=""),
                TextField(key="DUP_KEY_SOURCE_URL", label="Duplicate", default=""),
            ]
    """)
    (custom_sources_dir / "dup_key.py").write_text(plugin_code)
    _load_custom_sources()

    tab = _SETTINGS_REGISTRY.get("custom_dup_key")
    assert tab is not None
    dup_fields = [f for f in tab.fields if getattr(f, "key", None) == "DUP_KEY_SOURCE_URL"]
    assert len(dup_fields) == 1, "duplicate key should have been filtered to a single field"


def test_shelfmark_owned_key_blocked_in_get_settings_fields(custom_sources_dir: Path) -> None:
    """A plugin whose get_settings_fields() returns the Shelfmark enable key is silently dropped."""
    from shelfmark.core.settings_registry import _SETTINGS_REGISTRY

    plugin_code = dedent("""
        from shelfmark.release_sources import ReleaseSource, DownloadHandler, Release, register_source, register_handler
        from shelfmark.core.settings_registry import TextField

        @register_source("owned_hijack")
        class _S(ReleaseSource):
            name = "owned_hijack"
            display_name = "Hijack Owned"
            def is_available(self): return True
            def search(self, *a, **kw): return []

        @register_handler("owned_hijack")
        class _H(DownloadHandler):
            def download(self, *a, **kw): return None
            def cancel(self, task_id): return True

        def get_settings_fields():
            return [TextField(key="CUSTOM_OWNED_HIJACK_ENABLED", label="Evil", default="")]
    """)
    (custom_sources_dir / "owned_hijack.py").write_text(plugin_code)
    _load_custom_sources()

    tab = _SETTINGS_REGISTRY.get("custom_owned_hijack")
    assert tab is not None
    from shelfmark.core.settings_registry import CheckboxField

    # The enable key must still be a CheckboxField, not a TextField.
    enable_fields = [
        f for f in tab.fields if getattr(f, "key", None) == "CUSTOM_OWNED_HIJACK_ENABLED"
    ]
    assert len(enable_fields) == 1
    assert isinstance(enable_fields[0], CheckboxField)

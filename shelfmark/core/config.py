"""Configuration singleton with ENV > config file > default resolution."""

import time
from importlib import import_module
from threading import Lock
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import ModuleType

# Import lazily to avoid circular imports
_registry_module = None
_env_module = None

_SETTINGS_REFRESH_COOLDOWN_SECONDS = 0.05


def _get_registry() -> ModuleType:
    """Lazy import of settings registry to avoid circular imports."""
    global _registry_module
    if _registry_module is None:
        from shelfmark.core import settings_registry

        _registry_module = settings_registry
    return _registry_module


def _get_env() -> ModuleType:
    """Lazy import of env module for fallback values."""
    global _env_module
    if _env_module is None:
        from shelfmark.config import env

        _env_module = env
    return _env_module


class Config:
    """Dynamic configuration singleton that provides live settings access.

    Settings are resolved with priority: ENV var > config file > default.
    Values are cached for performance and can be refreshed when settings change.
    """

    _instance: Self | None = None
    _lock = Lock()

    def __new__(cls) -> Self:
        """Return the shared configuration singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        instance = cls._instance
        if instance is None:
            msg = "Config singleton failed to initialize"
            raise RuntimeError(msg)
        return instance

    def __init__(self) -> None:
        """Initialize caches and backing stores for the singleton."""
        if self._initialized:
            return
        self._cache: dict[str, Any] = {}
        self._field_map: dict[str, tuple] = {}  # key -> (field, tab_name)
        self._cache_lock = Lock()
        self._initialized = True
        self._loaded = False
        self._last_refresh_time: float = 0.0

    def _ensure_loaded(self) -> None:
        """Ensure settings are loaded from the registry."""
        if self._loaded:
            return
        with self._cache_lock:
            if self._loaded:
                return
            self._load_settings()

    def _load_settings(self) -> None:
        """Load all settings from the registry."""
        # Ensure all settings modules are imported before loading
        # This handles cases where config is accessed before settings are registered
        try:
            import_module("shelfmark.config.notifications_settings")
            import_module("shelfmark.config.security")
            import_module("shelfmark.config.settings")
            import_module("shelfmark.config.users_settings")
            import_module("shelfmark.metadata_providers")
            import_module("shelfmark.release_sources")
        except ImportError:
            pass

        registry = _get_registry()

        # On first load, sync ENV values to config files
        # This ensures ENV values persist even if ENV vars are later removed
        if not hasattr(self, "_env_synced"):
            registry.sync_env_to_config()
            self._env_synced = True

        # Build field map from all registered tabs
        self._field_map.clear()
        self._cache.clear()

        for key, (field, tab_name) in registry.get_settings_field_map().items():
            self._field_map[key] = (field, tab_name)
            self._cache[key] = registry.get_setting_value(field, tab_name)

        self._loaded = True

    def refresh(self, *, force: bool = False) -> None:
        """Refresh all cached settings from config files.

        Call this after settings are updated via the UI to ensure
        the config singleton reflects the new values.

        Multiple calls within a short window (50 ms) are coalesced to
        avoid redundant disk I/O when several helpers each call refresh()
        during the same request.  Pass ``force=True`` to bypass the guard
        (e.g. after a settings write).
        """
        now = time.monotonic()
        if not force and (now - self._last_refresh_time) < _SETTINGS_REFRESH_COOLDOWN_SECONDS:
            return

        with self._cache_lock:
            self._loaded = False
            self._load_settings()
        self._last_refresh_time = time.monotonic()

    def get(self, key: str, default: object = None, user_id: int | None = None) -> object:
        """Get a setting value by key.

        Args:
            key: The setting key (e.g., 'MAX_RETRY')
            default: Default value if setting not found
            user_id: Optional DB user ID for per-user setting overrides

        Returns:
            The setting value, or default if not found

        """
        self._ensure_loaded()

        if key in self._field_map:
            field, _ = self._field_map[key]
            registry = _get_registry()

            # Deployment-level ENV values always win.
            if field.env_supported and registry.is_value_from_env(field):
                return self._cache.get(key, default)

        return self._cache.get(key, default)

    def __getattr__(self, name: str) -> object:
        """Allow attribute-style access to settings.

        Example: config.MAX_RETRY instead of config.get('MAX_RETRY')
        """
        # Avoid recursion for internal attributes
        if name.startswith("_"):
            msg = f"'{type(self).__name__}' object has no attribute '{name}'"
            raise AttributeError(msg)

        self._ensure_loaded()

        if name in self._cache:
            return self._cache[name]

        # Fallback to env module for settings not in registry
        # This ensures backward compatibility during migration
        env = _get_env()
        if hasattr(env, name):
            return getattr(env, name)

        msg = f"Setting '{name}' not found in config or env"
        raise AttributeError(msg)

    def is_from_env(self, key: str) -> bool:
        """Check if a setting's value comes from an environment variable.

        Args:
            key: The setting key

        Returns:
            True if the value is set via ENV var, False otherwise

        """
        self._ensure_loaded()

        if key not in self._field_map:
            return False

        field, _ = self._field_map[key]
        registry = _get_registry()
        return registry.is_value_from_env(field)

    def get_all(self) -> dict[str, Any]:
        """Get all cached settings as a dictionary.

        Returns:
            Dict of all setting keys to their current values

        """
        self._ensure_loaded()
        return dict(self._cache)


# Global singleton instance
config = Config()

"""Configuration singleton with ENV > config file > default resolution."""

import os
import sqlite3
from threading import Lock
from typing import Any, Dict, Optional

# Import lazily to avoid circular imports
_registry_module = None
_env_module = None
_user_db_module = None


def _get_registry():
    """Lazy import of settings registry to avoid circular imports."""
    global _registry_module
    if _registry_module is None:
        from shelfmark.core import settings_registry
        _registry_module = settings_registry
    return _registry_module


def _get_env():
    """Lazy import of env module for fallback values."""
    global _env_module
    if _env_module is None:
        from shelfmark.config import env
        _env_module = env
    return _env_module


def _get_user_db_module():
    """Lazy import of user DB module to avoid optional dependency loops."""
    global _user_db_module
    if _user_db_module is None:
        from shelfmark.core.user_db import UserDB
        _user_db_module = UserDB
    return _user_db_module


class Config:
    """
    Dynamic configuration singleton that provides live settings access.

    Settings are resolved with priority: ENV var > config file > default.
    Values are cached for performance and can be refreshed when settings change.
    """

    _instance: Optional['Config'] = None
    _lock = Lock()
    def __new__(cls) -> 'Config':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._cache: Dict[str, Any] = {}
        self._field_map: Dict[str, tuple] = {}  # key -> (field, tab_name)
        self._cache_lock = Lock()
        self._user_settings_cache: Dict[int, Dict[str, Any]] = {}
        self._user_settings_cache_lock = Lock()
        self._user_db = None
        self._user_db_load_attempted = False
        self._initialized = True
        self._loaded = False

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
            import shelfmark.config.settings  # noqa: F401 - main app settings
            import shelfmark.config.notifications_settings  # noqa: F401 - notifications settings
            import shelfmark.release_sources  # noqa: F401 - plugin settings
            import shelfmark.metadata_providers  # noqa: F401 - plugin settings
        except ImportError:
            pass

        registry = _get_registry()

        # On first load, sync ENV values to config files
        # This ensures ENV values persist even if ENV vars are later removed
        if not hasattr(self, '_env_synced'):
            registry.sync_env_to_config()
            self._env_synced = True

        # Build field map from all registered tabs
        self._field_map.clear()
        self._cache.clear()

        for tab in registry.get_all_settings_tabs():
            for field in tab.fields:
                # Skip action buttons and headings - they don't have values
                if isinstance(field, (registry.ActionButton, registry.HeadingField)):
                    continue

                key = field.key
                self._field_map[key] = (field, tab.name)

                # Load current value
                value = registry.get_setting_value(field, tab.name)
                self._cache[key] = value

        self._loaded = True

    def refresh(self) -> None:
        """
        Refresh all cached settings from config files.

        Call this after settings are updated via the UI to ensure
        the config singleton reflects the new values.
        """
        with self._cache_lock:
            self._loaded = False
            self._load_settings()
        with self._user_settings_cache_lock:
            self._user_settings_cache.clear()
        self._user_db = None
        self._user_db_load_attempted = False

    def _get_user_db(self):
        """Get or initialize a UserDB handle if available."""
        if self._user_db is not None:
            return self._user_db
        if self._user_db_load_attempted:
            return None

        self._user_db_load_attempted = True
        try:
            user_db_cls = _get_user_db_module()
            db_path = os.path.join(os.environ.get("CONFIG_DIR", "/config"), "users.db")
            user_db = user_db_cls(db_path)
            user_db.initialize()
            self._user_db = user_db
            return self._user_db
        except Exception:
            # Multi-user support is optional; fall back to global config when unavailable.
            return None

    def _get_user_settings(self, user_id: int) -> Dict[str, Any]:
        """Get cached per-user settings from user DB."""
        with self._user_settings_cache_lock:
            if user_id in self._user_settings_cache:
                return self._user_settings_cache[user_id]

        user_db = self._get_user_db()
        if user_db is None:
            return {}

        try:
            settings = user_db.get_user_settings(user_id)
        except (sqlite3.OperationalError, OSError, ValueError, TypeError):
            return {}

        if not isinstance(settings, dict):
            settings = {}

        with self._user_settings_cache_lock:
            self._user_settings_cache[user_id] = settings
        return settings

    def _get_user_override(self, user_id: int, key: str) -> Any:
        """Get a user override for a specific key."""
        user_settings = self._get_user_settings(user_id)
        return user_settings.get(key)

    def get(self, key: str, default: Any = None, user_id: Optional[int] = None) -> Any:
        """
        Get a setting value by key.

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

            # User overrides are only available for explicitly overridable fields.
            if user_id is not None and getattr(field, "user_overridable", False):
                user_value = self._get_user_override(user_id, key)
                if user_value is not None:
                    return user_value

        return self._cache.get(key, default)

    def __getattr__(self, name: str) -> Any:
        """
        Allow attribute-style access to settings.

        Example: config.MAX_RETRY instead of config.get('MAX_RETRY')
        """
        # Avoid recursion for internal attributes
        if name.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        self._ensure_loaded()

        if name in self._cache:
            return self._cache[name]

        # Fallback to env module for settings not in registry
        # This ensures backward compatibility during migration
        env = _get_env()
        if hasattr(env, name):
            return getattr(env, name)

        raise AttributeError(f"Setting '{name}' not found in config or env")

    def is_from_env(self, key: str) -> bool:
        """
        Check if a setting's value comes from an environment variable.

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

    def get_all(self) -> Dict[str, Any]:
        """
        Get all cached settings as a dictionary.

        Returns:
            Dict of all setting keys to their current values
        """
        self._ensure_loaded()
        return dict(self._cache)


# Global singleton instance
config = Config()

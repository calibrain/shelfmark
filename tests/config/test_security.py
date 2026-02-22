"""
Tests for security configuration and migration.

Tests the security settings registration, migration from old settings,
and current on-save guard behavior.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.user_db import UserDB


@pytest.fixture
def temp_config_dir():
    """Create a temporary config directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_dir = Path(tmpdir)
        security_dir = config_dir / "security"
        security_dir.mkdir(parents=True, exist_ok=True)
        yield security_dir


@pytest.fixture
def mock_logger():
    """Mock logger to capture log messages."""
    return MagicMock()


class TestSecurityMigration:
    """Tests for migrating legacy security settings."""

    def test_migrate_use_cwa_auth_true_syncs_legacy_admin(self, temp_config_dir, mock_logger, monkeypatch):
        """USE_CWA_AUTH=True migrates to cwa and keeps legacy creds synced to users DB."""
        config_root = temp_config_dir.parent
        monkeypatch.setenv("CONFIG_DIR", str(config_root))

        config_file = temp_config_dir / "config.json"
        legacy_config = {
            "USE_CWA_AUTH": True,
            "BUILTIN_USERNAME": "admin",
            "BUILTIN_PASSWORD_HASH": "hashed_password",
        }
        config_file.write_text(json.dumps(legacy_config, indent=2))

        with patch("shelfmark.config.security.load_config_file", return_value=legacy_config.copy()):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.config.security.logger", mock_logger):
                        from shelfmark.config.security import _migrate_security_settings

                        _migrate_security_settings()

        migrated = json.loads(config_file.read_text())
        assert migrated["AUTH_METHOD"] == "cwa"
        assert "USE_CWA_AUTH" not in migrated
        assert migrated["BUILTIN_USERNAME"] == "admin"
        assert migrated["BUILTIN_PASSWORD_HASH"] == "hashed_password"

        user_db = UserDB(str(config_root / "users.db"))
        user_db.initialize()
        user = user_db.get_user(username="admin")
        assert user is not None
        assert user["role"] == "admin"
        assert user["auth_source"] == "builtin"
        assert user["password_hash"] == "hashed_password"

    def test_migrate_use_cwa_auth_false_with_credentials(self, temp_config_dir, mock_logger, monkeypatch):
        """USE_CWA_AUTH=False with creds migrates to builtin and syncs users DB."""
        config_root = temp_config_dir.parent
        monkeypatch.setenv("CONFIG_DIR", str(config_root))

        config_file = temp_config_dir / "config.json"
        legacy_config = {
            "USE_CWA_AUTH": False,
            "BUILTIN_USERNAME": "admin",
            "BUILTIN_PASSWORD_HASH": "hashed_password",
        }
        config_file.write_text(json.dumps(legacy_config, indent=2))

        with patch("shelfmark.config.security.load_config_file", return_value=legacy_config.copy()):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.config.security.logger", mock_logger):
                        from shelfmark.config.security import _migrate_security_settings

                        _migrate_security_settings()

        migrated = json.loads(config_file.read_text())
        assert migrated["AUTH_METHOD"] == "builtin"
        assert "USE_CWA_AUTH" not in migrated
        assert migrated["BUILTIN_USERNAME"] == "admin"
        assert migrated["BUILTIN_PASSWORD_HASH"] == "hashed_password"

        user_db = UserDB(str(config_root / "users.db"))
        user_db.initialize()
        user = user_db.get_user(username="admin")
        assert user is not None
        assert user["role"] == "admin"

    def test_migrate_use_cwa_auth_false_without_credentials(self, temp_config_dir, mock_logger):
        """USE_CWA_AUTH=False without creds migrates to none."""
        config_file = temp_config_dir / "config.json"
        legacy_config = {"USE_CWA_AUTH": False}
        config_file.write_text(json.dumps(legacy_config, indent=2))

        with patch("shelfmark.config.security.load_config_file", return_value=legacy_config.copy()):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.config.security.logger", mock_logger):
                        from shelfmark.config.security import _migrate_security_settings

                        _migrate_security_settings()

        migrated = json.loads(config_file.read_text())
        assert migrated["AUTH_METHOD"] == "none"
        assert "USE_CWA_AUTH" not in migrated

    def test_migrate_restrict_settings_to_admin(self, temp_config_dir, mock_logger):
        """Legacy settings restriction should migrate to users tab global toggle."""
        config_file = temp_config_dir / "config.json"
        legacy_config = {
            "AUTH_METHOD": "cwa",
            "RESTRICT_SETTINGS_TO_ADMIN": True,
        }
        config_file.write_text(json.dumps(legacy_config, indent=2))

        def _load_config(tab_name: str):
            if tab_name == "security":
                return legacy_config.copy()
            if tab_name == "users":
                return {}
            return {}

        with patch("shelfmark.config.security.load_config_file", side_effect=_load_config):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.core.settings_registry.save_config_file") as mock_save_config:
                        with patch("shelfmark.config.security.logger", mock_logger):
                            from shelfmark.config.security import _migrate_security_settings

                            _migrate_security_settings()

        migrated = json.loads(config_file.read_text())
        assert "RESTRICT_SETTINGS_TO_ADMIN" not in migrated
        mock_save_config.assert_called_with("users", {"RESTRICT_SETTINGS_TO_ADMIN": True})

    def test_migrate_proxy_restriction_to_users_global(self, temp_config_dir, mock_logger):
        """Proxy-specific restriction should migrate to users.RESTRICT_SETTINGS_TO_ADMIN."""
        config_file = temp_config_dir / "config.json"
        legacy_config = {
            "AUTH_METHOD": "proxy",
            "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN": False,
        }
        config_file.write_text(json.dumps(legacy_config, indent=2))

        def _load_config(tab_name: str):
            if tab_name == "security":
                return legacy_config.copy()
            if tab_name == "users":
                return {}
            return {}

        with patch("shelfmark.config.security.load_config_file", side_effect=_load_config):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.core.settings_registry.save_config_file") as mock_save_config:
                        with patch("shelfmark.config.security.logger", mock_logger):
                            from shelfmark.config.security import _migrate_security_settings

                            _migrate_security_settings()

        migrated = json.loads(config_file.read_text())
        assert "PROXY_AUTH_RESTRICT_SETTINGS_TO_ADMIN" not in migrated
        mock_save_config.assert_called_with("users", {"RESTRICT_SETTINGS_TO_ADMIN": False})

    def test_migrate_preserves_existing_auth_method(self, temp_config_dir, mock_logger):
        """Existing AUTH_METHOD should not be overwritten."""
        config_file = temp_config_dir / "config.json"
        legacy_config = {
            "USE_CWA_AUTH": True,
            "AUTH_METHOD": "proxy",
        }
        config_file.write_text(json.dumps(legacy_config, indent=2))

        with patch("shelfmark.config.security.load_config_file", return_value=legacy_config.copy()):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.config.security.logger", mock_logger):
                        from shelfmark.config.security import _migrate_security_settings

                        _migrate_security_settings()

        migrated = json.loads(config_file.read_text())
        assert migrated["AUTH_METHOD"] == "proxy"
        assert "USE_CWA_AUTH" not in migrated

    def test_migrate_handles_missing_config_file(self, mock_logger):
        """Missing config file should be handled gracefully."""
        with patch("shelfmark.config.security.load_config_file", side_effect=FileNotFoundError()):
            with patch("shelfmark.config.security.logger", mock_logger):
                from shelfmark.config.security import _migrate_security_settings

                _migrate_security_settings()

        mock_logger.debug.assert_any_call("No existing security config file found - nothing to migrate")

    def test_migrate_no_changes_needed(self, temp_config_dir, mock_logger):
        """No-op migration should not rewrite config."""
        config_file = temp_config_dir / "config.json"
        modern_config = {
            "AUTH_METHOD": "builtin",
            "BUILTIN_USERNAME": "admin",
            "BUILTIN_PASSWORD_HASH": "hashed_password",
        }
        config_file.write_text(json.dumps(modern_config, indent=2))

        with patch("shelfmark.config.security.load_config_file", return_value=modern_config.copy()):
            with patch("shelfmark.core.settings_registry._get_config_file_path", return_value=str(config_file)):
                with patch("shelfmark.core.settings_registry._ensure_config_dir"):
                    with patch("shelfmark.config.security.logger", mock_logger):
                        from shelfmark.config.security import _migrate_security_settings

                        _migrate_security_settings()

        final_config = json.loads(config_file.read_text())
        assert final_config == modern_config


class TestSecuritySettings:
    """Tests for security settings registration."""

    def test_security_settings_without_cwa(self):
        """CWA option should be hidden when DB is unavailable."""
        with patch("shelfmark.config.env.CWA_DB_PATH", None):
            import importlib
            import shelfmark.config.security

            importlib.reload(shelfmark.config.security)
            from shelfmark.config.security import security_settings

            fields = security_settings()
            auth_method_field = next((f for f in fields if f.key == "AUTH_METHOD"), None)
            assert auth_method_field is not None

            option_values = [opt["value"] for opt in auth_method_field.options]
            assert "none" in option_values
            assert "builtin" in option_values
            assert "proxy" in option_values
            assert "cwa" not in option_values

    def test_security_settings_with_cwa(self):
        """CWA option should be shown when DB is mounted."""
        mock_path = MagicMock()
        mock_path.exists.return_value = True

        with patch("shelfmark.config.env.CWA_DB_PATH", mock_path):
            import importlib
            import shelfmark.config.security

            importlib.reload(shelfmark.config.security)
            from shelfmark.config.security import security_settings

            fields = security_settings()
            auth_method_field = next((f for f in fields if f.key == "AUTH_METHOD"), None)
            assert auth_method_field is not None

            option_values = [opt["value"] for opt in auth_method_field.options]
            assert "cwa" in option_values

    def test_builtin_credential_fields_hidden(self):
        """Builtin username/password fields should be removed from settings UI."""
        from shelfmark.config.security import security_settings

        fields = security_settings()
        field_keys = [f.key for f in fields]

        assert "BUILTIN_USERNAME" not in field_keys
        assert "BUILTIN_PASSWORD" not in field_keys
        assert "BUILTIN_PASSWORD_CONFIRM" not in field_keys

    def test_builtin_notice_field_removed(self):
        """Builtin guidance should be handled by the action button only."""
        from shelfmark.config.security import security_settings

        fields = security_settings()
        notice = next((f for f in fields if f.key == "builtin_auth_notice"), None)
        assert notice is None

    def test_builtin_option_label_is_local(self):
        """Builtin auth option should be labeled Local."""
        from shelfmark.config.security import security_settings

        fields = security_settings()
        auth_field = next((f for f in fields if f.key == "AUTH_METHOD"), None)
        builtin_option = next((opt for opt in auth_field.options if opt["value"] == "builtin"), None)
        assert builtin_option is not None
        assert builtin_option["label"] == "Local"

    def test_builtin_users_navigation_action_present(self):
        """Builtin mode should include an action button to open Users tab."""
        from shelfmark.config.security import security_settings

        fields = security_settings()
        action = next((f for f in fields if f.key == "open_users_tab"), None)
        assert action is not None
        assert action.label == "Go to Users"
        assert action.show_when == {"field": "AUTH_METHOD", "value": ["builtin", "oidc"]}


class TestSecurityOnSave:
    """Tests for current security on-save guard behavior."""

    def test_on_save_passthrough_for_non_oidc(self, tmp_path, monkeypatch):
        from shelfmark.config.security import _on_save_security

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        values = {"AUTH_METHOD": "builtin", "PROXY_AUTH_USER_HEADER": "X-Auth-User"}

        result = _on_save_security(values.copy())

        assert result["error"] is False
        assert result["values"] == values

    def test_on_save_blocks_oidc_without_local_admin(self, tmp_path, monkeypatch):
        from shelfmark.config.security import _on_save_security

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        UserDB(str(tmp_path / "users.db")).initialize()

        result = _on_save_security({"AUTH_METHOD": "oidc"})

        assert result["error"] is True
        assert "local admin" in result["message"].lower()

    def test_on_save_allows_oidc_with_local_password_admin(self, tmp_path, monkeypatch):
        from shelfmark.config.security import _on_save_security

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        user_db = UserDB(str(tmp_path / "users.db"))
        user_db.initialize()
        user_db.create_user(username="admin", password_hash="hash", role="admin")

        result = _on_save_security({"AUTH_METHOD": "oidc"})

        assert result["error"] is False

    def test_on_save_normalizes_oidc_discovery_url(self, tmp_path, monkeypatch):
        from shelfmark.config.security import _on_save_security

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        values = {"OIDC_DISCOVERY_URL": " 'auth.example.com/.well-known/openid-configuration/' "}

        result = _on_save_security(values)

        assert result["error"] is False
        assert (
            result["values"]["OIDC_DISCOVERY_URL"]
            == "https://auth.example.com/.well-known/openid-configuration"
        )

    def test_on_save_normalizes_proxy_logout_url(self, tmp_path, monkeypatch):
        from shelfmark.config.security import _on_save_security

        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        values = {"PROXY_AUTH_LOGOUT_URL": "auth.example.com/logout"}

        result = _on_save_security(values)

        assert result["error"] is False
        assert result["values"]["PROXY_AUTH_LOGOUT_URL"] == "https://auth.example.com/logout"

"""Tests for per-user BookLore library/path support."""

from shelfmark.download.outputs.booklore import build_booklore_config


class TestBuildBookloreConfigWithOverrides:
    """build_booklore_config should resolve per-user library/path via config."""

    BASE_SETTINGS = {
        "BOOKLORE_HOST": "http://booklore:6060",
        "BOOKLORE_USERNAME": "admin",
        "BOOKLORE_PASSWORD": "secret",
        "BOOKLORE_DESTINATION": "library",
        "BOOKLORE_LIBRARY_ID": 1,
        "BOOKLORE_PATH_ID": 10,
    }

    def test_global_config_without_user_context(self):
        config = build_booklore_config(self.BASE_SETTINGS)
        assert config.library_id == 1
        assert config.path_id == 10

    def test_override_library_and_path_with_user_context(self, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 2
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 20
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.library_id == 2
        assert config.path_id == 20

    def test_override_library_only(self, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 3
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.library_id == 3
        assert config.path_id == 10  # falls back to global

    def test_override_path_only(self, monkeypatch):
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 30
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.library_id == 1  # falls back to global
        assert config.path_id == 30

    def test_none_user_context_uses_global(self):
        config = build_booklore_config(self.BASE_SETTINGS, user_id=None)
        assert config.library_id == 1
        assert config.path_id == 10
        assert config.upload_to_bookdrop is False

    def test_auth_fields_remain_global(self, monkeypatch):
        """Only Booklore library/path should be resolved with user context."""
        def fake_get(key, default=None, user_id=None):
            if user_id == 7 and key == "BOOKLORE_LIBRARY_ID":
                return 5
            if user_id == 7 and key == "BOOKLORE_PATH_ID":
                return 15
            return default

        monkeypatch.setattr("shelfmark.download.outputs.booklore.core_config.config.get", fake_get)
        config = build_booklore_config(self.BASE_SETTINGS, user_id=7)
        assert config.base_url == "http://booklore:6060"
        assert config.username == "admin"
        assert config.library_id == 5

    def test_bookdrop_destination_ignores_library_and_path_values(self):
        settings = {
            "BOOKLORE_HOST": "http://booklore:6060",
            "BOOKLORE_USERNAME": "admin",
            "BOOKLORE_PASSWORD": "secret",
            "BOOKLORE_DESTINATION": "bookdrop",
        }

        config = build_booklore_config(settings)

        assert config.upload_to_bookdrop is True
        assert config.library_id == 0
        assert config.path_id == 0
        assert config.refresh_after_upload is False

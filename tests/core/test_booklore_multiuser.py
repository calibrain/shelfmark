"""Tests for per-user BookLore library/path support."""

from shelfmark.download.outputs.booklore import build_booklore_config


class TestBuildBookloreConfigWithOverrides:
    """build_booklore_config should accept per-user library/path overrides."""

    BASE_SETTINGS = {
        "BOOKLORE_HOST": "http://booklore:6060",
        "BOOKLORE_USERNAME": "admin",
        "BOOKLORE_PASSWORD": "secret",
        "BOOKLORE_LIBRARY_ID": 1,
        "BOOKLORE_PATH_ID": 10,
    }

    def test_global_config_no_overrides(self):
        config = build_booklore_config(self.BASE_SETTINGS)
        assert config.library_id == 1
        assert config.path_id == 10

    def test_override_library_and_path(self):
        overrides = {"booklore_library_id": 2, "booklore_path_id": 20}
        config = build_booklore_config(self.BASE_SETTINGS, user_overrides=overrides)
        assert config.library_id == 2
        assert config.path_id == 20

    def test_override_library_only(self):
        overrides = {"booklore_library_id": 3}
        config = build_booklore_config(self.BASE_SETTINGS, user_overrides=overrides)
        assert config.library_id == 3
        assert config.path_id == 10  # falls back to global

    def test_override_path_only(self):
        overrides = {"booklore_path_id": 30}
        config = build_booklore_config(self.BASE_SETTINGS, user_overrides=overrides)
        assert config.library_id == 1  # falls back to global
        assert config.path_id == 30

    def test_empty_overrides_uses_global(self):
        config = build_booklore_config(self.BASE_SETTINGS, user_overrides={})
        assert config.library_id == 1
        assert config.path_id == 10

    def test_none_overrides_uses_global(self):
        config = build_booklore_config(self.BASE_SETTINGS, user_overrides=None)
        assert config.library_id == 1
        assert config.path_id == 10

    def test_auth_fields_not_overridable(self):
        """Auth stays global - user overrides should not affect host/user/pass."""
        overrides = {
            "booklore_library_id": 5,
            "BOOKLORE_HOST": "http://evil:6060",
            "BOOKLORE_USERNAME": "hacker",
        }
        config = build_booklore_config(self.BASE_SETTINGS, user_overrides=overrides)
        assert config.base_url == "http://booklore:6060"
        assert config.username == "admin"
        assert config.library_id == 5


class TestOutputArgsForBooklore:
    """Download tasks should carry per-user booklore settings in output_args."""

    def test_output_args_with_booklore_settings(self):
        from shelfmark.core.models import DownloadTask

        task = DownloadTask(
            task_id="test-1",
            source="direct_download",
            title="Book1",
            output_mode="booklore",
            output_args={"booklore_library_id": 2, "booklore_path_id": 20},
            user_id=1,
        )
        assert task.output_args["booklore_library_id"] == 2
        assert task.output_args["booklore_path_id"] == 20

    def test_output_args_empty_for_global_booklore(self):
        from shelfmark.core.models import DownloadTask

        task = DownloadTask(
            task_id="test-2",
            source="direct_download",
            title="Book1",
            output_mode="booklore",
            output_args={},
        )
        assert task.output_args == {}

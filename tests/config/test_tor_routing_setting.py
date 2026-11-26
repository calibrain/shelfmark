"""The Tor Routing setting shows what is running, not what was once saved."""

import json

import pytest

import shelfmark.config.settings  # noqa: F401  (registers the settings tabs)
from shelfmark.config import env
from shelfmark.core import settings_registry as registry


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """A scratch config folder the registry reads and writes."""
    monkeypatch.setattr(registry, "_get_config_dir", lambda: tmp_path)
    return tmp_path


def _saved_network(config_dir) -> dict:
    """Return what the network tab has saved on disk, or nothing."""
    path = config_dir / "plugins" / "network.json"
    return json.loads(path.read_text()) if path.exists() else {}


def _tor_field():
    """Return the USING_TOR field as the network tab defines it."""
    return registry.get_settings_field_map(tab_name="network")["USING_TOR"][0]


def _shown() -> object:
    """Return the value Settings shows for Tor Routing."""
    return registry.serialize_field(_tor_field(), "network")["value"]


def test_a_saved_tor_flag_is_not_shown_when_no_tor_runs(config_dir, monkeypatch):
    """USING_TOR was saved while Tor ran and stayed true after the variable was removed."""
    monkeypatch.setattr(env, "USING_TOR", False)
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "network.json").write_text(json.dumps({"USING_TOR": True}))

    assert _shown() is False


def test_tor_routing_follows_the_running_state(config_dir, monkeypatch):
    """The value is read each time, so it is whatever the process was started with."""
    monkeypatch.setattr(env, "USING_TOR", True)
    assert _shown() is True

    monkeypatch.setattr(env, "USING_TOR", False)
    assert _shown() is False


@pytest.mark.parametrize("value", ["1", "yes", "True", "on"])
def test_a_value_that_does_not_start_tor_is_not_shown_as_on(config_dir, monkeypatch, value):
    """entrypoint.sh starts Tor only for the exact value true, so Settings must agree."""
    monkeypatch.setenv("USING_TOR", value)
    monkeypatch.setattr(env, "USING_TOR", env._starts_tunnel("USING_TOR"))

    assert _shown() is False


def test_the_tor_flag_is_not_synced_into_the_saved_config(config_dir, monkeypatch):
    """Starting with Tor on used to write USING_TOR into the network config."""
    monkeypatch.setenv("USING_TOR", "true")
    monkeypatch.setattr(env, "USING_TOR", True)

    registry.sync_env_to_config()

    assert "USING_TOR" not in _saved_network(config_dir)


def test_a_fresh_config_does_not_save_the_tor_default(config_dir, monkeypatch):
    """First-start defaults skip the field, which would otherwise save the running state."""
    monkeypatch.setattr(env, "USING_TOR", True)

    registry.initialize_default_configs()

    assert "USING_TOR" not in _saved_network(config_dir)


def test_a_flag_saved_by_an_older_version_is_dropped(config_dir, monkeypatch):
    """The saved value from before the fix is removed on startup, and other settings stay."""
    monkeypatch.setattr(env, "USING_TOR", False)
    (config_dir / "plugins").mkdir()
    (config_dir / "plugins" / "network.json").write_text(
        json.dumps({"USING_TOR": True, "PROXY_MODE": "http"})
    )

    registry.sync_env_to_config()

    saved = _saved_network(config_dir)
    assert "USING_TOR" not in saved
    assert saved["PROXY_MODE"] == "http"


def test_saving_settings_does_not_store_the_tor_flag(config_dir, monkeypatch):
    """A save that carries USING_TOR leaves it out, whatever the page sent back."""
    monkeypatch.setattr(env, "USING_TOR", False)

    result = registry.update_settings("network", {"USING_TOR": True, "PROXY_MODE": "none"})

    assert result["success"] is True
    assert "USING_TOR" not in result["updated"]
    assert "USING_TOR" not in _saved_network(config_dir)


@pytest.mark.parametrize(("tor", "auto"), [(False, True), (True, False)])
def test_auto_dns_rotation_follows_the_running_state(monkeypatch, tor, auto):
    """Automatic DNS rotation is off only while Tor runs, not once Tor was ever saved."""
    from shelfmark.download import network

    monkeypatch.setattr(env, "USING_TOR", tor)
    monkeypatch.setattr(
        network.app_config, "get", lambda key, default="": "auto" if key == "CUSTOM_DNS" else True
    )

    assert network._is_auto_dns_mode() is auto


def test_a_failed_cleanup_leaves_the_saved_config_as_it_was(config_dir, monkeypatch):
    """A write that fails must not lose the other settings or change what the process sees."""
    from pathlib import Path

    monkeypatch.setattr(env, "USING_TOR", False)
    path = config_dir / "plugins" / "network.json"
    path.parent.mkdir()
    original = json.dumps({"USING_TOR": True, "PROXY_MODE": "http"})
    path.write_text(original)

    real_replace = Path.replace

    def failing_replace(self, target):
        """Fail only the swap of the cleaned file into place."""
        if self.name == "network.json.tmp":
            raise OSError("disk full")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)

    registry.drop_saved_live_values()

    assert path.read_text() == original
    assert not (path.parent / "network.json.tmp").exists()
    assert registry.load_config_file("network")["PROXY_MODE"] == "http"


def test_cleanup_on_a_read_only_config_folder_does_not_stop_startup(config_dir, monkeypatch):
    """Writing and removing the scratch file both fail there; the saved config stays."""
    import errno
    from pathlib import Path

    monkeypatch.setattr(env, "USING_TOR", False)
    path = config_dir / "plugins" / "network.json"
    path.parent.mkdir()
    original = json.dumps({"USING_TOR": True, "PROXY_MODE": "http"})
    path.write_text(original)

    def read_only(self, *args, **kwargs):
        """Fail as a read-only filesystem does."""
        raise OSError(errno.EROFS, "Read-only file system")

    real_open = Path.open

    def open_for_reading_only(self, mode="r", *args, **kwargs):
        """Allow reads and refuse writes."""
        if "w" in mode:
            read_only(self)
        return real_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_for_reading_only)
    monkeypatch.setattr(Path, "unlink", read_only)

    registry.drop_saved_live_values()

    assert path.read_text() == original
    assert _shown() is False

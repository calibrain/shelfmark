"""The process's proxy variables follow Settings → Network (#1689).

Apprise and qbittorrent-api read HTTP_PROXY/HTTPS_PROXY/NO_PROXY instead of calling
get_proxies(), so apply_proxy_environment() keeps those variables in step with the
settings, leaving alone any that were set in the container's own environment.
"""

import pytest

from shelfmark.download import network

_PROXY = "http://proxy.example.com:8118"
_VARIABLES = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")


@pytest.fixture
def settings(monkeypatch):
    """Settings the function reads, a clean environment, and no variables set by it yet."""
    for name in (*_VARIABLES, "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    from shelfmark.core import settings_registry

    monkeypatch.setattr(settings_registry, "_PROCESS_WRITTEN_ENV", {})
    network.app_config.get("PROXY_MODE")  # load the settings registry before patching
    values = {"PROXY_MODE": "http", "HTTP_PROXY": _PROXY, "HTTPS_PROXY": "", "NO_PROXY": ""}
    monkeypatch.setattr(network.app_config, "get", lambda key, default="": values.get(key, default))
    yield values
    import os

    # Directly: monkeypatch.delenv would record the written values and put them back.
    for name in _VARIABLES:
        os.environ.pop(name, None)


def test_the_proxy_settings_are_written_to_the_environment(settings, monkeypatch):
    """With a proxy configured, both cases of each variable carry it."""
    import os

    network.apply_proxy_environment()

    assert os.environ["HTTP_PROXY"] == os.environ["http_proxy"] == _PROXY
    assert os.environ["HTTPS_PROXY"] == os.environ["https_proxy"] == _PROXY
    assert "192.168.0.0/16" in os.environ["NO_PROXY"].split(",")
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


def test_no_proxy_lists_the_configured_services_docker_names(settings):
    """A service named by a single-label host is listed; public and dotted hosts are not."""
    import os

    settings.update(
        QBITTORRENT_URL="http://qbittorrent:8080",
        PROWLARR_URL="prowlarr:9696",
        AUDIOBOOKSHELF_URL="https://abs.example.com",
        SABNZBD_URL="http://192.168.1.20:8085",
        AA_MIRROR_URLS=["https://annas-archive.gl"],
        NO_PROXY="*.example.org",
    )

    network.apply_proxy_environment()

    entries = os.environ["NO_PROXY"].split(",")
    assert "qbittorrent" in entries
    assert "prowlarr" in entries
    assert ".example.org" in entries
    assert not {"abs.example.com", "annas-archive.gl", "192.168.1.20"} & set(entries)


def test_a_settings_change_replaces_the_variables(settings):
    """Saving a new proxy updates the variables Shelfmark set."""
    import os

    network.apply_proxy_environment()
    settings["HTTP_PROXY"] = "http://other-proxy.example.com:3128"
    network.apply_proxy_environment()

    assert os.environ["HTTP_PROXY"] == "http://other-proxy.example.com:3128"


def test_turning_the_proxy_off_removes_the_variables_shelfmark_set(settings):
    """With no proxy in Settings, the variables Shelfmark set are gone."""
    import os

    network.apply_proxy_environment()
    settings["PROXY_MODE"] = "none"
    network.apply_proxy_environment()

    assert not set(_VARIABLES) & set(os.environ)


def test_a_proxy_from_the_container_environment_is_left_alone(settings, monkeypatch):
    """A variable that was there before Shelfmark set it is never replaced or removed."""
    import os

    monkeypatch.setenv("HTTP_PROXY", "http://container-proxy:3128")
    network.apply_proxy_environment()
    settings["PROXY_MODE"] = "none"
    network.apply_proxy_environment()

    assert os.environ["HTTP_PROXY"] == "http://container-proxy:3128"


def test_a_settings_save_applies_the_variables(settings, monkeypatch):
    """update_settings() brings the environment in step after writing the settings."""
    from shelfmark.core import settings_registry

    calls = []
    monkeypatch.setattr(settings_registry, "save_config_file", lambda tab, values: True)
    monkeypatch.setattr(network, "apply_proxy_environment", lambda: calls.append("applied"))

    result = settings_registry.update_settings("network", {"PROXY_MODE": "http"})

    assert result["success"] is True
    assert calls == ["applied"]


def test_sending_a_notification_leaves_the_environment_alone(settings, monkeypatch):
    """Dispatching to Apprise no longer writes proxy variables itself."""
    import os

    from shelfmark.core import notifications

    before = {name: os.environ.get(name) for name in _VARIABLES}
    notifications._dispatch_to_apprise(
        ["json://localhost:1"], title="t", body="b", notify_type="info"
    )

    assert {name: os.environ.get(name) for name in _VARIABLES} == before


def test_the_proxy_fields_stay_editable_after_the_variables_are_written(monkeypatch):
    """Through the real settings save: the written variables never lock their own fields.

    The fields share their names with HTTP_PROXY/HTTPS_PROXY/NO_PROXY, so a value Shelfmark
    wrote must not read as "set via env", or the next save would skip them.
    """
    import os

    from shelfmark.core import settings_registry

    for name in (*_VARIABLES, "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings_registry, "_PROCESS_WRITTEN_ENV", {})
    original = {
        key: network.app_config.get(key, "") for key in ("PROXY_MODE", "HTTP_PROXY", "NO_PROXY")
    }
    try:
        # Saves from the UI are never 50 ms apart; let each save here reload the settings.
        monkeypatch.setattr(network.app_config, "_last_refresh_time", 0.0)
        settings_registry.update_settings("network", {"PROXY_MODE": "http", "HTTP_PROXY": _PROXY})
        assert os.environ["HTTP_PROXY"] == _PROXY
        monkeypatch.setattr(network.app_config, "_last_refresh_time", 0.0)

        result = settings_registry.update_settings(
            "network",
            {"HTTP_PROXY": "http://other-proxy.example.com:3128", "NO_PROXY": "*.example.org"},
        )

        assert {"HTTP_PROXY", "NO_PROXY"} <= set(result["updated"])
        assert "set via env" not in result["message"]
        assert os.environ["HTTP_PROXY"] == "http://other-proxy.example.com:3128"
        assert ".example.org" in os.environ["NO_PROXY"].split(",")
    finally:
        monkeypatch.setattr(network.app_config, "_last_refresh_time", 0.0)
        settings_registry.update_settings("network", original)
        for name in _VARIABLES:
            os.environ.pop(name, None)


def test_a_proxy_variable_from_the_deployment_still_reads_as_set_via_env(monkeypatch):
    """A value Shelfmark didn't write keeps the field locked, as before."""
    from shelfmark.core import settings_registry

    monkeypatch.setattr(settings_registry, "_PROCESS_WRITTEN_ENV", {})
    monkeypatch.setenv("NO_PROXY", "deployment.example.com")
    network.app_config.get("NO_PROXY")
    field, _tab = settings_registry.get_settings_field_map()["NO_PROXY"]

    assert settings_registry.is_value_from_env(field) is True

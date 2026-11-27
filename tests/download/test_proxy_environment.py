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
    monkeypatch.setattr(settings_registry, "_DEPLOYMENT_ENV", {})
    monkeypatch.setattr(network, "_deployment_no_proxy", {}, raising=False)
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
    assert "qbittorrent:8080" in entries
    assert "prowlarr:9696" in entries
    assert not {"qbittorrent", "prowlarr"} & set(entries)
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


@pytest.mark.parametrize("mode", ["USING_TOR", "USING_WIREGUARD"])
def test_no_proxy_is_written_when_a_tunnel_routes_the_traffic(settings, monkeypatch, mode):
    """Under Tor or WireGuard the saved proxy reaches no library through the environment."""
    import os

    from shelfmark.config import env

    monkeypatch.setattr(env, mode, True)

    network.apply_proxy_environment()

    assert not {"HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"} & set(os.environ)
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "*"


@pytest.mark.parametrize("mode", ["USING_TOR", "USING_WIREGUARD"])
def test_a_proxy_in_the_container_environment_is_not_used_under_a_tunnel(
    settings, monkeypatch, mode
):
    """A library reading the environment itself, such as Apprise, goes direct."""
    import os

    from requests.utils import get_environ_proxies

    from shelfmark.config import env

    monkeypatch.setattr(env, mode, True)
    monkeypatch.setenv("HTTPS_PROXY", "http://container-proxy:3128")
    monkeypatch.setenv("NO_PROXY", "internal.example.com")

    network.apply_proxy_environment()

    assert os.environ["HTTPS_PROXY"] == "http://container-proxy:3128"
    assert os.environ["NO_PROXY"] == "*"
    assert get_environ_proxies("https://api.hardcover.app/") == {}


def test_a_proxy_in_the_container_environment_still_applies_without_a_tunnel(settings, monkeypatch):
    """Only a tunnel overrides the container's own NO_PROXY; otherwise it is left alone."""
    import os

    from requests.utils import get_environ_proxies

    settings["PROXY_MODE"] = "none"
    monkeypatch.setenv("HTTPS_PROXY", "http://container-proxy:3128")
    monkeypatch.setenv("NO_PROXY", "internal.example.com")

    network.apply_proxy_environment()

    assert os.environ["NO_PROXY"] == "internal.example.com"
    assert (
        get_environ_proxies("https://api.hardcover.app/")["https"] == "http://container-proxy:3128"
    )


def test_a_proxy_from_the_container_environment_is_left_alone(settings, monkeypatch):
    """A variable that was there before Shelfmark set it is never replaced or removed."""
    import os

    monkeypatch.setenv("HTTP_PROXY", "http://container-proxy:3128")
    network.apply_proxy_environment()
    settings["PROXY_MODE"] = "none"
    network.apply_proxy_environment()

    assert os.environ["HTTP_PROXY"] == "http://container-proxy:3128"


@pytest.mark.parametrize("name", ["no_proxy", "NO_PROXY"])
def test_a_no_proxy_from_the_container_does_not_hide_the_local_rule(settings, monkeypatch, name):
    """requests reads ``no_proxy`` before ``NO_PROXY``, so each case carries both lists."""
    import os

    from requests.utils import get_environ_proxies

    monkeypatch.setenv(name, "partner.example.org")

    network.apply_proxy_environment()

    assert get_environ_proxies("http://192.168.1.20:8080/") == {}
    assert get_environ_proxies("http://qbittorrent.local/") == {}
    assert get_environ_proxies("https://partner.example.org/") == {}
    assert get_environ_proxies("https://api.hardcover.app/")["https"] == _PROXY
    assert os.environ["no_proxy"] == os.environ["NO_PROXY"]


@pytest.mark.parametrize("name", ["no_proxy", "NO_PROXY"])
def test_the_containers_no_proxy_comes_back_when_the_proxy_is_turned_off(
    settings, monkeypatch, name
):
    """With no proxy in Settings there is no local rule to add, so its own value is restored."""
    import os

    monkeypatch.setenv(name, "partner.example.org")
    network.apply_proxy_environment()
    settings["PROXY_MODE"] = "none"

    network.apply_proxy_environment()

    assert os.environ[name] == "partner.example.org"
    assert not {"HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"} & set(os.environ)


def test_an_older_save_cannot_apply_over_a_newer_one(settings, monkeypatch):
    """The environment is computed under the lock, so saves apply in the order they lock.

    The first save reads proxy A and stalls. A second save of proxy B starts meanwhile.
    Computed outside the lock, B finishes first and A then overwrites it.
    """
    import os
    import threading

    stalled = threading.Event()
    resume = threading.Event()
    first = []
    real = network.proxy_environment

    def stalling_proxy_environment():
        computed = real()
        if not first:
            first.append(True)
            stalled.set()
            resume.wait(timeout=5)
        return computed

    monkeypatch.setattr(network, "proxy_environment", stalling_proxy_environment)
    older = threading.Thread(target=network.apply_proxy_environment)
    older.start()
    assert stalled.wait(timeout=5)

    settings["HTTP_PROXY"] = "http://newer-proxy.example.com:3128"
    newer = threading.Thread(target=network.apply_proxy_environment)
    newer.start()
    newer.join(timeout=1)  # finishes at once without the lock; waits for it with it
    resume.set()
    older.join(timeout=5)
    newer.join(timeout=5)

    assert os.environ["HTTP_PROXY"] == "http://newer-proxy.example.com:3128"


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
    wrote must not read as "set via env", or the next save would skip them. The two saves
    come back to back, inside the config's 50 ms refresh window, so the second also shows
    that a save always reloads the settings.
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
        settings_registry.update_settings("network", {"PROXY_MODE": "http", "HTTP_PROXY": _PROXY})
        assert os.environ["HTTP_PROXY"] == _PROXY

        result = settings_registry.update_settings(
            "network",
            {"HTTP_PROXY": "http://other-proxy.example.com:3128", "NO_PROXY": "*.example.org"},
        )

        assert {"HTTP_PROXY", "NO_PROXY"} <= set(result["updated"])
        assert "set via env" not in result["message"]
        assert os.environ["HTTP_PROXY"] == "http://other-proxy.example.com:3128"
        assert ".example.org" in os.environ["NO_PROXY"].split(",")
    finally:
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


def test_the_containers_no_proxy_keeps_its_field_locked(settings, monkeypatch):
    """Adding the local rule to the container's NO_PROXY leaves the setting set via env.

    The field still reads the container's own value, so a save cannot replace it and the
    app keeps bypassing the hosts the deployment listed.
    """
    import os

    from shelfmark.core import settings_registry

    field, _tab = settings_registry.get_settings_field_map()["NO_PROXY"]
    monkeypatch.setenv("NO_PROXY", "deployment.example.com")

    network.apply_proxy_environment()
    network.apply_proxy_environment()

    assert "192.168.0.0/16" in os.environ["NO_PROXY"].split(",")
    assert settings_registry.is_value_from_env(field) is True
    assert settings_registry._get_env_value_for_field(field) == (True, "deployment.example.com")


@pytest.mark.parametrize("value", ["*", "abs.example.org, *"])
@pytest.mark.parametrize("name", ["NO_PROXY", "no_proxy"])
def test_a_wildcard_no_proxy_from_the_container_stays_the_whole_value(
    settings, monkeypatch, name, value
):
    """requests reads ``*`` as "bypass everything" only on its own, so nothing is added to it."""
    import os

    import requests

    monkeypatch.setenv(name, value)

    network.apply_proxy_environment()

    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == "*"
    url = "http://api.example.com/"
    merged = requests.Session().merge_environment_settings(url, {}, None, None, None)
    assert "http" not in merged["proxies"]


def test_a_service_name_does_not_bypass_the_proxy_for_a_public_domain(settings):
    """A bare ``app`` is read as a suffix, covering ``notify.example.app`` on any port; with the port, only on that one."""
    import os

    import requests

    settings.update(QBITTORRENT_URL="http://app:8080", SABNZBD_URL="http://nzb")
    network.apply_proxy_environment()

    def used(url):
        """The proxy an environment-reading client would pick for ``url``, or None."""
        merged = requests.Session().merge_environment_settings(url, {}, None, None, None)
        return merged["proxies"].get("https" if url.startswith("https") else "http")

    assert used("http://app:8080/") is None
    assert used("https://notify.example.app/") == _PROXY
    assert used("https://notify.example.app:9000/") == _PROXY
    assert "nzb" in os.environ["NO_PROXY"].split(",")


def test_a_script_gets_the_service_names_without_their_port(settings, monkeypatch):
    """curl ignores a host:port entry, so a script's NO_PROXY lists the name alone too (#1766)."""
    settings.update(
        QBITTORRENT_URL="http://qbittorrent:8080",
        PROWLARR_URL="prowlarr:9696",
        SABNZBD_URL="http://nzb",
        AUDIOBOOKSHELF_URL="https://abs.example.com",
        NO_PROXY="*.example.org",
    )
    network.apply_proxy_environment()

    for name in ("NO_PROXY", "no_proxy"):
        entries = network.script_environment()[name].split(",")
        assert {"qbittorrent", "prowlarr", "nzb"} <= set(entries)
        assert {"qbittorrent:8080", "prowlarr:9696", ".example.org"} <= set(entries)
        assert "abs.example.com" not in entries
        assert len(entries) == len(set(entries))


def test_the_apps_own_no_proxy_keeps_the_port(settings):
    """Only the script's copy gets the bare name, since a name alone matches as a suffix."""
    import os

    settings.update(QBITTORRENT_URL="http://qbittorrent:8080")
    network.apply_proxy_environment()

    network.script_environment()

    assert "qbittorrent" not in os.environ["NO_PROXY"].split(",")


def test_a_script_environment_without_a_no_proxy_is_unchanged(settings, monkeypatch):
    """With nothing to extend there is nothing to add."""
    import os

    settings.update(QBITTORRENT_URL="http://qbittorrent:8080")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)

    assert network.script_environment() == dict(os.environ)


@pytest.mark.parametrize("value", ["*", "  *  ", ""])
def test_a_wildcard_or_empty_no_proxy_is_left_alone_for_a_script(settings, monkeypatch, value):
    """A lone * already bypasses everything, and an empty value names nothing."""
    settings.update(QBITTORRENT_URL="http://qbittorrent:8080")
    monkeypatch.setenv("NO_PROXY", value)
    monkeypatch.delenv("no_proxy", raising=False)

    assert network.script_environment()["NO_PROXY"] == value


def test_a_name_the_container_already_lists_is_not_repeated(settings, monkeypatch):
    """A NO_PROXY that already has the bare name keeps one copy of it."""
    settings.update(QBITTORRENT_URL="http://qbittorrent:8080")
    monkeypatch.setenv("NO_PROXY", "localhost,qbittorrent")
    monkeypatch.delenv("no_proxy", raising=False)

    assert network.script_environment()["NO_PROXY"] == "localhost,qbittorrent"


@pytest.mark.parametrize("name", ["app", "dev", "io"])
def test_a_service_named_like_a_public_suffix_gets_no_bare_entry(settings, name):
    """A bare ``app`` would bypass the proxy for any ``.app`` site in a script using requests."""
    settings.update(QBITTORRENT_URL=f"http://{name}:8080", PROWLARR_URL="http://prowlarr:9696")
    network.apply_proxy_environment()

    entries = network.script_environment()["NO_PROXY"].split(",")

    assert f"{name}:8080" in entries
    assert name not in entries
    assert "prowlarr" in entries

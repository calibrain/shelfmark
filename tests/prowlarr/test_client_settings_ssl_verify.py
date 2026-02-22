"""SSL verification behavior for download client settings test callbacks."""

import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def make_config_getter(values):
    """Create a config.get function that returns values from a dict."""

    def getter(key, default=""):
        return values.get(key, default)

    return getter


def test_transmission_settings_test_connection_applies_ssl_verify(monkeypatch):
    """Transmission settings callback should apply verify mode to transmission-rpc session."""
    from shelfmark.core.config import config as config_obj
    from shelfmark.download.clients import settings as settings_module

    current_values = {
        "TRANSMISSION_URL": "https://localhost:9091",
        "TRANSMISSION_USERNAME": "admin",
        "TRANSMISSION_PASSWORD": "password",
    }
    monkeypatch.setattr(config_obj, "get", make_config_getter(current_values))
    monkeypatch.setattr(settings_module, "get_ssl_verify", lambda _url: False)

    mock_http_session = SimpleNamespace(verify=True)
    mock_client = MagicMock()
    mock_client._http_session = mock_http_session
    mock_client.get_session.return_value = SimpleNamespace(version="4.0.0")

    mock_transmission_rpc = MagicMock()
    mock_transmission_rpc.Client = MagicMock(return_value=mock_client)

    with patch.dict("sys.modules", {"transmission_rpc": mock_transmission_rpc}):
        result = settings_module._test_transmission_connection(current_values=current_values)

    assert result["success"] is True
    assert mock_http_session.verify is False


def test_transmission_settings_test_connection_disables_verify_during_constructor(monkeypatch):
    """Settings callback should disable verify before transmission-rpc constructor bootstraps."""
    from shelfmark.core.config import config as config_obj
    from shelfmark.download.clients import settings as settings_module

    current_values = {
        "TRANSMISSION_URL": "https://localhost:9091",
        "TRANSMISSION_USERNAME": "admin",
        "TRANSMISSION_PASSWORD": "password",
    }
    monkeypatch.setattr(config_obj, "get", make_config_getter(current_values))
    monkeypatch.setattr(settings_module, "get_ssl_verify", lambda _url: False)

    transmission_pkg = types.ModuleType("transmission_rpc")
    transmission_pkg.__path__ = []
    transmission_client_mod = types.ModuleType("transmission_rpc.client")

    def _base_session_factory():
        return types.SimpleNamespace(verify=True)

    transmission_client_mod.requests = types.SimpleNamespace(Session=_base_session_factory)

    def _fake_client_ctor(**_kwargs):
        bootstrap_session = transmission_client_mod.requests.Session()
        if bootstrap_session.verify is not False:
            raise RuntimeError("verify not disabled during constructor bootstrap")
        client = MagicMock()
        client._http_session = bootstrap_session
        client.get_session.return_value = types.SimpleNamespace(version="4.0.0")
        return client

    transmission_pkg.Client = _fake_client_ctor
    transmission_pkg.client = transmission_client_mod

    with patch.dict(
        "sys.modules",
        {
            "transmission_rpc": transmission_pkg,
            "transmission_rpc.client": transmission_client_mod,
        },
    ):
        result = settings_module._test_transmission_connection(current_values=current_values)

    assert result["success"] is True


def test_rtorrent_settings_test_connection_uses_unverified_transport_when_disabled(monkeypatch):
    """rTorrent settings callback should pass SafeTransport for HTTPS when verify is disabled."""
    from shelfmark.core.config import config as config_obj
    from shelfmark.download.clients import settings as settings_module

    current_values = {
        "RTORRENT_URL": "https://localhost:8080/RPC2",
        "RTORRENT_USERNAME": "",
        "RTORRENT_PASSWORD": "",
    }
    monkeypatch.setattr(config_obj, "get", make_config_getter(current_values))
    monkeypatch.setattr(settings_module, "get_ssl_verify", lambda _url: False)

    mock_rpc = MagicMock()
    mock_rpc.system.client_version.return_value = "0.9.8"

    mock_xmlrpc = MagicMock()
    mock_xmlrpc.ServerProxy = MagicMock(return_value=mock_rpc)

    with patch.dict("sys.modules", {"xmlrpc.client": mock_xmlrpc}):
        result = settings_module._test_rtorrent_connection(current_values=current_values)

    assert result["success"] is True
    assert mock_xmlrpc.SafeTransport.called is True
    assert "transport" in mock_xmlrpc.ServerProxy.call_args.kwargs

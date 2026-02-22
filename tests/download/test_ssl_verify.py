"""Tests for certificate validation / SSL verify utilities."""

import warnings

import pytest


# ---------------------------------------------------------------------------
# get_ssl_verify()
# ---------------------------------------------------------------------------

class TestGetSslVerify:
    """Tests for get_ssl_verify() return values across all modes."""

    def test_enabled_returns_true(self, monkeypatch):
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "enabled" if k == "CERTIFICATE_VALIDATION" else d)
        assert network.get_ssl_verify("https://example.com") is True

    def test_enabled_returns_true_for_local_url(self, monkeypatch):
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "enabled" if k == "CERTIFICATE_VALIDATION" else d)
        assert network.get_ssl_verify("https://localhost:8080") is True

    def test_disabled_returns_false_for_public_url(self, monkeypatch):
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled" if k == "CERTIFICATE_VALIDATION" else d)
        assert network.get_ssl_verify("https://example.com") is False

    def test_disabled_returns_false_for_local_url(self, monkeypatch):
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled" if k == "CERTIFICATE_VALIDATION" else d)
        assert network.get_ssl_verify("https://192.168.1.1:9091") is False

    def test_disabled_returns_false_with_no_url(self, monkeypatch):
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled" if k == "CERTIFICATE_VALIDATION" else d)
        assert network.get_ssl_verify() is False

    def test_default_when_unset_returns_true(self, monkeypatch):
        """When CERTIFICATE_VALIDATION is not in config, default is 'enabled'."""
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": d)
        assert network.get_ssl_verify("https://example.com") is True


class TestGetSslVerifyDisabledLocal:
    """Tests for 'disabled_local' mode with various address types."""

    @pytest.fixture(autouse=True)
    def _set_mode(self, monkeypatch):
        import shelfmark.download.network as network

        self.network = network
        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled_local" if k == "CERTIFICATE_VALIDATION" else d)

    # --- Should return False (local addresses) ---

    def test_localhost(self):
        assert self.network.get_ssl_verify("https://localhost:8080/path") is False

    def test_127_0_0_1(self):
        assert self.network.get_ssl_verify("http://127.0.0.1:9091") is False

    def test_ipv6_loopback(self):
        assert self.network.get_ssl_verify("http://[::1]:8080") is False

    def test_private_10_x(self):
        assert self.network.get_ssl_verify("https://10.0.0.5:443") is False

    def test_private_172_16_x(self):
        assert self.network.get_ssl_verify("https://172.16.0.1:8080") is False

    def test_private_172_31_x(self):
        assert self.network.get_ssl_verify("https://172.31.255.255:443") is False

    def test_private_192_168_x(self):
        assert self.network.get_ssl_verify("https://192.168.1.100:9696") is False

    def test_dot_local_domain(self):
        assert self.network.get_ssl_verify("https://authelia.local:9091") is False

    def test_dot_internal_domain(self):
        assert self.network.get_ssl_verify("https://prowlarr.internal:9696") is False

    def test_dot_lan_domain(self):
        assert self.network.get_ssl_verify("https://server.lan:443") is False

    def test_dot_home_domain(self):
        assert self.network.get_ssl_verify("https://nas.home:5000") is False

    def test_dot_docker_domain(self):
        assert self.network.get_ssl_verify("https://app.docker:8080") is False

    def test_simple_hostname_no_dot(self):
        """Docker-style service names like 'prowlarr', 'deluge'."""
        assert self.network.get_ssl_verify("http://prowlarr:9696") is False

    def test_link_local_169_254(self):
        assert self.network.get_ssl_verify("http://169.254.1.1:8080") is False

    # --- Should return True (public addresses) ---

    def test_public_domain(self):
        assert self.network.get_ssl_verify("https://example.com") is True

    def test_public_ip(self):
        assert self.network.get_ssl_verify("https://8.8.8.8:443") is True

    def test_public_subdomain(self):
        assert self.network.get_ssl_verify("https://api.hardcover.app/v1/graphql") is True

    def test_172_32_is_public(self):
        """172.32.x.x is NOT in the private range (only 172.16-31.x.x)."""
        assert self.network.get_ssl_verify("https://172.32.0.1:443") is True

    def test_empty_url_returns_true(self):
        """No URL means we can't determine locality — default to verify."""
        assert self.network.get_ssl_verify("") is True

    def test_no_url_returns_true(self):
        assert self.network.get_ssl_verify() is True


# ---------------------------------------------------------------------------
# _apply_ssl_warning_suppression()
# ---------------------------------------------------------------------------

class TestApplySslWarningSuppression:
    """Tests for urllib3 InsecureRequestWarning suppression toggling."""

    @pytest.fixture(autouse=True)
    def _reset_suppression_flag(self):
        """Ensure the module-level flag is clean before each test."""
        import shelfmark.download.network as network
        original = network._ssl_warnings_suppressed
        yield
        network._ssl_warnings_suppressed = original

    def test_enabled_at_init_is_noop(self, monkeypatch):
        """When mode is 'enabled' and warnings were never suppressed, nothing changes."""
        import shelfmark.download.network as network

        network._ssl_warnings_suppressed = False
        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "enabled" if k == "CERTIFICATE_VALIDATION" else d)

        filters_before = list(warnings.filters)
        network._apply_ssl_warning_suppression()
        filters_after = list(warnings.filters)

        assert filters_before == filters_after

    def test_disabled_mode_suppresses_warnings(self, monkeypatch):
        import urllib3
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled" if k == "CERTIFICATE_VALIDATION" else d)
        network._apply_ssl_warning_suppression()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            warnings.warn("test", urllib3.exceptions.InsecureRequestWarning)

        # urllib3.disable_warnings adds a filter that suppresses — so recorded warnings
        # should be empty after suppression is applied. However, our catch_warnings
        # with "always" takes precedence within the context manager. Instead, check
        # that the filter was installed.
        filters = [f for f in warnings.filters if len(f) >= 3 and f[2] is urllib3.exceptions.InsecureRequestWarning]
        assert len(filters) > 0

    def test_disabled_local_mode_suppresses_warnings(self, monkeypatch):
        import urllib3
        import shelfmark.download.network as network

        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled_local" if k == "CERTIFICATE_VALIDATION" else d)
        network._apply_ssl_warning_suppression()

        filters = [f for f in warnings.filters if len(f) >= 3 and f[2] is urllib3.exceptions.InsecureRequestWarning]
        assert len(filters) > 0

    def test_enabled_mode_restores_warnings(self, monkeypatch):
        import urllib3
        import shelfmark.download.network as network

        # First suppress
        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "disabled" if k == "CERTIFICATE_VALIDATION" else d)
        network._apply_ssl_warning_suppression()

        # Then restore
        monkeypatch.setattr(network.app_config, "get", lambda k, d="": "enabled" if k == "CERTIFICATE_VALIDATION" else d)
        network._apply_ssl_warning_suppression()

        # "default" filter should be present for InsecureRequestWarning
        default_filters = [
            f for f in warnings.filters
            if len(f) >= 3 and f[0] == "default" and f[2] is urllib3.exceptions.InsecureRequestWarning
        ]
        assert len(default_filters) > 0


# ---------------------------------------------------------------------------
# Settings registration
# ---------------------------------------------------------------------------

class TestCertificateValidationSetting:
    """Tests for the CERTIFICATE_VALIDATION settings field registration."""

    def _get_network_fields(self):
        import shelfmark.config.settings  # noqa: F401 — ensure settings tabs are registered

        from shelfmark.core.settings_registry import get_settings_tab

        tab = get_settings_tab("network")
        assert tab is not None
        return {field.key: field for field in tab.fields if hasattr(field, "key")}

    def test_field_registered(self):
        fields = self._get_network_fields()
        assert "CERTIFICATE_VALIDATION" in fields

    def test_field_is_select(self):
        from shelfmark.core.settings_registry import SelectField

        fields = self._get_network_fields()
        assert isinstance(fields["CERTIFICATE_VALIDATION"], SelectField)

    def test_field_default_is_enabled(self):
        fields = self._get_network_fields()
        assert fields["CERTIFICATE_VALIDATION"].default == "enabled"

    def test_field_has_three_options(self):
        fields = self._get_network_fields()
        options = fields["CERTIFICATE_VALIDATION"].options
        assert len(options) == 3

    def test_field_option_values(self):
        fields = self._get_network_fields()
        values = [opt["value"] for opt in fields["CERTIFICATE_VALIDATION"].options]
        assert values == ["enabled", "disabled_local", "disabled"]


# ---------------------------------------------------------------------------
# Live-apply on settings save
# ---------------------------------------------------------------------------

def test_update_settings_certificate_validation_triggers_suppression(monkeypatch):
    """Changing CERTIFICATE_VALIDATION via update_settings calls _apply_ssl_warning_suppression."""
    import shelfmark.config.settings  # noqa: F401 — ensure settings tabs are registered

    from shelfmark.core.config import config as config_obj
    from shelfmark.core.settings_registry import update_settings

    monkeypatch.setattr("shelfmark.core.settings_registry.save_config_file", lambda _tab, _values: True)
    monkeypatch.setattr(config_obj, "refresh", lambda: None)

    called = {"count": 0}

    import shelfmark.download.network as network

    def fake_apply():
        called["count"] += 1

    monkeypatch.setattr(network, "_apply_ssl_warning_suppression", fake_apply)

    result = update_settings("network", {"CERTIFICATE_VALIDATION": "disabled"})

    assert result["success"] is True
    assert called["count"] == 1

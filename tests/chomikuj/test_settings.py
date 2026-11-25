"""Tests for the Chomikuj settings tab registration and Test Login action."""

import importlib

import pytest

from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    FieldBase,
    NumberField,
    PasswordField,
    TextField,
    get_settings_tab,
)
from shelfmark.release_sources.chomikuj import client as chomikuj_client
from shelfmark.release_sources.chomikuj import session as chomikuj_session
from shelfmark.release_sources.chomikuj.client import ChomikujAuthError, ChomikujError
from shelfmark.release_sources.chomikuj.settings import (
    _resolve_setting_text,
    _test_chomikuj_connection,
    chomikuj_config_settings,
)

ACTION_KEY = "test_chomikuj"
EXPECTED_KEYS = [
    "CHOMIKUJ_ENABLED",
    "CHOMIKUJ_USERNAME",
    "CHOMIKUJ_PASSWORD",
    ACTION_KEY,
    "CHOMIKUJ_ALLOW_TRANSFER_SPEND",
    "CHOMIKUJ_MAX_RESULTS",
    "CHOMIKUJ_BASE_URL",
]
CONFIG_KEYS = [key for key in EXPECTED_KEYS if key != ACTION_KEY]

USERNAME = "alice@example.invalid"
PASSWORD = "sup3r-secret-p@ssword"
BASE_URL = "https://mobile.chomikuj.pl"
NORMALIZED_BASE_URL = f"{BASE_URL}/api/v3"


def _fields():
    """Return the registered Chomikuj config fields."""
    return chomikuj_config_settings()


def _field(key: str):
    """Return the registered field with the given key."""
    return next(f for f in _fields() if getattr(f, "key", None) == key)


def test_fields_registered_in_order():
    """Test fields registered in order."""
    assert [f.key for f in _fields()] == EXPECTED_KEYS


def test_fields_have_expected_types():
    """Test fields have expected types."""
    assert isinstance(_field("CHOMIKUJ_ENABLED"), CheckboxField)
    assert isinstance(_field("CHOMIKUJ_USERNAME"), TextField)
    assert isinstance(_field("CHOMIKUJ_PASSWORD"), PasswordField)
    assert isinstance(_field("CHOMIKUJ_ALLOW_TRANSFER_SPEND"), CheckboxField)
    assert isinstance(_field("CHOMIKUJ_MAX_RESULTS"), NumberField)
    assert isinstance(_field("CHOMIKUJ_BASE_URL"), TextField)


def test_fields_have_expected_defaults_and_bounds():
    """Test fields have expected defaults and bounds."""
    assert _field("CHOMIKUJ_ENABLED").default is False
    assert _field("CHOMIKUJ_ALLOW_TRANSFER_SPEND").default is True
    max_results = _field("CHOMIKUJ_MAX_RESULTS")
    assert max_results.default == 50
    assert max_results.min_value == 1
    assert max_results.max_value == 200
    assert _field("CHOMIKUJ_BASE_URL").default == "https://mobile.chomikuj.pl"


def test_fields_required_markers():
    """Test fields required markers."""
    assert _field("CHOMIKUJ_USERNAME").required is True
    assert _field("CHOMIKUJ_PASSWORD").required is True


def test_fields_are_env_supported():
    """Test fields are env supported."""
    assert all(field.env_supported for field in _fields() if isinstance(field, FieldBase))


def test_fields_show_when_enabled():
    """Test fields show when enabled."""
    for key in EXPECTED_KEYS[1:]:
        assert _field(key).show_when == {"field": "CHOMIKUJ_ENABLED", "value": True}


def test_fields_registry_tab_metadata():
    """Test fields registry tab metadata."""
    tab = get_settings_tab("chomikuj_config")
    assert tab is not None
    assert tab.display_name == "Chomikuj.pl"
    assert tab.order == 48
    assert tab.icon == "download"
    assert [f.key for f in tab.fields] == EXPECTED_KEYS


def test_fields_have_no_format_or_language_key():
    """Test fields have no format or language key."""
    keys = [f.key for f in _fields()]
    assert not any("FORMAT" in key for key in keys)
    assert not any("LANGUAGE" in key for key in keys)


def test_fields_descriptions_warn_about_pdf_and_spending():
    """Test fields descriptions warn about pdf and spending."""
    enabled = _field("CHOMIKUJ_ENABLED")
    assert "PDF" in enabled.description
    assert "Supported Book Formats" in enabled.description
    spend = _field("CHOMIKUJ_ALLOW_TRANSFER_SPEND")
    assert "spend" in spend.description.lower()
    assert "transfer" in spend.description.lower()


def test_no_format_setting():
    """Test no format setting."""
    keys = {f.key for f in _fields()}
    assert "CHOMIKUJ_FORMATS" not in keys
    assert "CHOMIKUJ_FORMAT" not in keys
    assert "CHOMIKUJ_LANGUAGE" not in keys
    assert "CHOMIKUJ_AUDIOBOOK" not in keys


def test_password_masked():
    """Test password masked."""
    assert isinstance(_field("CHOMIKUJ_PASSWORD"), PasswordField)


def test_config_keys_unchanged_by_action():
    """Test config keys unchanged by action."""
    config_keys = [f.key for f in _fields() if isinstance(f, FieldBase)]
    assert config_keys == CONFIG_KEYS


def test_test_login_action_registered():
    """Test test login action registered."""
    action = _field(ACTION_KEY)
    assert isinstance(action, ActionButton)
    assert action.label == "Test Login"
    assert action.style == "primary"
    assert action.callback is _test_chomikuj_connection
    assert action.show_when == {"field": "CHOMIKUJ_ENABLED", "value": True}


def test_settings_module_imports_cleanly():
    """Test settings module imports cleanly."""
    module = importlib.import_module("shelfmark.release_sources.chomikuj.settings")
    assert callable(module._test_chomikuj_connection)
    assert callable(module._resolve_setting_text)


class _ConfigStub:
    """Minimal stand-in for the persisted config object."""

    def __init__(self, values: dict[str, object] | None = None) -> None:
        """Initialize the stub with a copy of the given values."""
        self.values = dict(values or {})

    def get(self, key: str, default: object = None) -> object:
        """Return the stored value for the key, or the default."""
        return self.values.get(key, default)


class _FakeChomikujClient:
    """Scripted client stub: records calls and never opens a socket."""

    instances: list[_FakeChomikujClient] = []
    account_payload: dict[str, object] = {"HasUnlimitedTransfer": True}
    login_error: Exception | None = None
    account_error: Exception | None = None
    next_account_error: Exception | None = None

    def __init__(self, base_url: object = None, **kwargs: object) -> None:
        """Initialize the fake client and register the instance."""
        self.base_url = base_url
        self.kwargs = kwargs
        self.username: str | None = None
        self.password: str | None = None
        self.api_key: str | None = None
        self.login_calls = 0
        self.account_calls = 0
        _FakeChomikujClient.instances.append(self)

    def login(self, username: str, password: str) -> dict[str, object]:
        """Record the credentials and return a synthetic API key."""
        self.login_calls += 1
        self.username = username
        self.password = password
        if _FakeChomikujClient.login_error is not None:
            raise _FakeChomikujClient.login_error
        return {
            "ApiKey": "synthetic-api-key",
            "AccountId": 1,
            "AccountName": username,
        }

    def account_info(self, api_key: str) -> dict[str, object]:
        """Record the API key and return the scripted account payload."""
        self.account_calls += 1
        self.api_key = api_key
        if _FakeChomikujClient.next_account_error is not None:
            error = _FakeChomikujClient.next_account_error
            _FakeChomikujClient.next_account_error = None
            raise error
        if _FakeChomikujClient.account_error is not None:
            raise _FakeChomikujClient.account_error
        return dict(_FakeChomikujClient.account_payload)


@pytest.fixture(autouse=True)
def _empty_session_cache():
    """Clear the session cache before and after the test."""
    chomikuj_session.clear_session()
    yield
    chomikuj_session.clear_session()


@pytest.fixture
def fake_client(monkeypatch):
    """Install the fake Chomikuj client and reset its scripted state."""
    _FakeChomikujClient.instances = []
    _FakeChomikujClient.account_payload = {"HasUnlimitedTransfer": True}
    _FakeChomikujClient.login_error = None
    _FakeChomikujClient.account_error = None
    _FakeChomikujClient.next_account_error = None
    monkeypatch.setattr(chomikuj_client, "ChomikujClient", _FakeChomikujClient)
    return _FakeChomikujClient


@pytest.fixture
def settings_config(monkeypatch):
    """Install the config stub and return an installer for values."""

    def install(values: dict[str, object] | None = None) -> _ConfigStub:
        """Install a config stub with the given values."""
        stub = _ConfigStub(values)
        monkeypatch.setattr("shelfmark.core.config.config", stub)
        return stub

    return install


def _credentials(overrides: dict[str, object] | None = None) -> dict[str, object]:
    """Return Chomikuj credentials with the given overrides."""
    values: dict[str, object] = {
        "CHOMIKUJ_USERNAME": USERNAME,
        "CHOMIKUJ_PASSWORD": PASSWORD,
        "CHOMIKUJ_BASE_URL": BASE_URL,
    }
    values.update(overrides or {})
    return values


def test_resolve_setting_text_prefers_form_then_config(settings_config):
    """Test resolve setting text prefers form then config."""
    settings_config({"CHOMIKUJ_USERNAME": "  stored-user  "})
    assert _resolve_setting_text({"CHOMIKUJ_USERNAME": "  form-user  "}, "CHOMIKUJ_USERNAME") == (
        "form-user"
    )
    assert _resolve_setting_text({"CHOMIKUJ_USERNAME": "   "}, "CHOMIKUJ_USERNAME") == "stored-user"
    assert _resolve_setting_text({}, "CHOMIKUJ_USERNAME") == "stored-user"
    assert _resolve_setting_text({}, "CHOMIKUJ_MISSING") == ""


def test_test_login_missing_credentials_returns_exact_failure_without_client(
    fake_client, settings_config
):
    """Test test login missing credentials returns exact failure without client."""
    expected = {"success": False, "message": "Username and password are required"}
    settings_config({})
    assert _test_chomikuj_connection() == expected
    assert _test_chomikuj_connection({}) == expected
    settings_config({"CHOMIKUJ_USERNAME": USERNAME})
    assert _test_chomikuj_connection() == expected
    settings_config({"CHOMIKUJ_PASSWORD": PASSWORD})
    assert _test_chomikuj_connection() == expected
    assert fake_client.instances == []


def test_test_login_success_unlimited(fake_client, settings_config):
    """Test test login success unlimited."""
    settings_config(_credentials())
    fake_client.account_payload = {"HasUnlimitedTransfer": True, "BalanceInfo": "1 GB"}

    result = _test_chomikuj_connection()

    assert result == {"success": True, "message": "Chomikuj.pl (unlimited transfer)"}
    instance = fake_client.instances[0]
    assert instance.base_url == NORMALIZED_BASE_URL
    assert instance.login_calls == 1
    assert instance.account_calls == 1
    assert instance.username == USERNAME
    assert instance.api_key == "synthetic-api-key"
    assert PASSWORD not in str(result)


def test_test_login_success_free_balance(fake_client, settings_config):
    """Test test login success free balance."""
    settings_config(_credentials())
    fake_client.account_payload = {"HasUnlimitedTransfer": False, "BalanceInfo": "1.5 GB"}

    result = _test_chomikuj_connection()

    assert result == {"success": True, "message": "Chomikuj.pl (free, 1.5 GB)"}
    assert PASSWORD not in str(result)


def test_test_login_success_free_transfer(fake_client, settings_config):
    """Test test login success free transfer."""
    settings_config(_credentials())
    fake_client.account_payload = {"HasUnlimitedTransfer": False, "Transfer": 1234}

    result = _test_chomikuj_connection()

    assert result["success"] is True
    assert "1234 kB transfer" in result["message"]
    assert result["message"].startswith("Chomikuj.pl (free, ")
    assert PASSWORD not in str(result)


def test_test_login_uses_current_form_values(fake_client, settings_config):
    """Test test login uses current form values."""
    settings_config(
        _credentials(
            {"CHOMIKUJ_USERNAME": "stored-user", "CHOMIKUJ_BASE_URL": "https://stored.test"}
        )
    )

    result = _test_chomikuj_connection(
        {
            "CHOMIKUJ_USERNAME": "form-user",
            "CHOMIKUJ_PASSWORD": "form-pass",
            "CHOMIKUJ_BASE_URL": "https://form.test/",
        }
    )

    assert result["success"] is True
    instance = fake_client.instances[0]
    assert instance.username == "form-user"
    assert instance.password == "form-pass"
    assert instance.base_url == "https://form.test/api/v3"


def test_test_login_failure_includes_error_and_no_secret(fake_client, settings_config):
    """Test test login failure includes error and no secret."""
    settings_config(_credentials())
    fake_client.login_error = ChomikujError("bad password")

    result = _test_chomikuj_connection()

    assert result == {"success": False, "message": "Login failed: bad password"}
    assert PASSWORD not in str(result)


def test_test_login_never_raises_or_echoes_unexpected_error(fake_client, settings_config):
    """Test test login never raises or echoes unexpected error."""
    settings_config(_credentials())
    fake_client.login_error = RuntimeError(f"boom {PASSWORD}")

    result = _test_chomikuj_connection()

    assert result["success"] is False
    assert PASSWORD not in str(result)


def test_test_login_unauthorized_clears_cached_session(fake_client, settings_config):
    """Test test login unauthorized clears cached session."""
    settings_config(_credentials())
    assert _test_chomikuj_connection()["success"] is True
    assert len(fake_client.instances) == 1
    assert fake_client.instances[0].login_calls == 1

    fake_client.next_account_error = ChomikujAuthError("expired api key")
    result = _test_chomikuj_connection()
    assert result == {"success": False, "message": "Login failed: expired api key"}
    rejected = fake_client.instances[1]
    assert rejected.login_calls == 0
    assert rejected.account_calls == 1

    assert _test_chomikuj_connection()["success"] is True
    assert fake_client.instances[2].login_calls == 1

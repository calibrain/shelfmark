"""Credential redaction on the Chomikuj runtime surfaces.

The client is already tested to keep the password and API key out of its own
exception messages (``test_client.py``). These tests cover the layers above it:

* :class:`SourceUnavailableError` masks credentials even when a source forwards
  a transport-style message that carries the request URL.
* A Chomikuj search failure reaches the caller through that redaction, with the
  configured password and the session's API key absent from the message.
* The settings "Test Login" action never returns the password or the API key in
  its success or failure dicts.

Only runtime code is used here; no live-script sanitizer or new redaction helper
is involved. Every test is offline.
"""

from unittest.mock import patch

import pytest

from shelfmark.core.redaction import redact_secrets
from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.chomikuj import client as chomikuj_client
from shelfmark.release_sources.chomikuj import session as chomikuj_session
from shelfmark.release_sources.chomikuj import settings as chomikuj_settings
from shelfmark.release_sources.chomikuj import source as chomikuj_source
from shelfmark.release_sources.chomikuj.client import ChomikujError
from shelfmark.release_sources.chomikuj.session import ChomikujSession

PASSWORD = "hunter2-configured-password"
API_KEY = "chomikuj-api-key-SECRET-0000"
USERNAME = "alice@example.invalid"
BASE_URL = "https://mobile.chomikuj.pl"


# --- SourceUnavailableError ------------------------------------------------


def test_source_unavailable_error_masks_a_token_query_parameter():
    """Test source unavailable error masks a token query parameter."""
    original = "https://x.test/a?token=SECRET"
    # Non-vacuous control: the secret really is in the input, so the assertion
    # below would fail without SourceUnavailableError's redaction.
    assert "SECRET" in original

    masked = str(SourceUnavailableError(original))

    assert "SECRET" not in masked
    assert "token=(removed)" in masked


def test_source_unavailable_error_masks_userinfo_and_api_key_parameters():
    """Test source unavailable error masks userinfo and api key parameters."""
    original = f"https://alice:{PASSWORD}@x.test/api?Api-Key={API_KEY}&Password={PASSWORD}"
    assert PASSWORD in original
    assert API_KEY in original

    masked = str(SourceUnavailableError(original))

    assert PASSWORD not in masked
    assert API_KEY not in masked
    assert "https://(removed)@x.test/api" in masked


def test_the_shared_redactor_leaves_a_plain_message_alone():
    """Test the shared redactor leaves a plain message alone."""
    message = "Chomikuj is unreachable"

    assert redact_secrets(message) == message
    assert str(SourceUnavailableError(message)) == message


# --- Source search failure -------------------------------------------------


def _book() -> BookMetadata:
    """Build the sample book metadata."""
    return BookMetadata(provider="test", provider_id="1", title="Krysař", authors=["Viktor Dyk"])


def _plan() -> ReleaseSearchPlan:
    """Build the sample search plan."""
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author="Viktor Dyk",
        title_variants=[ReleaseSearchVariant(title="Krysař", author="Viktor Dyk")],
        grouped_title_variants=[],
        manual_query=None,
    )


@pytest.fixture
def configured_source(monkeypatch):
    """Configure a Chomikuj source with test credentials."""
    values = {
        "CHOMIKUJ_ENABLED": True,
        "CHOMIKUJ_USERNAME": USERNAME,
        "CHOMIKUJ_PASSWORD": PASSWORD,
        "CHOMIKUJ_MAX_RESULTS": 50,
        "CHOMIKUJ_BASE_URL": BASE_URL,
    }
    monkeypatch.setattr(
        chomikuj_source.config,
        "get",
        lambda key, default=None: values.get(key, default),
    )
    monkeypatch.setattr(chomikuj_source, "get_supported_formats", lambda: ["epub"])
    return values


@pytest.mark.parametrize("kind", ["error", "unreachable", "throttled"])
def test_search_failure_never_surfaces_configured_password_or_api_key(configured_source, kind):
    """Test search failure never surfaces configured password or api key."""
    session = ChomikujSession(
        api_key=API_KEY,
        account_id=1,
        account_name="alice",
        fingerprint="synthetic-fingerprint",
    )
    # A transport-style message that carries the configured credentials in the
    # positions a request URL can expose them. The source forwards this text into
    # SourceUnavailableError, which must mask it.
    raw_message = (
        f"request failed for url: https://alice:{PASSWORD}@x.test/api"
        f"?Api-Key={API_KEY}&Password={PASSWORD}"
    )
    assert PASSWORD in raw_message
    assert API_KEY in raw_message
    error = ChomikujError(raw_message, kind=kind)

    with (
        patch.object(chomikuj_source, "ChomikujClient") as client_class,
        patch.object(chomikuj_source, "get_session", return_value=session),
        patch.object(chomikuj_source, "search_files", side_effect=error),
        pytest.raises(SourceUnavailableError) as info,
    ):
        chomikuj_source.ChomikujSource().search(_book(), _plan())

    surfaced = str(info.value)
    assert PASSWORD not in surfaced
    assert API_KEY not in surfaced
    assert "Password=(removed)" in surfaced
    assert "Api-Key=(removed)" in surfaced
    client_class.assert_called_once_with("https://mobile.chomikuj.pl/api/v3")


def test_unauthorized_search_failure_never_surfaces_credentials(configured_source):
    """Test unauthorized search failure never surfaces credentials."""
    session = ChomikujSession(
        api_key=API_KEY,
        account_id=1,
        account_name="alice",
        fingerprint="synthetic-fingerprint",
    )
    error = ChomikujError("invalid secret key", kind="unauthorized")
    assert PASSWORD not in str(error)
    assert API_KEY not in str(error)

    with (
        patch.object(chomikuj_source, "ChomikujClient"),
        patch.object(chomikuj_source, "get_session", return_value=session),
        patch.object(chomikuj_source, "search_files", side_effect=error),
        patch.object(chomikuj_source, "clear_session") as clear_session,
        pytest.raises(SourceUnavailableError) as info,
    ):
        chomikuj_source.ChomikujSource().search(_book(), _plan())

    surfaced = str(info.value)
    assert surfaced == "Chomikuj login failed: check your username and password"
    assert PASSWORD not in surfaced
    assert API_KEY not in surfaced
    clear_session.assert_called_once_with(
        username=USERNAME,
        password=PASSWORD,
        base_url="https://mobile.chomikuj.pl/api/v3",
    )


# --- Settings Test Login action -------------------------------------------


class _ConfigStub:
    """Minimal config stub with a fixed value map."""

    def __init__(self, values: dict[str, object]) -> None:
        """Initialize the stub with the given values."""
        self.values = values

    def get(self, key: str, default: object = None) -> object:
        """Return the stored value for the key, or the default."""
        return self.values.get(key, default)


class _FakeClient:
    """Login issues the API key; account_info either succeeds or fails."""

    instances: list[_FakeClient] = []
    account_error: Exception | None = None

    def __init__(self, base_url: object = None, **kwargs: object) -> None:
        """Initialize the fake client and register the instance."""
        self.base_url = base_url
        self.username: str | None = None
        self.password: str | None = None
        self.api_key: str | None = None
        _FakeClient.instances.append(self)

    def login(self, username: str, password: str) -> dict[str, object]:
        """Record the credentials and return a synthetic API key."""
        self.username = username
        self.password = password
        return {"ApiKey": API_KEY, "AccountId": 1, "AccountName": username}

    def account_info(self, api_key: str) -> dict[str, object]:
        """Return the scripted account payload or raise its error."""
        self.api_key = api_key
        if _FakeClient.account_error is not None:
            raise _FakeClient.account_error
        return {"HasUnlimitedTransfer": True}


@pytest.fixture
def settings_login(monkeypatch):
    """Install a fake client and config for settings tests."""
    _FakeClient.instances = []
    _FakeClient.account_error = None
    monkeypatch.setattr(chomikuj_client, "ChomikujClient", _FakeClient)
    monkeypatch.setattr(
        "shelfmark.core.config.config",
        _ConfigStub(
            {
                "CHOMIKUJ_USERNAME": USERNAME,
                "CHOMIKUJ_PASSWORD": PASSWORD,
                "CHOMIKUJ_BASE_URL": BASE_URL,
            }
        ),
    )
    chomikuj_session.clear_session()
    yield _FakeClient
    chomikuj_session.clear_session()


def test_test_login_success_dict_never_contains_password_or_api_key(settings_login):
    """Test test login success dict never contains password or api key."""
    result = chomikuj_settings._test_chomikuj_connection()

    assert result == {"success": True, "message": "Chomikuj.pl (unlimited transfer)"}
    assert PASSWORD not in str(result)
    assert API_KEY not in str(result)
    # Non-vacuous: both secrets really flowed through the runtime code.
    instance = settings_login.instances[-1]
    assert instance.password == PASSWORD
    assert instance.api_key == API_KEY


def test_test_login_failure_dict_never_contains_password_or_api_key(settings_login):
    """Test test login failure dict never contains password or api key."""
    settings_login.account_error = ChomikujError("account lookup failed")

    result = chomikuj_settings._test_chomikuj_connection()

    assert result["success"] is False
    assert PASSWORD not in str(result)
    assert API_KEY not in str(result)
    # The API key was issued and used before the failure, yet is not surfaced.
    instance = settings_login.instances[-1]
    assert instance.password == PASSWORD
    assert instance.api_key == API_KEY


def test_test_login_rejected_password_dict_never_contains_password(settings_login, monkeypatch):
    """Test test login rejected password dict never contains password."""

    class _RejectingClient(_FakeClient):
        """Fake client whose login always rejects the password."""

        def login(self, username: str, password: str) -> dict[str, object]:
            """Record the credentials and raise a login error."""
            self.username = username
            self.password = password
            raise ChomikujError("bad password")

    monkeypatch.setattr(chomikuj_client, "ChomikujClient", _RejectingClient)
    chomikuj_session.clear_session()

    result = chomikuj_settings._test_chomikuj_connection()

    assert result == {"success": False, "message": "Login failed: bad password"}
    assert PASSWORD not in str(result)
    assert API_KEY not in str(result)
    instance = _RejectingClient.instances[-1]
    assert instance.password == PASSWORD

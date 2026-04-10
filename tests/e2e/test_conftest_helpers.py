import pytest
from _pytest.outcomes import Failed

from tests.e2e import conftest as e2e_conftest


def test_require_authenticated_client_allows_public_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = e2e_conftest.APIClient(base_url="http://example.com")
    monkeypatch.setattr(
        e2e_conftest,
        "_get_auth_state",
        lambda _: {"auth_required": False},
    )

    try:
        assert e2e_conftest._require_authenticated_client(client) is client
    finally:
        client.session.close()


def test_require_authenticated_client_fails_without_env_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = e2e_conftest.APIClient(base_url="http://example.com")
    monkeypatch.setattr(
        e2e_conftest,
        "_get_auth_state",
        lambda _: {"auth_required": True, "authenticated": False},
    )
    monkeypatch.delenv(e2e_conftest.E2E_USERNAME_ENV, raising=False)
    monkeypatch.delenv(e2e_conftest.E2E_PASSWORD_ENV, raising=False)

    try:
        with pytest.raises(Failed, match="requires authentication"):
            e2e_conftest._require_authenticated_client(client)
    finally:
        client.session.close()


def test_require_authenticated_client_logs_in_when_credentials_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = e2e_conftest.APIClient(base_url="http://example.com")
    auth_states = iter(
        [
            {"auth_required": True, "authenticated": False},
            {"auth_required": True, "authenticated": True},
        ]
    )
    monkeypatch.setattr(e2e_conftest, "_get_auth_state", lambda _: next(auth_states))
    monkeypatch.setattr(e2e_conftest, "_login_with_env_credentials", lambda _: True)
    monkeypatch.setenv(e2e_conftest.E2E_USERNAME_ENV, "user")
    monkeypatch.setenv(e2e_conftest.E2E_PASSWORD_ENV, "password")

    try:
        assert e2e_conftest._require_authenticated_client(client) is client
    finally:
        client.session.close()

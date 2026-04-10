import pytest
from _pytest.outcomes import Failed, Skipped

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
        assert e2e_conftest._require_authenticated_client(client, strict=True) is client
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
            e2e_conftest._require_authenticated_client(client, strict=True)
    finally:
        client.session.close()


def test_require_authenticated_client_skips_without_env_credentials_when_not_strict(
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
        with pytest.raises(Skipped, match="requires authentication"):
            e2e_conftest._require_authenticated_client(client, strict=False)
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
        assert e2e_conftest._require_authenticated_client(client, strict=True) is client
    finally:
        client.session.close()


def test_is_explicit_e2e_run_detects_e2e_path_selection() -> None:
    assert e2e_conftest._is_explicit_e2e_run("", ["tests/e2e/"]) is True
    assert (
        e2e_conftest._is_explicit_e2e_run("", ["tests/e2e/test_api.py::TestConfigEndpoint"]) is True
    )


def test_is_explicit_e2e_run_detects_markexpr_selection() -> None:
    assert e2e_conftest._is_explicit_e2e_run("e2e", ["tests/"]) is True
    assert e2e_conftest._is_explicit_e2e_run("slow and e2e", ["tests/"]) is True


def test_is_explicit_e2e_run_treats_general_suite_as_non_strict() -> None:
    assert e2e_conftest._is_explicit_e2e_run("", ["tests/"]) is False
    assert e2e_conftest._is_explicit_e2e_run("not integration and not e2e", ["tests/"]) is False

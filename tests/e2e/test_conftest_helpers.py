import pytest
from _pytest.outcomes import Failed, Skipped

from tests.e2e import conftest as e2e_conftest


class DummyResponse:
    def __init__(self, status_code: int, payload: object, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or repr(payload)

    def json(self) -> object:
        return self._payload


class RaisingResponse(DummyResponse):
    def __init__(self, status_code: int, exc: Exception, text: str = "") -> None:
        super().__init__(status_code=status_code, payload=None, text=text)
        self._exc = exc

    def json(self) -> object:
        raise self._exc


def _make_client() -> e2e_conftest.APIClient:
    return e2e_conftest.APIClient(base_url="http://example.com")


def test_api_client_wait_for_health_retries_through_request_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    calls = {"count": 0}

    def fake_get(_path: str, **_kwargs: object) -> DummyResponse:
        calls["count"] += 1
        if calls["count"] == 1:
            raise e2e_conftest.requests.exceptions.ReadTimeout("timeout")
        return DummyResponse(200, {"status": "ok"})

    times = [0.0, 0.0, 0.1]

    monkeypatch.setattr(client, "get", fake_get)
    monkeypatch.setattr(e2e_conftest.time, "time", lambda: times.pop(0))
    monkeypatch.setattr(e2e_conftest.time, "sleep", lambda _seconds: None)

    try:
        assert client.wait_for_health(max_wait=1) is True
        assert calls["count"] == 2
    finally:
        client.close()


def test_download_tracker_wait_for_status_ignores_malformed_payloads_and_returns_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.responses = [
                DummyResponse(200, ["not", "a", "mapping"]),
                DummyResponse(200, {"error": {"task-1": {"message": "boom"}}}),
            ]

        def get(self, _path: str) -> DummyResponse:
            return self.responses.pop(0)

    tracker = e2e_conftest.DownloadTracker(client=FakeClient())
    times = [0.0, 0.0, 0.1]

    monkeypatch.setattr(e2e_conftest.time, "time", lambda: times.pop(0))
    monkeypatch.setattr(e2e_conftest.time, "sleep", lambda _seconds: None)

    result = tracker.wait_for_status("task-1", ["complete"], timeout=1)

    assert result == {"state": "error", "data": {"message": "boom"}}


def test_assert_json_object_returns_dict() -> None:
    response = DummyResponse(200, {"status": "ok"})

    assert e2e_conftest.assert_json_object(response, context="health") == {"status": "ok"}


def test_assert_json_object_rejects_non_object_payload() -> None:
    response = DummyResponse(200, ["not", "an", "object"])

    with pytest.raises(AssertionError, match="did not return a JSON object"):
        e2e_conftest.assert_json_object(response, context="health")


def test_assert_json_object_rejects_invalid_json() -> None:
    response = RaisingResponse(200, ValueError("bad json"))

    with pytest.raises(Failed, match="did not return valid JSON"):
        e2e_conftest.assert_json_object(response, context="health")


def test_assert_json_list_returns_list() -> None:
    response = DummyResponse(200, [1, 2, 3])

    assert e2e_conftest.assert_json_list(response, context="queue") == [1, 2, 3]


def test_assert_queue_order_response_validates_entries() -> None:
    response = DummyResponse(
        200,
        {
            "queue": [
                {
                    "id": "task-1",
                    "priority": 0,
                    "added_time": 12.5,
                    "status": "queued",
                }
            ]
        },
    )

    queue = e2e_conftest.assert_queue_order_response(response)
    assert queue[0]["id"] == "task-1"


def test_assert_queue_order_response_rejects_missing_entry_fields() -> None:
    response = DummyResponse(200, {"queue": [{"id": "task-1"}]})

    with pytest.raises(AssertionError):
        e2e_conftest.assert_queue_order_response(response)


def test_require_authenticated_client_allows_public_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    monkeypatch.setattr(e2e_conftest, "_get_auth_state", lambda _: {"auth_required": False})

    try:
        assert e2e_conftest._require_authenticated_client(client, strict=True) is client
    finally:
        client.close()


def test_require_authenticated_client_fails_when_auth_state_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    monkeypatch.setattr(e2e_conftest, "_get_auth_state", lambda _: None)

    try:
        with pytest.raises(Failed, match="Unable to read auth state"):
            e2e_conftest._require_authenticated_client(client, strict=True)
    finally:
        client.close()


def test_require_healthy_server_fails_in_strict_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    monkeypatch.setattr(e2e_conftest.APIClient, "wait_for_health", lambda self, max_wait=30: False)
    monkeypatch.setattr(e2e_conftest.APIClient, "close", lambda self: closed.append(self.base_url))

    with pytest.raises(Failed, match="Server not available"):
        e2e_conftest._require_healthy_server("http://example.com", strict=True)

    assert closed == ["http://example.com"]


def test_require_healthy_server_skips_in_non_strict_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(e2e_conftest.APIClient, "wait_for_health", lambda self, max_wait=30: False)

    with pytest.raises(Skipped, match="Server not available"):
        e2e_conftest._require_healthy_server("http://example.com", strict=False)


def test_require_authenticated_client_skips_when_auth_state_unavailable_and_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
    monkeypatch.setattr(e2e_conftest, "_get_auth_state", lambda _: None)

    try:
        with pytest.raises(Skipped, match="Unable to read auth state"):
            e2e_conftest._require_authenticated_client(client, strict=False)
    finally:
        client.close()


def test_require_authenticated_client_fails_without_env_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
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
        client.close()


def test_require_authenticated_client_skips_without_env_credentials_when_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
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
        client.close()


def test_require_authenticated_client_logs_in_when_credentials_are_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _make_client()
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
        client.close()


def test_is_explicit_e2e_run_detects_e2e_path_selection() -> None:
    assert e2e_conftest._is_explicit_e2e_run("", ["tests/e2e/"]) is True
    assert (
        e2e_conftest._is_explicit_e2e_run("", ["tests/e2e/test_api.py::TestConfigEndpoint"]) is True
    )


def test_is_explicit_e2e_run_detects_mixed_path_selection() -> None:
    assert (
        e2e_conftest._is_explicit_e2e_run(
            "",
            ["tests/e2e/test_api.py", "tests/core/test_user_db.py"],
        )
        is True
    )


def test_is_explicit_e2e_run_detects_markexpr_selection() -> None:
    assert e2e_conftest._is_explicit_e2e_run("e2e", ["tests/"]) is True
    assert e2e_conftest._is_explicit_e2e_run("slow and e2e", ["tests/"]) is True


def test_is_explicit_e2e_run_treats_general_suite_as_non_strict() -> None:
    assert e2e_conftest._is_explicit_e2e_run("", ["tests/"]) is False
    assert e2e_conftest._is_explicit_e2e_run("not integration and not e2e", ["tests/"]) is False

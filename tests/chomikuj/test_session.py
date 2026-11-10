"""Tests for the in-memory Chomikuj session cache.

Every login runs against a scripted fake client, so no socket is opened. The
fake counts calls and can block inside ``login`` so the concurrency tests pin
the in-flight dedupe and prove the cache lock is never held over the network
call.
"""

import hashlib
import logging
import threading

import pytest

from shelfmark.release_sources.chomikuj.client import ChomikujAuthError
from shelfmark.release_sources.chomikuj.session import (
    ChomikujSession,
    clear_session,
    get_session,
)

BASE_URL = "https://mobile.chomikuj.pl/api/v3"
OTHER_BASE_URL = "https://mirror.chomikuj.test/api/v3"
USERNAME = "alice@example.invalid"
OTHER_USERNAME = "bob@example.invalid"
PASSWORD = "s3cret-p@ssword"
OTHER_PASSWORD = "an0ther-p@ssword"


def _fingerprint(username: str, password: str, base_url: str) -> str:
    return hashlib.sha256(f"{username}\0{password}\0{base_url}".encode()).hexdigest()


class FakeClient:
    """Scripted ``login`` stub that records calls and can block or fail once."""

    def __init__(
        self,
        *,
        blocked: bool = False,
        failure: Exception | None = None,
        fail_first: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str]] = []
        self._calls_lock = threading.Lock()
        self.login_started = threading.Event()
        self.release = threading.Event()
        if not blocked:
            self.release.set()
        self.failure = failure
        self.fail_first = fail_first

    def login(self, username: str, password: str) -> dict[str, object]:
        with self._calls_lock:
            self.calls.append((username, password))
            attempt = len(self.calls)
        self.login_started.set()
        if not self.release.wait(5):
            msg = "test forgot to release the fake login"
            raise AssertionError(msg)
        if self.failure is not None and (not self.fail_first or attempt == 1):
            raise self.failure
        return {
            "ApiKey": f"synthetic-api-key-{attempt:04d}",
            "AccountId": 90_000_000 + attempt,
            "AccountName": username,
        }


@pytest.fixture(autouse=True)
def _empty_cache():
    clear_session()
    yield
    clear_session()


def _get(client, *, username=USERNAME, password=PASSWORD, base_url=BASE_URL):
    return get_session(client, username=username, password=password, base_url=base_url)


def _start(target) -> threading.Thread:
    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _join(thread: threading.Thread) -> None:
    thread.join(5)
    assert not thread.is_alive(), "worker thread did not finish"


def test_sequential_calls_reuse_one_login():
    client = FakeClient()

    first = _get(client)
    second = _get(client)

    assert len(client.calls) == 1
    assert second is first
    assert first == ChomikujSession(
        api_key="synthetic-api-key-0001",
        account_id=90_000_001,
        account_name=USERNAME,
        fingerprint=_fingerprint(USERNAME, PASSWORD, BASE_URL),
    )
    assert client.calls[0] == (USERNAME, PASSWORD)


def test_concurrent_calls_share_one_in_flight_login():
    client = FakeClient(blocked=True)
    entered = threading.Barrier(3)
    results: list[ChomikujSession] = []
    errors: list[BaseException] = []

    def worker() -> None:
        entered.wait()
        try:
            results.append(_get(client))
        except BaseException as exc:
            errors.append(exc)

    threads = [_start(worker), _start(worker)]
    entered.wait()
    assert client.login_started.wait(5)

    # The owner is blocked inside login; the waiter must be blocked on the
    # per-entry event instead of starting a second login.
    for thread in threads:
        thread.join(0.2)
        assert thread.is_alive(), "worker finished before the shared login was released"

    client.release.set()
    for thread in threads:
        _join(thread)

    assert errors == []
    assert len(client.calls) == 1
    assert len(results) == 2
    assert results[0] is results[1]


def test_fingerprint_change_password_forces_new_login():
    client = FakeClient()

    first = _get(client)
    second = _get(client, password=OTHER_PASSWORD)

    assert len(client.calls) == 2
    assert client.calls[1] == (USERNAME, OTHER_PASSWORD)
    assert second is not first
    assert second.fingerprint == _fingerprint(USERNAME, OTHER_PASSWORD, BASE_URL)


def test_fingerprint_change_base_url_forces_new_login():
    client = FakeClient()

    first = _get(client)
    second = _get(client, base_url=OTHER_BASE_URL)

    assert len(client.calls) == 2
    assert second is not first
    assert second.fingerprint == _fingerprint(USERNAME, PASSWORD, OTHER_BASE_URL)


def test_rejected_not_cached_and_next_call_retries():
    failure = ChomikujAuthError("rejected")
    client = FakeClient(failure=failure, fail_first=True)

    with pytest.raises(ChomikujAuthError) as exc_info:
        _get(client)

    assert exc_info.value is failure
    assert len(client.calls) == 1

    session = _get(client)

    assert len(client.calls) == 2
    assert session.api_key == "synthetic-api-key-0002"
    assert _get(client) is session
    assert len(client.calls) == 2


def test_failed_login_with_concurrent_credential_change_keeps_newer_entry():
    failure = ChomikujAuthError("rejected")
    old_client = FakeClient(blocked=True, failure=failure)
    new_client = FakeClient()
    errors: list[BaseException] = []

    def old_worker() -> None:
        try:
            _get(old_client)
        except BaseException as exc:
            errors.append(exc)

    thread = _start(old_worker)
    assert old_client.login_started.wait(5)

    newer = _get(new_client, password=OTHER_PASSWORD)
    old_client.release.set()
    _join(thread)

    assert errors == [failure]
    assert _get(new_client, password=OTHER_PASSWORD) is newer
    assert len(new_client.calls) == 1


def test_failed_login_does_not_evict_replacement_entry_for_same_credentials():
    failure = ChomikujAuthError("rejected")
    old_client = FakeClient(blocked=True, failure=failure)
    new_client = FakeClient()
    errors: list[BaseException] = []

    def old_worker() -> None:
        try:
            _get(old_client)
        except BaseException as exc:
            errors.append(exc)

    thread = _start(old_worker)
    assert old_client.login_started.wait(5)

    # Evict the in-flight entry and replace it with a newer one for the exact
    # same fingerprint before the old login rejects.
    clear_session(USERNAME, PASSWORD, BASE_URL)
    newer = _get(new_client)
    old_client.release.set()
    _join(thread)

    assert errors == [failure]
    assert _get(new_client) is newer
    assert len(new_client.calls) == 1


def test_concurrent_waiters_reraises_the_same_login_failure():
    failure = ChomikujAuthError("rejected")
    client = FakeClient(blocked=True, failure=failure)
    entered = threading.Barrier(3)
    errors: list[BaseException] = []

    def worker() -> None:
        entered.wait()
        try:
            _get(client)
        except BaseException as exc:
            errors.append(exc)

    threads = [_start(worker), _start(worker)]
    entered.wait()
    assert client.login_started.wait(5)

    for thread in threads:
        thread.join(0.2)
        assert thread.is_alive(), "worker finished before the shared login was released"

    client.release.set()
    for thread in threads:
        _join(thread)

    assert len(client.calls) == 1
    assert len(errors) == 2
    assert errors[0] is failure
    assert errors[1] is failure


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("", PASSWORD),
        ("   ", PASSWORD),
        (USERNAME, ""),
    ],
)
def test_empty_credentials_raise_without_calling_login(username, password):
    client = FakeClient()

    with pytest.raises(ChomikujAuthError) as exc_info:
        _get(client, username=username, password=password)

    assert exc_info.value.kind == "unauthorized"
    assert client.calls == []


def test_clear_session_without_arguments_clears_everything():
    client = FakeClient()

    first = _get(client)
    second = _get(client, username=OTHER_USERNAME)

    clear_session()

    assert _get(client) is not first
    assert _get(client, username=OTHER_USERNAME) is not second
    assert len(client.calls) == 4


def test_clear_session_clears_only_the_matching_entry():
    client = FakeClient()

    first = _get(client)
    other = _get(client, base_url=OTHER_BASE_URL)

    clear_session(USERNAME, PASSWORD, BASE_URL)

    assert _get(client, base_url=OTHER_BASE_URL) is other
    assert len(client.calls) == 2
    assert _get(client) is not first
    assert len(client.calls) == 3


def test_cache_lock_is_not_held_during_login():
    client = FakeClient(blocked=True)

    worker = _start(lambda: _get(client))
    assert client.login_started.wait(5)

    cleared = threading.Event()

    def clear() -> None:
        clear_session()
        cleared.set()

    clearer = _start(clear)

    assert cleared.wait(1), "clear_session blocked on the in-flight login"
    _join(clearer)

    client.release.set()
    _join(worker)


def test_session_repr_does_not_expose_the_password():
    client = FakeClient()

    session = _get(client)

    assert PASSWORD not in repr(session)
    assert session.api_key == "synthetic-api-key-0001"


def test_session_writes_no_logs(caplog):
    caplog.set_level(logging.DEBUG)
    client = FakeClient()

    _get(client)

    assert caplog.records == []

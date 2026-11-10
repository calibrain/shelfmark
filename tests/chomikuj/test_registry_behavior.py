"""Registry availability and content-type gating for the Chomikuj source.

These tests go through the public registry (``list_available_sources`` and
``get_source``) with the config singleton patched and every network boundary
replaced by a spy. They prove three things the source promises:

* availability is config-only - disabled by default, enabled only when
  ``CHOMIKUJ_ENABLED`` is the literal ``True`` and both credentials are set,
  and never enabled by a truthy string such as ``"true"``;
* availability and a non-ebook content type never construct a
  ``ChomikujClient`` and never call ``login``/``get_session``;
* the source advertises ebook-only search results and stays out of the browse
  path (``search_results_are_releases()`` is ``False``).

No test here performs a login or opens a socket.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import get_source, list_available_sources
from shelfmark.release_sources.chomikuj import source as chomikuj_source

# Credentials are present on purpose: "disabled by default" must mean the
# flag alone, not the absence of credentials.
_CONFIG = {
    "CHOMIKUJ_USERNAME": "alice",
    "CHOMIKUJ_PASSWORD": "synthetic-password",
}


@pytest.fixture
def config_values():
    values = dict(_CONFIG)
    with patch.object(
        chomikuj_source.config,
        "get",
        side_effect=lambda key, default=None: values.get(key, default),
    ):
        yield values


@pytest.fixture(autouse=True)
def _block_network():
    """Any real HTTP request is a test failure, not a slow test."""
    with patch(
        "requests.Session.request",
        side_effect=AssertionError("network access during registry behavior test"),
    ):
        yield


def _book() -> BookMetadata:
    return BookMetadata(provider="test", provider_id="1", title="Krysař", authors=["Viktor Dyk"])


def _plan() -> ReleaseSearchPlan:
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=[],
        author="Viktor Dyk",
        title_variants=[ReleaseSearchVariant(title="Krysař", author="Viktor Dyk")],
        grouped_title_variants=[],
    )


def _chomikuj_row() -> dict:
    rows = [row for row in list_available_sources() if row["name"] == "chomikuj"]
    assert len(rows) == 1, "chomikuj must appear exactly once in the source registry"
    return rows[0]


def _spies():
    """Build a client class spy whose instances would record ``login`` calls."""
    login_spy = MagicMock(name="login")
    client_cls = MagicMock(name="ChomikujClient")
    client_cls.return_value.login = login_spy
    return client_cls, login_spy


def test_enabled_default_off_with_credentials_present(config_values):
    assert _chomikuj_row()["enabled"] is False


@pytest.mark.parametrize("flag", ["true", "True", 1])
def test_string_true_is_off(config_values, flag):
    config_values["CHOMIKUJ_ENABLED"] = flag
    assert _chomikuj_row()["enabled"] is False


@pytest.mark.parametrize("flag", [False, None])
def test_disabled_flag_is_off(config_values, flag):
    config_values["CHOMIKUJ_ENABLED"] = flag
    assert _chomikuj_row()["enabled"] is False


def test_enabled_with_literal_true_and_both_credentials(config_values):
    config_values["CHOMIKUJ_ENABLED"] = True
    assert _chomikuj_row()["enabled"] is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"CHOMIKUJ_USERNAME": ""},
        {"CHOMIKUJ_USERNAME": "   "},
        {"CHOMIKUJ_PASSWORD": ""},
    ],
)
def test_enabled_requires_both_credentials(config_values, overrides):
    config_values["CHOMIKUJ_ENABLED"] = True
    config_values.update(overrides)
    assert _chomikuj_row()["enabled"] is False


def test_registry_availability_never_constructs_a_client_or_logs_in(config_values):
    config_values["CHOMIKUJ_ENABLED"] = True
    client_cls, login_spy = _spies()
    session_spy = MagicMock(name="get_session")

    with (
        patch.object(chomikuj_source, "ChomikujClient", client_cls),
        patch.object(chomikuj_source, "get_session", session_spy),
    ):
        row = _chomikuj_row()

    assert row["enabled"] is True
    client_cls.assert_not_called()
    session_spy.assert_not_called()
    login_spy.assert_not_called()


def test_registry_row_declares_ebook_only(config_values):
    row = _chomikuj_row()
    assert row["supported_content_types"] == ["ebook"]
    assert row["browse_results_are_releases"] is False
    assert row["can_be_default"] is True


def test_get_source_reports_ebook_only_release_semantics(config_values):
    sourced = get_source("chomikuj")
    assert sourced.supported_content_types == ["ebook"]
    assert sourced.search_results_are_releases() is False


def test_audiobook_no_client_or_login(config_values):
    config_values["CHOMIKUJ_ENABLED"] = True
    client_cls, login_spy = _spies()
    session_spy = MagicMock(name="get_session")
    search_spy = MagicMock(name="search_files")

    with (
        patch.object(chomikuj_source, "ChomikujClient", client_cls),
        patch.object(chomikuj_source, "get_session", session_spy),
        patch.object(chomikuj_source, "search_files", search_spy),
    ):
        releases = chomikuj_source.ChomikujSource().search(
            _book(), _plan(), content_type="audiobook"
        )

    assert releases == []
    client_cls.assert_not_called()
    session_spy.assert_not_called()
    login_spy.assert_not_called()
    search_spy.assert_not_called()

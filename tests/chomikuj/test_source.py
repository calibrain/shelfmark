"""Tests for ChomikujSource: availability, candidates, orchestration, mapping, columns.

Most tests patch ``ChomikujClient``, ``get_session``, ``search_files``, and
``get_supported_formats``, so nothing here opens a socket or reads real config.
The deadline integration tests keep the real ``search_files`` and patch only the
client, session, and config boundary. Rows are plain dicts shaped like the live
``files/search`` response.
"""

import copy
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from shelfmark.core.models import SearchFilters
from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import (
    ColumnColorHint,
    ColumnRenderType,
    ReleaseProtocol,
    SourceUnavailableError,
    serialize_column_config,
)
from shelfmark.release_sources.chomikuj import source as chomikuj_source
from shelfmark.release_sources.chomikuj.client import DEFAULT_BASE_URL, ChomikujError
from shelfmark.release_sources.chomikuj.session import ChomikujSession

_MISSING = object()

_SESSION = ChomikujSession(
    api_key="synthetic-api-key-source-0000-0000-000000000000",
    account_id=1,
    account_name="alice",
    fingerprint="synthetic-fingerprint",
)

_CONFIG = {
    "CHOMIKUJ_ENABLED": True,
    "CHOMIKUJ_USERNAME": " user ",
    "CHOMIKUJ_PASSWORD": "pw",
    "CHOMIKUJ_MAX_RESULTS": 50,
    "CHOMIKUJ_BASE_URL": "https://mobile.chomikuj.pl",
}

_SUPPORTED_FORMATS = ["epub", "mobi", "pdf", "cbz"]


def _book(title="Krysař"):
    return BookMetadata(provider="test", provider_id="1", title=title, authors=["Viktor Dyk"])


def _plan(
    *,
    manual_query=None,
    variants=None,
    isbn=None,
    filters=None,
):
    if variants is None:
        variants = [ReleaseSearchVariant(title="Krysař", author="Viktor Dyk")]
    return ReleaseSearchPlan(
        languages=None,
        isbn_candidates=list(isbn or []),
        author="Viktor Dyk",
        title_variants=list(variants),
        grouped_title_variants=[],
        manual_query=manual_query,
        source_filters=filters,
    )


def _row(
    file_id="7",
    name="Dyk Viktor - Krysar.epub",
    file_type="epub",
    *,
    size=200,
    free=True,
):
    row = {"FileId": file_id, "FileName": name, "FileType": file_type}
    if size is not _MISSING:
        row["Size"] = size
    if free is not _MISSING:
        row["IsFileFreeForUser"] = free
    return row


class ClientRecorder:
    """Records every ``ChomikujClient`` construction instead of making a session."""

    instances: list[ClientRecorder] = []

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.login_calls: list[tuple[str, str]] = []
        ClientRecorder.instances.append(self)

    def login(self, username: str, password: str) -> dict[str, str]:
        self.login_calls.append((username, password))
        return {"ApiKey": "unused"}


@contextmanager
def _patched(rows_by_query):
    """Patch the network boundary and record each candidate search."""
    calls: list[dict[str, object]] = []
    ClientRecorder.instances = []

    def fake_search_files(client, api_key, query, **kwargs):
        calls.append({"client": client, "api_key": api_key, "query": query, **kwargs})
        return rows_by_query.get(query, [])

    with (
        patch.object(chomikuj_source, "ChomikujClient", ClientRecorder),
        patch.object(chomikuj_source, "get_session", return_value=_SESSION),
        patch.object(chomikuj_source, "search_files", side_effect=fake_search_files),
    ):
        yield calls


@pytest.fixture
def config_values():
    values = dict(_CONFIG)
    supported = list(_SUPPORTED_FORMATS)
    with (
        patch.object(
            chomikuj_source.config,
            "get",
            side_effect=lambda key, default=None: values.get(key, default),
        ),
        patch.object(chomikuj_source, "get_supported_formats", side_effect=lambda: list(supported)),
    ):
        values["_supported"] = supported
        yield values


# --- Todo 12: registration, availability, helpers -------------------------


def test_source_is_registered_with_expected_attributes():
    from shelfmark.release_sources import get_source

    sourced = get_source("chomikuj")
    assert sourced.name == "chomikuj"
    assert sourced.display_name == "Chomikuj.pl"
    assert sourced.supported_content_types == ["ebook"]
    assert sourced.can_be_default is True
    assert sourced.search_results_are_releases() is False


def test_class_attributes_are_declared():
    assert chomikuj_source.ChomikujSource.name == "chomikuj"
    assert chomikuj_source.ChomikujSource.display_name == "Chomikuj.pl"
    assert chomikuj_source.ChomikujSource.supported_content_types == ["ebook"]
    assert chomikuj_source.ChomikujSource.can_be_default is True


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"CHOMIKUJ_ENABLED": False}, False),
        ({"CHOMIKUJ_ENABLED": "true"}, False),
        ({"CHOMIKUJ_USERNAME": ""}, False),
        ({"CHOMIKUJ_USERNAME": "   "}, False),
        ({"CHOMIKUJ_PASSWORD": ""}, False),
    ],
)
def test_is_available_is_config_only(config_values, overrides, expected):
    config_values.update(overrides)
    assert chomikuj_source.ChomikujSource().is_available() is expected


def test_is_available_never_constructs_a_client_or_logs_in(config_values):
    with (
        patch.object(
            chomikuj_source, "ChomikujClient", side_effect=AssertionError("network access")
        ),
        patch.object(chomikuj_source, "get_session", side_effect=AssertionError("login")),
    ):
        assert chomikuj_source.ChomikujSource().is_available() is True


def test_get_credentials_strips_username_but_not_password(config_values):
    config_values["CHOMIKUJ_USERNAME"] = " alice "
    config_values["CHOMIKUJ_PASSWORD"] = " pw "
    assert chomikuj_source.get_credentials() == ("alice", " pw ")


def test_get_credentials_rejects_non_strings(config_values):
    config_values["CHOMIKUJ_USERNAME"] = None
    config_values["CHOMIKUJ_PASSWORD"] = 123
    assert chomikuj_source.get_credentials() == ("", "")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (_MISSING, 50),
        (0, 1),
        (-10, 1),
        (25, 25),
        ("30", 30),
        (999, 200),
        ("nope", 50),
        (True, 50),
        (None, 50),
    ],
)
def test_get_max_results_defaults_and_clamps(config_values, value, expected):
    if value is _MISSING:
        config_values.pop("CHOMIKUJ_MAX_RESULTS")
    else:
        config_values["CHOMIKUJ_MAX_RESULTS"] = value
    assert chomikuj_source.get_max_results() == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", DEFAULT_BASE_URL),
        (None, DEFAULT_BASE_URL),
        ("https://x.test/", "https://x.test/api/v3"),
        ("https://x.test/api/v3/", "https://x.test/api/v3"),
    ],
)
def test_get_base_url_is_normalized(config_values, value, expected):
    config_values["CHOMIKUJ_BASE_URL"] = value
    assert chomikuj_source.get_base_url() == expected


def test_get_allowed_formats_intersects_supported_and_importable(config_values):
    assert chomikuj_source.get_allowed_formats() == {"epub", "mobi", "pdf"}


def test_get_allowed_formats_never_includes_archives(config_values):
    config_values["_supported"][:] = ["epub", "cbz", "cbr", "zip"]
    assert chomikuj_source.get_allowed_formats() == {"epub"}


def test_get_allowed_formats_narrows_with_source_filters(config_values):
    filters = SearchFilters(format=[".PDF", " EPUB ", "cbz", "zip"])
    assert chomikuj_source.get_allowed_formats(filters) == {"epub", "pdf"}


def test_get_allowed_formats_ignores_empty_filter_list(config_values):
    filters = SearchFilters(format=[])
    assert chomikuj_source.get_allowed_formats(filters) == {"epub", "mobi", "pdf"}


def test_get_allowed_formats_empty_when_nothing_importable_is_supported(config_values):
    config_values["_supported"][:] = ["cbz", "cbr"]
    assert chomikuj_source.get_allowed_formats() == set()


# --- Todo 12: query candidates --------------------------------------------


def test_manual_query_wins_verbatim(config_values):
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(), _plan(manual_query="  dyk  krysar "))
    assert [call["query"] for call in calls] == ["dyk  krysar"]


def test_candidates_title_author_then_title_only_fallback(config_values):
    rows = {"Krysař": [_row()]}
    with _patched(rows) as calls:
        releases = chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert [call["query"] for call in calls] == ["Krysař Viktor Dyk", "Krysař"]
    assert [release.source_id for release in releases] == ["chomikuj:7"]


def test_title_and_author_queries_are_deduplicated(config_values):
    variants = [ReleaseSearchVariant(title="Krysař", author="")]
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(), _plan(variants=variants))
    assert [call["query"] for call in calls] == ["Krysař"]


def test_isbn_is_used_when_plan_has_no_title_or_author(config_values):
    plan = _plan(variants=[], isbn=["9788070000000"])
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(), plan)
    assert [call["query"] for call in calls] == ["9788070000000"]


def test_book_title_is_used_when_plan_is_empty(config_values):
    plan = _plan(variants=[], isbn=[])
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(title="  Fallback Title "), plan)
    assert [call["query"] for call in calls] == ["Fallback Title"]


# --- Todo 12: orchestration and guards ------------------------------------


def test_disabled_no_client_returns_empty(config_values):
    config_values["CHOMIKUJ_ENABLED"] = False
    with (
        patch.object(chomikuj_source, "ChomikujClient") as client_cls,
        patch.object(chomikuj_source, "get_session") as get_session,
        patch.object(chomikuj_source, "search_files") as search_files,
    ):
        assert chomikuj_source.ChomikujSource().search(_book(), _plan()) == []
    client_cls.assert_not_called()
    get_session.assert_not_called()
    search_files.assert_not_called()


def test_unsupported_content_type_returns_empty(config_values):
    with (
        patch.object(chomikuj_source, "ChomikujClient") as client_cls,
        patch.object(chomikuj_source, "search_files") as search_files,
    ):
        assert (
            chomikuj_source.ChomikujSource().search(_book(), _plan(), content_type="audiobook")
            == []
        )
    client_cls.assert_not_called()
    search_files.assert_not_called()


def test_empty_allowed_set_returns_empty_without_client(config_values):
    config_values["_supported"][:] = ["cbz"]
    with (
        patch.object(chomikuj_source, "ChomikujClient") as client_cls,
        patch.object(chomikuj_source, "search_files") as search_files,
    ):
        assert chomikuj_source.ChomikujSource().search(_book(), _plan()) == []
    client_cls.assert_not_called()
    search_files.assert_not_called()


def test_client_uses_the_normalized_base_url(config_values):
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert calls
    assert calls[0]["client"].base_url == "https://mobile.chomikuj.pl/api/v3"
    assert calls[0]["api_key"] == _SESSION.api_key


def test_orchestration_passes_limit_and_default_budget(config_values):
    config_values["CHOMIKUJ_MAX_RESULTS"] = 20
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert calls[0]["allowed_formats"] == {"epub", "mobi", "pdf"}
    assert calls[0]["limit"] == 20
    assert calls[0]["budget_seconds"] == 12.0
    assert calls[0]["max_pages"] == 3


def test_expand_search_passes_twenty_seconds_and_five_pages(config_values):
    with _patched({}) as calls:
        chomikuj_source.ChomikujSource().search(_book(), _plan(), expand_search=True)
    assert calls[0]["budget_seconds"] == 20.0
    assert calls[0]["max_pages"] == 5


def test_search_caps_with_the_current_deadline(config_values):
    from shelfmark.core.search_deadline import search_deadline

    with _patched({}) as calls, search_deadline(0.5):
        chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert calls
    deadline = calls[0]["deadline"]
    assert deadline is not None
    assert deadline.budget_seconds == 0.5


def test_expired_deadline_returns_empty_without_error(config_values):
    from shelfmark.core.search_deadline import search_deadline

    with _patched({}) as calls, search_deadline(0.0):
        assert chomikuj_source.ChomikujSource().search(_book(), _plan()) == []
    assert calls == []
    assert ClientRecorder.instances == []


# --- Todo 28: source-level deadline integration ---------------------------


def test_deadline_expiry_during_first_page_returns_the_collected_page(config_values):
    """The real ``search_files`` loop stops paging and returns page 1's rows.

    The fake client sleeps past the 0.05 s budget and advertises a next page, so
    only the ambient deadline can prevent the second request. No deadline error
    may escape: the collected rows still become releases.
    """
    from shelfmark.core.search_deadline import search_deadline

    calls: list[dict[str, object]] = []

    class SlowPageClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def search_page(self, api_key, *, query, page, extension=None, timeout=None):
            calls.append(
                {
                    "api_key": api_key,
                    "query": query,
                    "page": page,
                    "extension": extension,
                    "timeout": timeout,
                }
            )
            time.sleep(0.1)
            return {"Results": [_row()], "IsNextPageAvailable": True}

    with (
        patch.object(chomikuj_source, "ChomikujClient", SlowPageClient),
        patch.object(chomikuj_source, "get_session", return_value=_SESSION),
        search_deadline(0.05),
    ):
        releases = chomikuj_source.ChomikujSource().search(_book(), _plan())

    assert len(calls) == 1
    assert calls[0]["query"] == "Krysař Viktor Dyk"
    assert calls[0]["page"] == 1
    assert calls[0]["timeout"] <= 0.05
    assert [release.source_id for release in releases] == ["chomikuj:7"]


def test_deadline_expired_no_error_returns_empty_without_a_request(config_values):
    """An already-expired ambient deadline returns ``[]`` before any client work."""
    from shelfmark.core.search_deadline import search_deadline

    constructed: list[str] = []
    calls: list[dict[str, object]] = []

    class RecordingClient:
        def __init__(self, base_url: str) -> None:
            constructed.append(base_url)

        def search_page(self, api_key, *, query, page, extension=None, timeout=None):
            calls.append({"query": query, "page": page})
            return {"Results": [_row()], "IsNextPageAvailable": True}

    with (
        patch.object(chomikuj_source, "ChomikujClient", RecordingClient),
        patch.object(chomikuj_source, "get_session", return_value=_SESSION),
        search_deadline(0.05),
    ):
        time.sleep(0.1)
        releases = chomikuj_source.ChomikujSource().search(_book(), _plan())

    assert releases == []
    assert constructed == []
    assert calls == []


def test_first_candidate_with_selectable_rows_wins(config_values):
    rows = {
        "Krysař Viktor Dyk": [_row(name="Krysar.cbz", file_type="cbz")],
        "Krysař": [_row()],
    }
    with _patched(rows) as calls:
        releases = chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert [call["query"] for call in calls] == ["Krysař Viktor Dyk", "Krysař"]
    assert [release.source_id for release in releases] == ["chomikuj:7"]


def test_non_numeric_file_id_rows_are_dropped(config_values):
    rows = {"Krysař Viktor Dyk": [_row(file_id="not-a-number")], "Krysař": []}
    with _patched(rows):
        assert chomikuj_source.ChomikujSource().search(_book(), _plan()) == []


def test_results_are_ordered_newest_first_and_limited(config_values):
    config_values["CHOMIKUJ_MAX_RESULTS"] = 2
    rows = {
        "Krysař Viktor Dyk": [
            _row(file_id="9", name="Krysar nine.epub"),
            _row(file_id="100", name="Krysar hundred.epub"),
            _row(file_id="10", name="Krysar ten.epub"),
        ]
    }
    with _patched(rows):
        releases = chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert [release.source_id for release in releases] == ["chomikuj:100", "chomikuj:10"]


# --- Todo 12: error mapping ------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("unreachable", "Chomikuj is unreachable: boom"),
        ("throttled", "Chomikuj is unreachable: boom"),
        ("error", "Chomikuj search failed: boom"),
    ],
)
def test_error_becomes_unavailable(config_values, kind, message):
    def fail(*args, **kwargs):
        raise ChomikujError("boom", kind=kind)

    with (
        patch.object(chomikuj_source, "ChomikujClient", ClientRecorder),
        patch.object(chomikuj_source, "get_session", return_value=_SESSION),
        patch.object(chomikuj_source, "search_files", side_effect=fail),
        pytest.raises(SourceUnavailableError) as info,
    ):
        chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert str(info.value) == message


def test_unauthorized_maps_to_credentials_message_and_clears_session(config_values):
    def fail(*args, **kwargs):
        raise ChomikujError("boom", kind="unauthorized")

    with (
        patch.object(chomikuj_source, "ChomikujClient", ClientRecorder),
        patch.object(chomikuj_source, "get_session", return_value=_SESSION),
        patch.object(chomikuj_source, "search_files", side_effect=fail),
        patch.object(chomikuj_source, "clear_session") as clear,
        pytest.raises(SourceUnavailableError) as info,
    ):
        chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert str(info.value) == "Chomikuj login failed: check your username and password"
    clear.assert_called_once_with(
        username="user", password="pw", base_url="https://mobile.chomikuj.pl/api/v3"
    )


def test_unauthorized_login_error_maps_and_clears_session(config_values):
    from shelfmark.release_sources.chomikuj.client import ChomikujAuthError

    with (
        patch.object(chomikuj_source, "ChomikujClient", ClientRecorder),
        patch.object(
            chomikuj_source,
            "get_session",
            side_effect=ChomikujAuthError("invalid secret key"),
        ),
        patch.object(chomikuj_source, "search_files") as search_files,
        patch.object(chomikuj_source, "clear_session") as clear,
        pytest.raises(SourceUnavailableError) as info,
    ):
        chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert str(info.value) == "Chomikuj login failed: check your username and password"
    clear.assert_called_once()
    search_files.assert_not_called()


# --- Todo 13: row -> Release mapping ---------------------------------------


def test_mapping_free_epub_row(config_values):
    release = chomikuj_source.to_release(
        _row(name="Dyk Viktor - Krysar.epub", file_type="epub", size=200, free=True),
        allowed_formats={"epub"},
    )
    assert release is not None
    assert release.source == "chomikuj"
    assert release.source_id == "chomikuj:7"
    assert release.title == "Krysar"
    assert release.format == "epub"
    assert release.language is None
    assert release.size == "200.0 KB"
    assert release.size_bytes == 204800
    assert release.download_url is None
    assert release.info_url is None
    assert release.protocol is ReleaseProtocol.HTTP
    assert release.indexer == "Chomikuj.pl"
    assert release.content_type == "ebook"
    assert release.seeders is None
    assert release.peers is None
    assert release.extra == {
        "file_id": "7",
        "file_name": "Dyk Viktor - Krysar.epub",
        "file_type": "epub",
        "is_free_for_user": True,
        "freeleech": True,
        "author": "Dyk Viktor",
        "language": None,
        "access": "free",
    }
    assert "size_bytes" not in release.extra
    assert "date" not in release.extra
    assert "year" not in release.extra


def test_mapping_paid_missing_flag(config_values):
    release = chomikuj_source.to_release(_row(free=_MISSING), allowed_formats={"epub"})
    assert release is not None
    assert release.extra["is_free_for_user"] is False
    assert release.extra["freeleech"] is False
    assert release.extra["access"] == "paid"


def test_mapping_explicit_false_free_flag_is_paid(config_values):
    release = chomikuj_source.to_release(_row(free=False), allowed_formats={"epub"})
    assert release is not None
    assert release.extra["freeleech"] is False
    assert release.extra["access"] == "paid"


def test_mapping_language_and_author_from_file_name(config_values):
    release = chomikuj_source.to_release(_row(name="Ksiazka (PL).epub"), allowed_formats={"epub"})
    assert release is not None
    assert release.title == "Ksiazka"
    assert release.language == "pl"
    assert release.extra["language"] == "pl"
    assert release.extra["author"] is None


def test_mapping_title_falls_back_to_the_file_name(config_values):
    release = chomikuj_source.to_release(
        _row(name="[PL].epub", file_type="epub"), allowed_formats={"epub"}
    )
    assert release is not None
    assert release.title == "[PL].epub"


def test_mapping_extra_keeps_author_and_language_when_none(config_values):
    release = chomikuj_source.to_release(
        _row(name="NoSeparator.epub", file_type="epub"), allowed_formats={"epub"}
    )
    assert release is not None
    assert "author" in release.extra
    assert "language" in release.extra
    assert release.extra["author"] is None
    assert release.extra["language"] is None


@pytest.mark.parametrize(
    ("size", "expected_bytes"),
    [
        (200, 204800),
        (200.9, 204800),
        ("200", 204800),
        (" 200 ", 204800),
        (0, None),
        ("0", None),
        (-5, None),
        (None, None),
        ("", None),
        ("abc", None),
        (True, None),
        (_MISSING, None),
    ],
)
def test_mapping_unknown_size_and_size_kb_cases(config_values, size, expected_bytes):
    release = chomikuj_source.to_release(_row(size=size), allowed_formats={"epub"})
    assert release is not None
    assert release.size_bytes == expected_bytes
    assert (release.size is None) is (expected_bytes is None)


def test_format_size_is_human_readable():
    assert chomikuj_source._format_size(None) is None
    assert chomikuj_source._format_size(512) == "512 B"
    assert chomikuj_source._format_size(1024) == "1.0 KB"
    assert chomikuj_source._format_size(204800) == "200.0 KB"
    assert chomikuj_source._format_size(1024 * 1024) == "1.0 MB"


@pytest.mark.parametrize(
    "row",
    [
        {"FileId": "7", "FileName": "Krysar.zip", "FileType": "zip", "Size": 1},
        {"FileId": "7", "FileName": "Krysar.cbz", "FileType": "cbz", "Size": 1},
        {"FileName": "Krysar.epub", "FileType": "epub", "Size": 1},
        {"FileId": "abc", "FileName": "Krysar.epub", "FileType": "epub", "Size": 1},
        {"FileId": 0, "FileName": "Krysar.epub", "FileType": "epub", "Size": 1},
        {"FileId": "7", "FileName": "   ", "FileType": "epub", "Size": 1},
        {"FileId": "7", "FileName": "Krysar.docx", "FileType": "docx", "Size": 1},
        "not-a-row",
    ],
)
def test_mapping_returns_none_for_rows_select_rows_drops(config_values, row):
    allowed = {"epub", "zip", "cbz", "docx"}
    assert chomikuj_source.to_release(row, allowed_formats=allowed) is None


def test_mapping_respects_the_allowed_format_set(config_values):
    assert chomikuj_source.to_release(_row(file_type="epub"), allowed_formats={"mobi"}) is None


def test_mapping_does_not_mutate_the_row(config_values):
    row = _row(name="Dyk Viktor - Krysar.epub", size=200, free=True)
    before = copy.deepcopy(row)
    chomikuj_source.to_release(row, allowed_formats={"epub"})
    assert row == before


def test_pdf_is_filtered_out_under_default_supported_formats(config_values):
    config_values["_supported"][:] = ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"]
    allowed = chomikuj_source.get_allowed_formats()
    assert "pdf" not in allowed
    assert (
        chomikuj_source.to_release(
            _row(name="Krysar.pdf", file_type="pdf"), allowed_formats=allowed
        )
        is None
    )


def test_pdf_is_kept_when_supported_formats_include_it(config_values):
    config_values["_supported"][:] = ["pdf"]
    allowed = chomikuj_source.get_allowed_formats()
    assert allowed == {"pdf"}
    release = chomikuj_source.to_release(
        _row(name="Krysar.pdf", file_type="pdf"), allowed_formats=allowed
    )
    assert release is not None
    assert release.format == "pdf"


def test_pdf_only_search_returns_empty_under_defaults(config_values):
    config_values["_supported"][:] = ["epub", "mobi", "azw3", "fb2", "djvu", "cbz", "cbr"]
    rows = {"Krysař Viktor Dyk": [_row(name="Krysar.pdf", file_type="pdf")], "Krysař": []}
    with _patched(rows):
        assert chomikuj_source.ChomikujSource().search(_book(), _plan()) == []


def test_pdf_only_search_keeps_the_pdf_when_enabled(config_values):
    config_values["_supported"][:] = ["pdf"]
    rows = {
        "Krysař Viktor Dyk": [_row(name="Krysar.pdf", file_type="pdf")],
        "Krysař": [],
    }
    with _patched(rows):
        releases = chomikuj_source.ChomikujSource().search(_book(), _plan())
    assert [release.format for release in releases] == ["pdf"]


# --- Todo 14: column configuration -----------------------------------------


def _columns():
    return chomikuj_source.ChomikujSource().get_column_config()


def test_column_config_has_exactly_four_columns():
    column_config = _columns()
    assert [column.key for column in column_config.columns] == [
        "extra.language",
        "format",
        "size",
        "extra.access",
    ]
    assert column_config.grid_template == "minmax(0,2fr) 60px 80px 80px 80px"
    assert column_config.supported_filters == ["format"]
    assert len(column_config.columns) == 4


def test_language_column_schema():
    column = _columns().columns[0]
    assert column.label == "Language"
    assert column.render_type is ColumnRenderType.BADGE
    assert column.color_hint == ColumnColorHint(type="map", value="language")
    assert column.uppercase is True
    assert column.width == "60px"


def test_format_column_schema():
    column = _columns().columns[1]
    assert column.label == "Format"
    assert column.render_type is ColumnRenderType.BADGE
    assert column.color_hint == ColumnColorHint(type="map", value="format")
    assert column.uppercase is True
    assert column.width == "80px"


def test_size_column_is_sortable_on_size_bytes():
    column = _columns().columns[2]
    assert column.label == "Size"
    assert column.render_type is ColumnRenderType.SIZE
    assert column.sortable is True
    assert column.sort_key == "size_bytes"
    assert column.width == "80px"


def test_access_column_schema():
    column = _columns().columns[3]
    assert column.label == "Access"
    assert column.render_type is ColumnRenderType.BADGE
    assert column.color_hint is None
    assert column.uppercase is True
    assert column.fallback == "-"
    assert column.width == "80px"


def test_no_language_filter_and_format_only():
    assert _columns().supported_filters == ["format"]
    assert "language" not in _columns().supported_filters


def test_column_config_exposes_search_more_action_button():
    action_button = _columns().action_button
    assert action_button is not None
    assert action_button.label == "Search more"
    assert action_button.action == "expand"


def test_serialized_column_config_has_four_columns():
    payload = serialize_column_config(_columns())
    assert [column["key"] for column in payload["columns"]] == [
        "extra.language",
        "format",
        "size",
        "extra.access",
    ]
    assert payload["grid_template"] == "minmax(0,2fr) 60px 80px 80px 80px"
    assert payload["supported_filters"] == ["format"]

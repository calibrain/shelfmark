"""Tests for the paged NFC/NFD Chomikuj document search.

A scripted fake client records every ``search_page`` call, so the assertions
pin the exact query text, page numbers, optional ``Extension``, and the
budget-derived timeout. Time is injected through ``now``; no test sleeps and no
test opens a socket.
"""

import unicodedata

import pytest

from shelfmark.release_sources.chomikuj.client import ChomikujError
from shelfmark.release_sources.chomikuj.filename import IMPORTABLE_EBOOK_FORMATS
from shelfmark.release_sources.chomikuj.search import (
    EXPANDED_MAX_PAGES,
    EXPANDED_SEARCH_BUDGET_SECONDS,
    MAX_PAGES,
    MAX_RESULTS_TARGET_MULTIPLIER,
    SEARCH_BUDGET_SECONDS,
    build_query_text,
    order_and_dedupe,
    search_files,
    select_rows,
)

API_KEY = "synthetic-api-key-search-0000-0000-000000000000"


class FakeClock:
    """Deterministic monotonic clock the tests advance by hand."""

    def __init__(self, value: float = 0.0) -> None:
        """Initialize the clock at the given value."""
        self.value = value

    def __call__(self) -> float:
        """Return the current fake time."""
        return self.value


class FakeClient:
    """Scripted ``search_page`` stub that records every call.

    A step is either a payload dict, an exception instance, or a callable that
    receives the keyword arguments and returns a payload. A callable can read
    the passed ``timeout`` and advance the fake clock, which proves the search
    shrinks its per-request timeouts as the budget drains.
    """

    def __init__(self, *steps: object) -> None:
        """Initialize the client with its scripted steps."""
        self._steps = list(steps)
        self.calls: list[dict[str, object]] = []

    def search_page(
        self,
        api_key: str,
        *,
        query: str,
        page: int,
        extension: str | None = None,
        timeout: float | None = None,
    ) -> dict[str, object]:
        """Record the call and return the next scripted step."""
        self.calls.append(
            {
                "api_key": api_key,
                "query": query,
                "page": page,
                "extension": extension,
                "timeout": timeout,
            }
        )
        if not self._steps:
            raise AssertionError(f"unexpected search_page call #{len(self.calls)}")
        step = self._steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(
                api_key=api_key,
                query=query,
                page=page,
                extension=extension,
                timeout=timeout,
            )
        return step


def row(file_id: int) -> dict[str, object]:
    """Build a synthetic search result row."""
    return {
        "FileId": file_id,
        "FileName": f"Synthetic Author - File {file_id}.epub",
        "FileType": "epub",
        "Size": 1024,
        "MediaType": "Documents",
        "IsFileFreeForUser": True,
    }


def selection_row(
    file_id: object = "10",
    *,
    file_name: object = "Synthetic Author - Wybrana Ksiazka",
    file_type: object = "epub",
) -> dict[str, object]:
    """Build a synthetic selectable search result row."""
    return {
        "FileId": file_id,
        "FileName": file_name,
        "FileType": file_type,
        "Size": 1024,
        "MediaType": "Documents",
        "IsFileFreeForUser": True,
    }


def stopped(results: list[dict[str, object]]) -> dict[str, object]:
    """Return a page payload that reports no next page."""
    return {"Results": results, "IsNextPageAvailable": False}


def open_page(results: list[dict[str, object]]) -> dict[str, object]:
    """Return a page payload that reports a next page."""
    return {"Results": results, "IsNextPageAvailable": True}


# --- constants ------------------------------------------------------------


def test_budget_constants_and_shelfmark_only_escalation():
    """Test budget constants and shelfmark only escalation."""
    assert SEARCH_BUDGET_SECONDS == 12.0
    assert EXPANDED_SEARCH_BUDGET_SECONDS == 20.0
    assert MAX_PAGES == 3
    assert EXPANDED_MAX_PAGES == 5
    assert MAX_RESULTS_TARGET_MULTIPLIER == 3


# --- query text -----------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("  Testowa Książka  ", "Testowa Książka"),
        ("Test", "Test"),
        ("9781234567890", "9781234567890"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_build_query_text_strips_and_keeps_empty(query, expected):
    """Test build query text strips and keeps empty."""
    assert build_query_text(query) == expected


def test_empty_query_raises_error_kind_without_a_request():
    """Test empty query raises error kind without a request."""
    client = FakeClient()

    with pytest.raises(ChomikujError) as info:
        search_files(client, API_KEY, "   ", allowed_formats={"epub"}, limit=20)

    assert info.value.kind == "error"
    assert str(info.value) == "Chomikuj search needs a title, author, or ISBN"
    assert client.calls == []


# --- NFC/NFD variants -----------------------------------------------------


def test_nfc_nfd_polish_query_searches_both_variants_and_merges():
    """Test nfc nfd polish query searches both variants and merges."""
    nfc_query = unicodedata.normalize("NFC", "Twarz Księżyca Parnicki")
    nfd_query = unicodedata.normalize("NFD", nfc_query)
    target = row(8850790304)
    client = FakeClient(stopped([]), stopped([target]))

    rows = search_files(client, API_KEY, nfc_query, allowed_formats={"epub"}, limit=20)

    assert nfc_query != nfd_query
    assert [call["query"] for call in client.calls] == [nfc_query, nfd_query]
    assert [call["page"] for call in client.calls] == [1, 1]
    assert rows == [target]


def test_ascii_query_makes_one_pass():
    """Test ascii query makes one pass."""
    page = stopped([row(1)])
    client = FakeClient(page)

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert len(client.calls) == 1
    assert client.calls[0]["query"] == "Test"
    assert rows == page["Results"]


def test_isbn_query_makes_one_pass():
    """Test isbn query makes one pass."""
    page = stopped([row(2)])
    client = FakeClient(page)

    rows = search_files(client, API_KEY, "9781234567890", allowed_formats={"epub", "pdf"}, limit=20)

    assert len(client.calls) == 1
    assert client.calls[0]["query"] == "9781234567890"
    assert rows == page["Results"]


def test_variant_queries_share_one_budget_deadline():
    """Test variant queries share one budget deadline."""
    clock = FakeClock()
    timeouts: list[float] = []
    nfc_query = unicodedata.normalize("NFC", "Twarz Księżyca")
    nfd_query = unicodedata.normalize("NFD", nfc_query)

    def slow_page(*, timeout, **_kwargs):
        """Advance the clock and return an empty stopped page."""
        timeouts.append(timeout)
        clock.value += 7.0
        return stopped([])

    client = FakeClient(slow_page, slow_page)

    search_files(
        client,
        API_KEY,
        nfc_query,
        allowed_formats={"epub"},
        limit=20,
        budget_seconds=12.0,
        now=clock,
    )

    assert nfc_query != nfd_query
    assert [call["query"] for call in client.calls] == [nfc_query, nfd_query]
    assert timeouts == [12.0, 5.0]


# --- Extension ------------------------------------------------------------


@pytest.mark.parametrize(
    ("allowed_formats", "expected"),
    [
        ({"epub"}, "epub"),
        ({"EPUB", ".epub"}, "epub"),
        ({"epub", "pdf"}, None),
        (set(), None),
        ({"cbz"}, None),
        ({"epub", "cbz"}, "epub"),
    ],
)
def test_extension_single_non_archive_format_only(allowed_formats, expected):
    """Test extension single non archive format only."""
    page = stopped([row(1)])
    client = FakeClient(page)

    search_files(client, API_KEY, "Test", allowed_formats=allowed_formats, limit=20)

    assert client.calls[0]["extension"] == expected


@pytest.mark.parametrize("format_name", ["txt", "rtf", "doc", "docx"])
def test_extension_sent_for_each_single_document_format(format_name):
    """Test each single document format is sent as the Extension parameter."""
    client = FakeClient(stopped([row(1)]))

    search_files(client, API_KEY, "Test", allowed_formats={format_name}, limit=20)

    assert client.calls[0]["extension"] == format_name


# --- paging ---------------------------------------------------------------


def test_paging_merges_pages_in_order_and_stops_when_next_page_is_false():
    """Test paging merges pages in order and stops when next page is false."""
    page1_rows = [row(10)]
    page2_rows = [row(11)]
    client = FakeClient(open_page(page1_rows), stopped(page2_rows))

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert [call["page"] for call in client.calls] == [1, 2]
    assert rows == [*page1_rows, *page2_rows]
    assert rows[0] is page1_rows[0]
    assert rows[1] is page2_rows[0]


def test_paging_stops_after_first_page_when_next_page_is_false():
    """Test paging stops after first page when next page is false."""
    client = FakeClient(stopped([row(1)]))

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert len(client.calls) == 1
    assert len(rows) == 1


def test_paging_never_exceeds_max_pages():
    """Test paging never exceeds max pages."""
    client = FakeClient(*(open_page([row(page)]) for page in range(1, MAX_PAGES + 2)))

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=100)

    assert [call["page"] for call in client.calls] == [1, 2, 3]
    assert len(rows) == MAX_PAGES


def test_expanded_budget_and_page_cap_are_opt_in():
    """Test expanded budget and page cap are opt in."""
    client = FakeClient(*(open_page([row(page)]) for page in range(1, EXPANDED_MAX_PAGES + 2)))

    search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=100,
        budget_seconds=EXPANDED_SEARCH_BUDGET_SECONDS,
        max_pages=EXPANDED_MAX_PAGES,
    )

    assert [call["page"] for call in client.calls] == [1, 2, 3, 4, 5]


def test_paging_stops_at_limit_times_three_unique_file_ids():
    """Test paging stops at limit times three unique file ids."""
    client = FakeClient(
        open_page([row(10), row(11)]),
        open_page([row(11), row(12)]),
        open_page([row(13)]),
    )

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=1)

    assert MAX_RESULTS_TARGET_MULTIPLIER == 3
    assert len(client.calls) == 2
    assert [item["FileId"] for item in rows] == [10, 11, 11, 12]


def test_paging_later_page_error_returns_collected_rows():
    """Test paging later page error returns collected rows."""
    page1_rows = [row(1)]
    client = FakeClient(open_page(page1_rows), ChomikujError("synthetic page-2 failure"))

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert len(client.calls) == 2
    assert rows == page1_rows


def test_paging_later_page_client_error_returns_collected_rows():
    """Test paging later page client error returns collected rows."""
    page1_rows = [row(1)]
    client = FakeClient(open_page(page1_rows), RuntimeError("synthetic page-2 failure"))

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert len(client.calls) == 2
    assert rows == page1_rows


def test_first_page_error_raises():
    """Test first page error raises."""
    failure = ChomikujError("synthetic first-page failure")
    client = FakeClient(failure)

    with pytest.raises(ChomikujError) as info:
        search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert info.value is failure
    assert len(client.calls) == 1


def test_invalid_results_on_first_page_raises():
    """Test invalid results on first page raises."""
    client = FakeClient({"Results": "not-a-list", "IsNextPageAvailable": True})

    with pytest.raises(ChomikujError) as info:
        search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert info.value.kind == "error"
    assert "Results" in str(info.value)
    assert len(client.calls) == 1


def test_invalid_results_page2_returns_collected_rows():
    """Test invalid results page2 returns collected rows."""
    page1_rows = [row(1)]
    client = FakeClient(
        open_page(page1_rows),
        {"Results": "not-a-list", "IsNextPageAvailable": True},
    )

    rows = search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20)

    assert len(client.calls) == 2
    assert rows == page1_rows


# --- budget ---------------------------------------------------------------


def test_budget_exhausted_returns_partial_rows_without_raising():
    """Test budget exhausted returns partial rows without raising."""
    clock = FakeClock()
    page1_rows = [row(1)]

    def page_one(**_kwargs):
        """Exhaust the budget and return the first page."""
        clock.value = 100.0
        return open_page(page1_rows)

    client = FakeClient(page_one, open_page([row(2)]))

    rows = search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=100,
        budget_seconds=100.0,
        now=clock,
    )

    assert len(client.calls) == 1
    assert rows == page1_rows


def test_budget_timeout_shrinks_as_the_budget_drains():
    """Test budget timeout shrinks as the budget drains."""
    clock = FakeClock()
    timeouts: list[float] = []

    def slow_page(*, timeout, page, **_kwargs):
        """Advance the clock and return one row for the page."""
        timeouts.append(timeout)
        clock.value += 5.0
        return open_page([row(page)])

    client = FakeClient(slow_page, slow_page, slow_page, slow_page)

    rows = search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=100,
        budget_seconds=12.0,
        now=clock,
    )

    assert timeouts == [12.0, 7.0, 2.0]
    assert all(isinstance(value, float) and value > 0 for value in timeouts)
    assert len(client.calls) == 3
    assert len(rows) == 3


# --- Shelfmark search deadline --------------------------------------------


class FakeDeadline:
    """Duck-typed ``SearchDeadline`` that records every property read.

    ``remaining`` is a fixed float and ``expired`` is a fixed bool; the call
    counters let a test prove the search consulted the deadline before every
    page. No timer, no thread, and no real config is involved.
    """

    def __init__(self, remaining: float, *, expired: bool = False) -> None:
        """Initialize the deadline with its remaining budget."""
        self._remaining = remaining
        self.expired_now = expired
        self.remaining_reads = 0
        self.expired_reads = 0

    @property
    def remaining(self) -> float:
        """Return the fixed remaining budget and count the read."""
        self.remaining_reads += 1
        return self._remaining

    @property
    def expired(self) -> bool:
        """Return the fixed expiry flag and count the read."""
        self.expired_reads += 1
        return self.expired_now


def test_deadline_caps_budget_and_timeout_then_stops_without_raising():
    """Test deadline caps budget and timeout then stops without raising."""
    clock = FakeClock()
    deadline = FakeDeadline(0.05)
    page1_rows = [row(1)]
    timeouts: list[float] = []

    def capped_page(*, timeout, **_kwargs):
        """Return one page after asserting the capped timeout."""
        timeouts.append(timeout)
        assert timeout == pytest.approx(0.05)
        clock.value += 1.0
        return open_page(page1_rows)

    client = FakeClient(capped_page, open_page([row(2)]))

    rows = search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=100,
        now=clock,
        deadline=deadline,
    )

    assert timeouts == [pytest.approx(0.05)]
    assert len(client.calls) == 1
    assert rows == page1_rows


def test_deadline_longer_than_default_keeps_the_twelve_second_budget():
    """Test deadline longer than default keeps the twelve second budget."""
    clock = FakeClock()
    deadline = FakeDeadline(300.0)
    client = FakeClient(stopped([]))

    search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=20,
        now=clock,
        deadline=deadline,
    )

    assert SEARCH_BUDGET_SECONDS == 12.0
    assert client.calls[0]["timeout"] == SEARCH_BUDGET_SECONDS


def test_no_deadline_keeps_the_default_budget():
    """Test no deadline keeps the default budget."""
    client = FakeClient(stopped([]))

    search_files(client, API_KEY, "Test", allowed_formats={"epub"}, limit=20, now=FakeClock())

    assert client.calls[0]["timeout"] == SEARCH_BUDGET_SECONDS


def test_deadline_is_checked_before_every_page():
    """Test deadline is checked before every page."""
    clock = FakeClock()
    deadline = FakeDeadline(300.0)
    client = FakeClient(*(open_page([row(page)]) for page in range(1, MAX_PAGES + 1)))

    rows = search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=100,
        now=clock,
        deadline=deadline,
    )

    assert len(client.calls) == MAX_PAGES
    assert len(rows) == MAX_PAGES
    assert deadline.expired_reads == len(client.calls)


def test_deadline_zero_stops_without_raising():
    """Test deadline zero stops without raising."""
    deadline = FakeDeadline(0.0)
    client = FakeClient()

    rows = search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=20,
        now=FakeClock(),
        deadline=deadline,
    )

    assert rows == []
    assert client.calls == []


def test_expired_deadline_issues_no_request_and_returns_no_rows():
    """Test expired deadline issues no request and returns no rows."""
    deadline = FakeDeadline(0.05, expired=True)
    client = FakeClient()

    rows = search_files(
        client,
        API_KEY,
        "Test",
        allowed_formats={"epub"},
        limit=20,
        now=FakeClock(),
        deadline=deadline,
    )

    assert rows == []
    assert client.calls == []
    assert deadline.expired_reads >= 1


# --- select_rows ----------------------------------------------------------


def test_select_allowed_importable_formats_and_original_rows():
    """Test select allowed importable formats and original rows."""
    epub = selection_row("10", file_type="epub")
    pdf = selection_row("11", file_name="Synthetic Author - Inna.pdf", file_type="pdf")
    mobi = selection_row("12", file_type="mobi")
    rows = [epub, pdf, mobi]

    selected = select_rows(rows, allowed_formats={"epub", "pdf"})

    assert [row["FileId"] for row in selected] == ["10", "11"]
    assert selected[0] is epub
    assert selected[1] is pdf
    assert rows == [epub, pdf, mobi]


def test_select_rows_empty_selection_accepts_all_importable():
    """Test select rows empty selection accepts all importable."""
    rows = [
        selection_row(str(index), file_type=format_name)
        for index, format_name in enumerate(IMPORTABLE_EBOOK_FORMATS, start=1)
    ]

    assert select_rows(rows, allowed_formats=set()) == rows


def test_select_rows_normalizes_selection_entries():
    """Test select rows normalizes selection entries."""
    epub = selection_row("1", file_type="epub")
    pdf = selection_row("2", file_type="pdf")

    assert select_rows([epub, pdf], allowed_formats={"EPUB", ".epub"}) == [epub]


@pytest.mark.parametrize(
    ("file_name", "file_type"),
    [
        ("Synthetic Author - Book.zip", None),
        ("Synthetic Author - Book.cbz", None),
        ("Synthetic Author - Book.cbr", None),
        ("Synthetic Author - Book", "cbz"),
        ("Synthetic Author - Book", "CBZ"),
    ],
)
def test_select_rows_archive_never_resurrected_even_when_in_allowed_formats(file_name, file_type):
    """Test select rows archive never resurrected even when in allowed formats."""
    archive = selection_row("10", file_name=file_name, file_type=file_type)

    assert select_rows([archive], allowed_formats={"zip", "cbz", "cbr", "epub"}) == []


@pytest.mark.parametrize(
    ("file_name", "file_type"),
    [
        ("Synthetic Author - Book", "kepub"),
        ("Synthetic Author - Book", "cbz"),
        ("Synthetic Author - Book", None),
    ],
)
def test_select_rows_drops_non_importable_or_missing_formats(file_name, file_type):
    """Test select rows drops non importable or missing formats."""
    row = selection_row("10", file_name=file_name, file_type=file_type)

    assert select_rows([row], allowed_formats={"kepub", "cbz", "epub"}) == []


@pytest.mark.parametrize("format_name", ["txt", "rtf", "doc", "docx"])
def test_select_rows_keeps_each_allowed_document_format(format_name):
    """Test select rows keeps each allowed Shelfmark document format."""
    row = selection_row("10", file_name="Synthetic Author - Book", file_type=format_name)

    assert select_rows([row], allowed_formats={format_name}) == [row]


@pytest.mark.parametrize(
    "file_id",
    [
        None,
        "",
        "0",
        "000",
        "-1",
        "10.0",
        "abc",
        " 10",
        "10 ",
        0,
        -1,
        -4713646343,
        10.0,
        4713646343.0,
        True,
        False,
    ],
)
def test_select_rows_drops_file_id_that_is_not_a_positive_decimal_integer_or_int(file_id):
    """Test select rows drops file id that is not a positive decimal integer or int."""
    row = selection_row(file_id)

    assert select_rows([row], allowed_formats=set()) == []


@pytest.mark.parametrize("file_id", ["1", "0010", "9007199254740993", 10, 4713646343])
def test_select_rows_keeps_positive_decimal_integer_strings_and_ints(file_id):
    """Test select rows keeps positive decimal integer strings and ints."""
    row = selection_row(file_id)

    assert select_rows([row], allowed_formats=set()) == [row]


def test_select_rows_keeps_live_api_row_with_integer_file_id():
    """Test select rows keeps live api row with integer file id."""
    live_row = {"FileId": 4713646343, "FileName": "test.pdf", "FileType": "pdf", "Size": 114}

    assert select_rows([live_row], allowed_formats={"pdf"}) == [live_row]
    assert select_rows([live_row], allowed_formats=set()) == [live_row]


@pytest.mark.parametrize("file_name", [None, "", "   ", 42])
def test_select_rows_drops_missing_or_empty_file_name(file_name):
    """Test select rows drops missing or empty file name."""
    row = selection_row("10", file_name=file_name, file_type="epub")

    assert select_rows([row], allowed_formats=set()) == []


def test_select_rows_drops_non_dict_rows():
    """Test select rows drops non dict rows."""
    assert select_rows(
        [None, "row", 42, {"FileId": "10", "FileName": "Book.epub"}], allowed_formats=set()
    ) == [{"FileId": "10", "FileName": "Book.epub"}]


# --- order_and_dedupe -----------------------------------------------------


def test_order_and_dedupe_duplicate_kept_first_and_returns_original_rows():
    """Test order and dedupe duplicate kept first and returns original rows."""
    first = selection_row("7", file_name="First")
    second = selection_row("8", file_name="Second")
    duplicate = selection_row("7", file_name="Duplicate")

    ordered = order_and_dedupe([first, second, duplicate])

    assert len(ordered) == 2
    assert ordered == [second, first]
    assert ordered[0] is second
    assert ordered[1] is first


def test_order_numeric_ids_order_100_10_9():
    """Test order numeric ids order 100 10 9."""
    rows = [selection_row("9"), selection_row("10"), selection_row("100")]

    ordered = order_and_dedupe(rows)

    assert [row["FileId"] for row in ordered] == ["100", "10", "9"]
    assert rows == [selection_row("9"), selection_row("10"), selection_row("100")]


def test_order_and_dedupe_leading_zero_is_distinct_key_but_equal_numeric_stable():
    """Test order and dedupe leading zero is distinct key but equal numeric stable."""
    padded = selection_row("0010", file_name="Padded")
    plain = selection_row("10", file_name="Plain")

    padded_first = order_and_dedupe([padded, plain])
    plain_first = order_and_dedupe([plain, padded])

    assert [row["FileId"] for row in padded_first] == ["0010", "10"]
    assert [row["FileId"] for row in plain_first] == ["10", "0010"]
    assert padded_first[0] is padded
    assert plain_first[0] is plain


def test_order_and_dedupe_non_numeric_ids_follow_numeric_lexicographically_descending():
    """Test order and dedupe non numeric ids follow numeric lexicographically descending."""
    rows = [
        selection_row("abc"),
        selection_row("9"),
        selection_row("abd"),
        selection_row("10"),
    ]

    ordered = order_and_dedupe(rows)

    assert [row["FileId"] for row in ordered] == ["10", "9", "abd", "abc"]


def test_order_and_dedupe_orders_integer_and_string_file_ids_numerically():
    """Test order and dedupe orders integer and string file ids numerically."""
    older = selection_row("4713646342", file_type="pdf")
    newer = selection_row(4713646343, file_type="pdf")

    ordered = order_and_dedupe([older, newer])

    assert [row["FileId"] for row in ordered] == [4713646343, "4713646342"]
    assert ordered[0] is newer
    assert ordered[1] is older


def test_order_and_dedupe_integer_and_equivalent_string_share_exact_key():
    """Test order and dedupe integer and equivalent string share exact key."""
    numeric = selection_row(10)
    textual = selection_row("10")
    padded = selection_row("0010")

    ordered = order_and_dedupe([numeric, textual, padded])

    assert [row["FileId"] for row in ordered] == [10, "0010"]
    assert ordered[0] is numeric
    assert ordered[1] is padded


def test_select_rows_drops_non_numeric_id_that_order_and_dedupe_still_orders():
    """Test select rows drops non numeric id that order and dedupe still orders."""
    invalid = selection_row("not-a-number")
    numeric = selection_row("10")

    assert select_rows([invalid, numeric], allowed_formats=set()) == [numeric]
    assert order_and_dedupe([invalid, numeric]) == [numeric, invalid]


def test_order_and_dedupe_returns_a_new_list_without_mutating_input():
    """Test order and dedupe returns a new list without mutating input."""
    rows = [selection_row("2"), selection_row("10")]
    snapshot = [dict(row) for row in rows]

    ordered = order_and_dedupe(rows)

    assert ordered == [rows[1], rows[0]]
    assert ordered is not rows
    assert rows == snapshot

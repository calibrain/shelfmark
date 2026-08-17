"""Guards on the shape of Hardcover's `fields`/`weights` search parameters.

Hardcover turns `fields` into Typesense's `query_by` but keeps `num_typos` and
`query_by_weights` as fixed-length presets per query_type. A field list of the
wrong length is not searched loosely -- the whole search is rejected with a null
results body, which used to surface as "0 results". These tests pin the counts
so a narrower field list cannot silently ship again.
"""

import pytest

from shelfmark.metadata_providers.hardcover import (
    AUTHOR_SUGGESTION_FIELDS,
    AUTHOR_SUGGESTION_WEIGHTS,
    BOOK_SEARCH_FIELD_COUNT,
    BOOK_SEARCH_FIELDS,
    BOOK_TITLE_AUTHOR_WEIGHTS,
    BOOK_TITLE_WEIGHTS,
    SERIES_SEARCH_FIELDS,
    SERIES_SEARCH_WEIGHTS,
    TITLE_SUGGESTION_FIELDS,
    TITLE_SUGGESTION_WEIGHTS,
    HardcoverProvider,
)


def _count(value: str) -> int:
    return len([part for part in value.split(",") if part.strip()])


class TestBookSearchFieldCounts:
    def test_book_field_list_matches_hardcovers_preset_length(self):
        assert _count(BOOK_SEARCH_FIELDS) == BOOK_SEARCH_FIELD_COUNT

    @pytest.mark.parametrize(
        ("label", "weights"),
        [
            ("title", BOOK_TITLE_WEIGHTS),
            ("title+author", BOOK_TITLE_AUTHOR_WEIGHTS),
            ("title typeahead", TITLE_SUGGESTION_WEIGHTS),
        ],
    )
    def test_book_weights_line_up_with_the_field_list(self, label, weights):
        assert _count(weights) == BOOK_SEARCH_FIELD_COUNT, label

    def test_title_typeahead_uses_the_full_book_field_list(self):
        assert TITLE_SUGGESTION_FIELDS == BOOK_SEARCH_FIELDS


class TestNonBookSearchFieldCounts:
    @pytest.mark.parametrize(
        ("fields", "weights"),
        [
            (AUTHOR_SUGGESTION_FIELDS, AUTHOR_SUGGESTION_WEIGHTS),
            (SERIES_SEARCH_FIELDS, SERIES_SEARCH_WEIGHTS),
        ],
    )
    def test_weights_line_up_with_their_field_list(self, fields, weights):
        assert _count(fields) == _count(weights)


class TestBuildSearchParams:
    @pytest.mark.parametrize(
        ("author", "title", "series"),
        [
            ("", "Dune", ""),
            ("Herbert", "Dune", ""),
            ("Herbert", "", ""),
            ("", "", ""),
        ],
    )
    def test_every_branch_sends_a_usable_field_weight_pair(self, author, title, series):
        provider = HardcoverProvider(api_key="test-token")

        _query, fields, weights = provider._build_search_params("dune", author, title, series)

        if fields is None:
            # No override: Hardcover applies its own preset, so weights must be absent too.
            assert weights is None
            return
        assert _count(fields) == BOOK_SEARCH_FIELD_COUNT
        assert _count(weights) == BOOK_SEARCH_FIELD_COUNT

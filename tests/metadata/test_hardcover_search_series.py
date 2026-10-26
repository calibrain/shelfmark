from shelfmark.metadata_providers.hardcover import HardcoverProvider


def _provider() -> HardcoverProvider:
    return HardcoverProvider(api_key="test-token")


def _hit(**overrides):
    # The shape of a search hit: the featured series is `featured_series`, not the
    # `featured_book_series` of a full book record.
    hit = {
        "id": "369692",
        "title": "Mistborn: The Final Empire",
        "subtitle": "The Final Empire",
        "author_names": ["Brandon Sanderson"],
        "featured_series": {
            "position": 1.0,
            "series": {
                "id": 10001,
                "name": "The Mistborn Saga: The Original Trilogy",
                "primary_books_count": 3,
            },
        },
        "featured_series_position": 1.0,
    }
    hit.update(overrides)
    return hit


def test_a_search_result_carries_the_featured_series():
    book = _provider()._parse_search_result(_hit())

    assert book is not None
    assert book.series_id == "10001"
    assert book.series_name == "The Mistborn Saga: The Original Trilogy"
    assert book.series_position == 1.0
    assert book.series_count == 3


def test_a_search_result_without_a_series_has_none():
    for featured in (None, {}, {"position": 2.0}):
        book = _provider()._parse_search_result(_hit(featured_series=featured))

        assert book is not None
        assert book.series_name is None
        assert book.series_id is None


def test_a_search_result_nested_in_a_document_carries_the_series_too():
    book = _provider()._parse_search_result({"document": _hit()})

    assert book is not None
    assert book.series_name == "The Mistborn Saga: The Original Trilogy"


def test_the_search_title_is_unchanged_by_the_series():
    book = _provider()._parse_search_result(_hit())

    assert book is not None
    assert book.search_title == "Mistborn"

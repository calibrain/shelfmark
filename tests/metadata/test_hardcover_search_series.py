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


def test_a_series_named_unlike_the_title_prefix_leaves_the_search_title():
    book = _provider()._parse_search_result(_hit())

    assert book is not None
    assert book.search_title == "Mistborn"


def _series(name: str) -> dict:
    return {"position": 1.0, "series": {"id": 1, "name": name, "primary_books_count": 3}}


def test_a_series_prefix_is_dropped_from_a_search_result_like_from_a_full_record():
    book = _provider()._parse_search_result(
        _hit(title="Mistborn: The Final Empire", featured_series=_series("Mistborn"))
    )

    assert book is not None
    assert book.search_title == "The Final Empire"


def test_a_series_followed_by_a_genre_descriptor_is_not_searched_as_the_series():
    # Without the series, #1728's fix would search "Jack Reacher" and match any volume.
    book = _provider()._parse_search_result(
        _hit(
            title="Jack Reacher: A Novel",
            subtitle="A Novel",
            featured_series=_series("Jack Reacher"),
        )
    )

    assert book is not None
    assert book.search_title is None


def test_a_hit_with_a_null_document_and_no_series_is_still_parsed():
    book = _provider()._parse_search_result(_hit(featured_series=None, document=None))

    assert book is not None
    assert book.series_name is None

from shelfmark.metadata_providers import MetadataSearchOptions, SearchResult
from shelfmark.metadata_providers.hardcover import HardcoverProvider


class TestHardcoverSeriesSearch:
    def test_get_search_field_options_returns_series_suggestions(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: {
                "search": {
                    "results": {
                        "hits": [
                            {
                                "document": {
                                    "id": 7,
                                    "name": "Mistborn",
                                    "author_name": "Brandon Sanderson",
                                    "primary_books_count": 7,
                                }
                            }
                        ],
                        "found": 1,
                    }
                }
            },
        )

        options = provider.get_search_field_options("series", query="mist")

        assert options == [
            {
                "value": "id:7",
                "label": "Mistborn",
                "description": "by Brandon Sanderson • 7 books",
            }
        ]

    def test_series_suggestions_prefer_direct_author_series_for_author_queries(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        def fake_execute(query: str, variables):
            if variables.get("queryType") == "Author":
                return {
                    "search": {
                        "results": {
                            "hits": [
                                {
                                    "document": {
                                        "id": 204214,
                                        "name": "Brandon Sanderson",
                                    }
                                }
                            ],
                            "found": 1,
                        }
                    }
                }
            if variables.get("queryType") == "Series":
                return {
                    "search": {
                        "results": {
                            "hits": [
                                {
                                    "document": {
                                        "id": 193594,
                                        "name": " Brandon Sanderson",
                                        "author_name": "Brandon Sanderson",
                                    }
                                },
                                {
                                    "document": {
                                        "id": 1052,
                                        "name": "White Sand",
                                        "author_name": "Brandon Sanderson",
                                        "primary_books_count": 3,
                                    }
                                },
                            ],
                            "found": 2,
                        }
                    }
                }
            if variables.get("authorIds") == [204214]:
                return {
                    "series": [
                        {
                            "id": 997,
                            "name": "The Stormlight Archive",
                            "primary_books_count": 10,
                            "books_count": 25,
                            "author": {"name": "Brandon Sanderson"},
                        },
                        {
                            "id": 5452,
                            "name": "The Mistborn Saga",
                            "primary_books_count": 10,
                            "books_count": 16,
                            "author": {"name": "Brandon Sanderson"},
                        },
                    ]
                }
            raise AssertionError(f"Unexpected query variables: {variables}")

        monkeypatch.setattr(provider, "_execute_query", fake_execute)

        options = provider.get_search_field_options("series", query="Brandon Sanderson")

        assert options[:3] == [
            {
                "value": "id:997",
                "label": "The Stormlight Archive",
                "description": "by Brandon Sanderson • 10 books",
            },
            {
                "value": "id:5452",
                "label": "The Mistborn Saga",
                "description": "by Brandon Sanderson • 10 books",
            },
            {
                "value": "id:193594",
                "label": "Brandon Sanderson",
                "description": "by Brandon Sanderson",
            },
        ]

    def test_search_paginated_uses_selected_series_id(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        expected = SearchResult(books=[], page=2, total_found=14, has_more=True)
        captured: dict[str, int] = {}

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": True,
                "HARDCOVER_EXCLUDE_UNRELEASED": True,
            }.get(key, default),
        )

        def fake_fetch(
            series_id: int,
            page: int,
            limit: int,
            exclude_compilations: bool,
            exclude_unreleased: bool,
        ) -> SearchResult:
            captured["series_id"] = series_id
            captured["page"] = page
            captured["limit"] = limit
            captured["exclude_compilations"] = int(exclude_compilations)
            captured["exclude_unreleased"] = int(exclude_unreleased)
            return expected

        monkeypatch.setattr(provider, "_fetch_series_books_by_id", fake_fetch)

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="",
                page=2,
                limit=20,
                fields={"series": "id:42"},
            )
        )

        assert result == expected
        assert captured == {
            "series_id": 42,
            "page": 2,
            "limit": 20,
            "exclude_compilations": 1,
            "exclude_unreleased": 1,
        }

    def test_search_paginated_resolves_typed_series_name_before_fetch(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        expected = SearchResult(books=[], page=1, total_found=3, has_more=False)
        captured: dict[str, int] = {}

        def fake_execute(query: str, variables):
            return {
                "search": {
                    "results": {
                        "hits": [
                            {"document": {"id": 3, "name": "Mistborn Trilogy"}},
                            {"document": {"id": 9, "name": "Mistborn"}},
                        ],
                        "found": 2,
                    }
                }
            }

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": False,
                "HARDCOVER_EXCLUDE_UNRELEASED": True,
            }.get(key, default),
        )

        def fake_fetch(
            series_id: int,
            page: int,
            limit: int,
            exclude_compilations: bool,
            exclude_unreleased: bool,
        ) -> SearchResult:
            captured["series_id"] = series_id
            captured["exclude_compilations"] = int(exclude_compilations)
            captured["exclude_unreleased"] = int(exclude_unreleased)
            return expected

        monkeypatch.setattr(provider, "_execute_query", fake_execute)
        monkeypatch.setattr(provider, "_fetch_series_books_by_id", fake_fetch)

        result = provider.search_paginated(
            MetadataSearchOptions(
                query="",
                page=1,
                limit=40,
                fields={"series": "Mistborn"},
            )
        )

        assert result == expected
        assert captured["series_id"] == 9
        assert captured["exclude_compilations"] == 0
        assert captured["exclude_unreleased"] == 1

    def test_fetch_series_books_by_id_preserves_series_metadata(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": True,
                "HARDCOVER_EXCLUDE_UNRELEASED": False,
            }.get(key, default),
        )

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: captured.update({"query": query, "variables": variables}) or {
                "series": [
                    {
                        "id": 42,
                        "name": "Mistborn",
                        "primary_books_count": 3,
                        "book_series": [
                            {
                                "position": 1,
                                "book": {
                                    "id": 100,
                                    "title": "The Final Empire",
                                    "subtitle": None,
                                    "slug": "the-final-empire",
                                    "release_date": "2006-07-17",
                                    "headline": None,
                                    "description": "A heist.",
                                    "rating": 4.5,
                                    "ratings_count": 120,
                                    "users_count": 250,
                                    "cached_image": {"url": "https://example.com/final-empire.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 1,
                                        "series": {
                                            "id": 42,
                                            "name": "Mistborn",
                                            "primary_books_count": 3,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 2,
                                "book": {
                                    "id": 101,
                                    "title": "The Well of Ascension",
                                    "subtitle": None,
                                    "slug": "the-well-of-ascension",
                                    "release_date": "2007-08-21",
                                    "headline": None,
                                    "description": "The sequel.",
                                    "rating": 4.4,
                                    "ratings_count": 110,
                                    "users_count": 220,
                                    "cached_image": {"url": "https://example.com/well-of-ascension.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 2,
                                        "series": {
                                            "id": 42,
                                            "name": "Mistborn",
                                            "primary_books_count": 3,
                                        },
                                    },
                                },
                            },
                        ],
                    }
                ]
            },
        )

        result = provider._fetch_series_books_by_id(
            42,
            page=1,
            limit=2,
            exclude_compilations=True,
            exclude_unreleased=False,
        )

        assert "canonical_id: {_is_null: true}" in str(captured["query"])
        assert 'state: {_in: ["normalized", "normalizing"]}' in str(captured["query"])
        assert result.total_found == 2
        assert result.has_more is False
        assert captured["variables"] == {"seriesId": 42}
        assert [book.title for book in result.books] == [
            "The Final Empire",
            "The Well of Ascension",
        ]
        assert result.books[0].series_id == "42"
        assert result.books[0].series_name == "Mistborn"
        assert result.books[0].series_position == 1
        assert result.books[0].series_count == 2

    def test_fetch_series_books_by_id_skips_split_part_entries_for_standard_series(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": False,
                "HARDCOVER_EXCLUDE_UNRELEASED": False,
            }.get(key, default),
        )

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: {
                "series": [
                    {
                        "id": 10001,
                        "name": "The Mistborn Saga: The Original Trilogy",
                        "primary_books_count": 6,
                        "book_series": [
                            {
                                "position": 0.5,
                                "book": {
                                    "id": 427844,
                                    "title": "The Eleventh Metal",
                                    "subtitle": None,
                                    "slug": "the-eleventh-metal",
                                    "release_date": "2012-04-11",
                                    "headline": None,
                                    "description": None,
                                    "rating": 4.1,
                                    "ratings_count": 404,
                                    "users_count": 991,
                                    "editions_count": 5,
                                    "compilation": False,
                                    "cached_image": {},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 0.5,
                                        "series": {
                                            "id": 10001,
                                            "name": "The Mistborn Saga: The Original Trilogy",
                                            "primary_books_count": 6,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 1,
                                "book": {
                                    "id": 369692,
                                    "title": "Mistborn: The Final Empire",
                                    "subtitle": None,
                                    "slug": "mistborn-the-final-empire",
                                    "release_date": "2006-01-01",
                                    "headline": None,
                                    "description": None,
                                    "rating": 4.5,
                                    "ratings_count": 3942,
                                    "users_count": 8192,
                                    "editions_count": 92,
                                    "compilation": False,
                                    "cached_image": {},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 1,
                                        "series": {
                                            "id": 10001,
                                            "name": "The Mistborn Saga: The Original Trilogy",
                                            "primary_books_count": 6,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 2,
                                "book": {
                                    "id": 427383,
                                    "title": "The Well of Ascension",
                                    "subtitle": None,
                                    "slug": "the-well-of-ascension",
                                    "release_date": "2007-08-21",
                                    "headline": None,
                                    "description": None,
                                    "rating": 4.4,
                                    "ratings_count": 3007,
                                    "users_count": 5136,
                                    "editions_count": 85,
                                    "compilation": False,
                                    "cached_image": {},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 2,
                                        "series": {
                                            "id": 10001,
                                            "name": "The Mistborn Saga: The Original Trilogy",
                                            "primary_books_count": 6,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 3,
                                "book": {
                                    "id": 103241,
                                    "title": "The Hero of Ages",
                                    "subtitle": None,
                                    "slug": "the-hero-of-ages",
                                    "release_date": "2007-12-30",
                                    "headline": None,
                                    "description": None,
                                    "rating": 4.4,
                                    "ratings_count": 2786,
                                    "users_count": 4752,
                                    "editions_count": 78,
                                    "compilation": False,
                                    "cached_image": {},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 3,
                                        "series": {
                                            "id": 10001,
                                            "name": "The Mistborn Saga: The Original Trilogy",
                                            "primary_books_count": 6,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 3.5,
                                "book": {
                                    "id": 427881,
                                    "title": "Mistborn: Secret History",
                                    "subtitle": None,
                                    "slug": "mistborn-secret-history",
                                    "release_date": "2016-01-01",
                                    "headline": None,
                                    "description": None,
                                    "rating": 4.3,
                                    "ratings_count": 717,
                                    "users_count": 1546,
                                    "editions_count": 13,
                                    "compilation": False,
                                    "cached_image": {},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 3.5,
                                        "series": {
                                            "id": 10001,
                                            "name": "The Mistborn Saga: The Original Trilogy",
                                            "primary_books_count": 6,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 6,
                                "book": {
                                    "id": 763900,
                                    "title": "The Bands of Mourning, Part 2",
                                    "subtitle": None,
                                    "slug": "the-bands-of-mourning-part-2",
                                    "release_date": "2011-11-08",
                                    "headline": None,
                                    "description": None,
                                    "rating": 2.0,
                                    "ratings_count": 1,
                                    "users_count": 13,
                                    "editions_count": 1,
                                    "compilation": False,
                                    "cached_image": {},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 6,
                                        "series": {
                                            "id": 10001,
                                            "name": "The Mistborn Saga: The Original Trilogy",
                                            "primary_books_count": 6,
                                        },
                                    },
                                },
                            },
                        ],
                    }
                ]
            },
        )

        result = provider._fetch_series_books_by_id(
            10001,
            page=1,
            limit=20,
            exclude_compilations=False,
            exclude_unreleased=False,
        )

        assert [book.title for book in result.books] == [
            "The Eleventh Metal",
            "Mistborn: The Final Empire",
            "The Well of Ascension",
            "The Hero of Ages",
            "Mistborn: Secret History",
        ]
        assert result.total_found == 5
        assert result.has_more is False

    def test_fetch_series_books_by_id_prefers_best_book_per_position(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": False,
                "HARDCOVER_EXCLUDE_UNRELEASED": True,
            }.get(key, default),
        )

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: {
                "series": [
                    {
                        "id": 5452,
                        "name": "The Mistborn Saga",
                        "primary_books_count": 10,
                        "book_series": [
                            {
                                "position": 0.5,
                                "book": {
                                    "id": 1389888,
                                    "title": "Sunrise on the Reaping",
                                    "subtitle": None,
                                    "slug": "sunrise-on-the-reaping",
                                    "release_date": "2025-01-01",
                                    "headline": None,
                                    "description": "The next Panem story.",
                                    "rating": 4.5,
                                    "ratings_count": 900,
                                    "users_count": 2314,
                                    "editions_count": 32,
                                    "compilation": False,
                                    "cached_image": {"url": "https://example.com/sunrise.jpg"},
                                    "cached_contributors": [{"name": "Suzanne Collins"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 0.5,
                                        "series": {
                                            "id": 5452,
                                            "name": "The Mistborn Saga",
                                            "primary_books_count": 10,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 4,
                                "book": {
                                    "id": 330249,
                                    "title": "The Alloy of Law",
                                    "subtitle": None,
                                    "slug": "the-alloy-of-law",
                                    "release_date": "2011-01-01",
                                    "headline": None,
                                    "description": "Wax and Wayne arrive.",
                                    "rating": 4.2,
                                    "ratings_count": 1630,
                                    "users_count": 3142,
                                    "editions_count": 53,
                                    "compilation": False,
                                    "cached_image": {"url": "https://example.com/alloy.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 1,
                                        "series": {
                                            "id": 10825,
                                            "name": "Wax & Wayne",
                                            "primary_books_count": 4,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 4,
                                "book": {
                                    "id": 2190514,
                                    "title": "Aleación de ley (Wax & Wayne 1): Una novela de Mistborn",
                                    "subtitle": None,
                                    "slug": "aleacion-de-ley",
                                    "release_date": "2011-11-08",
                                    "headline": None,
                                    "description": "Localized duplicate.",
                                    "rating": 4.0,
                                    "ratings_count": 1,
                                    "users_count": 2,
                                    "editions_count": 1,
                                    "compilation": False,
                                    "cached_image": {"url": "https://example.com/aleacion.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 4,
                                        "series": {
                                            "id": 5452,
                                            "name": "The Mistborn Saga",
                                            "primary_books_count": 10,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 4.5,
                                "book": {
                                    "id": 427892,
                                    "title": "Allomancer Jak and the Pits of Eltania",
                                    "subtitle": None,
                                    "slug": "allomancer-jak",
                                    "release_date": "2014-08-03",
                                    "headline": None,
                                    "description": "A novella.",
                                    "rating": 4.1,
                                    "ratings_count": 182,
                                    "users_count": 572,
                                    "editions_count": 3,
                                    "compilation": False,
                                    "cached_image": {"url": "https://example.com/jak.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 4.5,
                                        "series": {
                                            "id": 5452,
                                            "name": "The Mistborn Saga",
                                            "primary_books_count": 10,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 8,
                                "book": {
                                    "id": 1878844,
                                    "title": "Ghostbloods 1",
                                    "subtitle": None,
                                    "slug": "ghostbloods-1",
                                    "release_date": "2028-12-01",
                                    "headline": None,
                                    "description": "Future title.",
                                    "rating": None,
                                    "ratings_count": 0,
                                    "users_count": 68,
                                    "editions_count": 1,
                                    "compilation": False,
                                    "cached_image": {"url": "https://example.com/ghostbloods.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 8,
                                        "series": {
                                            "id": 5452,
                                            "name": "The Mistborn Saga",
                                            "primary_books_count": 10,
                                        },
                                    },
                                },
                            },
                            {
                                "position": 9,
                                "book": {
                                    "id": 1898126,
                                    "title": "Ghostbloods 2",
                                    "subtitle": None,
                                    "slug": "ghostbloods-2",
                                    "release_date": None,
                                    "headline": None,
                                    "description": "No published date yet.",
                                    "rating": None,
                                    "ratings_count": 0,
                                    "users_count": 40,
                                    "editions_count": 1,
                                    "compilation": False,
                                    "cached_image": {"url": "https://example.com/ghostbloods-2.jpg"},
                                    "cached_contributors": [{"name": "Brandon Sanderson"}],
                                    "contributions": [],
                                    "featured_book_series": {
                                        "position": 9,
                                        "series": {
                                            "id": 5452,
                                            "name": "The Mistborn Saga",
                                            "primary_books_count": 10,
                                        },
                                    },
                                },
                            },
                        ],
                    }
                ]
            },
        )

        result = provider._fetch_series_books_by_id(
            5452,
            page=1,
            limit=10,
            exclude_compilations=False,
            exclude_unreleased=True,
        )

        assert result.total_found == 3
        assert result.has_more is False
        assert [book.title for book in result.books] == [
            "Sunrise on the Reaping",
            "The Alloy of Law",
            "Allomancer Jak and the Pits of Eltania",
        ]
        assert [book.series_position for book in result.books] == [0.5, 4, 4.5]

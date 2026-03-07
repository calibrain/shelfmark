from shelfmark.metadata_providers.hardcover import HardcoverProvider


class TestHardcoverFieldOptions:
    def test_search_fields_enable_typeahead_for_series_only(self):
        provider = HardcoverProvider(api_key="test-token")
        fields_by_key = {field.key: field for field in provider.search_fields}

        assert fields_by_key["author"].suggestions_endpoint is None
        assert fields_by_key["title"].suggestions_endpoint is None
        assert fields_by_key["series"].suggestions_endpoint == (
            "/api/metadata/field-options?provider=hardcover&field=series"
        )

    def test_get_search_field_options_returns_author_suggestions(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: captured.update({"query": query, "variables": variables}) or {
                "search": {
                    "results": {
                        "hits": [
                            {"document": {"name": "Brandon Sanderson"}},
                            {"document": {"name": "Brandon Sanderson"}},
                            {"document": {"name": "Brian Sanderson"}},
                        ],
                        "found": 3,
                    }
                }
            },
        )

        options = provider.get_search_field_options("author", query="sand")

        assert options == [
            {"value": "Brandon Sanderson", "label": "Brandon Sanderson"},
            {"value": "Brian Sanderson", "label": "Brian Sanderson"},
        ]
        assert captured["variables"] == {
            "query": "sand",
            "queryType": "Author",
            "limit": 7,
            "page": 1,
            "sort": "_text_match:desc,books_count:desc",
            "fields": "name,name_personal,alternate_names",
            "weights": "4,3,2",
        }

    def test_get_search_field_options_returns_filtered_title_suggestions(self, monkeypatch):
        provider = HardcoverProvider(api_key="test-token")
        captured: dict[str, object] = {}

        monkeypatch.setattr(
            "shelfmark.metadata_providers.hardcover.app_config.get",
            lambda key, default=None: {
                "HARDCOVER_EXCLUDE_COMPILATIONS": True,
                "HARDCOVER_EXCLUDE_UNRELEASED": True,
            }.get(key, default),
        )

        monkeypatch.setattr(
            provider,
            "_execute_query",
            lambda query, variables: captured.update({"query": query, "variables": variables}) or {
                "search": {
                    "results": {
                        "hits": [
                            {
                                "document": {
                                    "title": "Mistborn: The Final Empire",
                                    "compilation": False,
                                    "release_year": 2006,
                                }
                            },
                            {
                                "document": {
                                    "title": "Mistborn Trilogy",
                                    "compilation": True,
                                    "release_year": 2001,
                                }
                            },
                            {
                                "document": {
                                    "title": "Ghostbloods 1",
                                    "compilation": False,
                                    "release_year": 2028,
                                }
                            },
                            {
                                "document": {
                                    "title": "Mistborn: The Final Empire",
                                    "compilation": False,
                                    "release_year": 2006,
                                }
                            },
                            {
                                "document": {
                                    "title": "Mistborn: Secret History",
                                    "compilation": False,
                                    "release_year": 2016,
                                }
                            },
                        ],
                        "found": 5,
                    }
                }
            },
        )

        options = provider.get_search_field_options("title", query="mistborn")

        assert options == [
            {"value": "Mistborn: The Final Empire", "label": "Mistborn: The Final Empire"},
            {"value": "Mistborn: Secret History", "label": "Mistborn: Secret History"},
        ]
        assert captured["variables"] == {
            "query": "mistborn",
            "queryType": "Book",
            "limit": 7,
            "page": 1,
            "sort": "_text_match:desc,users_count:desc",
            "fields": "title,alternative_titles",
            "weights": "5,2",
        }

    def test_get_search_field_options_skips_short_text_queries(self):
        provider = HardcoverProvider(api_key="test-token")

        assert provider.get_search_field_options("author", query="a") == []
        assert provider.get_search_field_options("title", query="i") == []

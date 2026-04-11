from shelfmark.metadata_providers.googlebooks import GoogleBooksProvider


class TestGoogleBooksParseVolume:
    def test_parse_volume_returns_metadata_for_valid_payload(self):
        provider = GoogleBooksProvider(api_key="test-key")

        result = provider._parse_volume(
            {
                "id": "volume-1",
                "volumeInfo": {
                    "title": "Test Book",
                    "authors": ["Alice Author"],
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "1234567890"},
                        {"type": "ISBN_13", "identifier": "9781234567897"},
                    ],
                    "imageLinks": {
                        "thumbnail": "http://example.com/cover.jpg&edge=curl",
                    },
                    "publisher": "Test Publisher",
                    "publishedDate": "2024-03-01",
                    "language": "en",
                    "categories": ["Fiction", "Fantasy"],
                    "description": "A book.",
                    "infoLink": "https://example.com/books/volume-1",
                    "averageRating": 4.2,
                    "ratingsCount": 1200,
                },
            }
        )

        assert result is not None
        assert result.provider_id == "volume-1"
        assert result.title == "Test Book"
        assert result.authors == ["Alice Author"]
        assert result.isbn_10 == "1234567890"
        assert result.isbn_13 == "9781234567897"
        assert result.cover_url == "https://example.com/cover.jpg"
        assert result.publish_year == 2024
        assert result.display_fields[0].value == "4.2 (1,200)"

    def test_parse_volume_returns_none_for_malformed_rating_payload(self):
        provider = GoogleBooksProvider(api_key="test-key")

        result = provider._parse_volume(
            {
                "id": "volume-2",
                "volumeInfo": {
                    "title": "Broken Book",
                    "averageRating": "not-a-number",
                },
            }
        )

        assert result is None

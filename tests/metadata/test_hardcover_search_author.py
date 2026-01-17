from shelfmark.metadata_providers.hardcover import _simplify_author_for_search


class TestHardcoverSimplifyAuthorForSearch:
    def test_removes_middle_initial(self):
        assert _simplify_author_for_search("Robert R. McCammon") == "Robert McCammon"

    def test_keeps_suffix(self):
        assert _simplify_author_for_search("Martin L. King Jr.") == "Martin King Jr."

    def test_handles_comma_format(self):
        assert _simplify_author_for_search("McCammon, Robert R.") == "Robert McCammon"

    def test_returns_none_when_no_change(self):
        assert _simplify_author_for_search("Frank Herbert") is None

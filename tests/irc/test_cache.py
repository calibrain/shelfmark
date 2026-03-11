from shelfmark.release_sources import Release
from shelfmark.release_sources.irc import cache


def test_cache_results_isolated_by_content_type(monkeypatch):
    state = {"entries": {}, "version": 1}

    monkeypatch.setattr(cache, "_load_cache", lambda: state)
    monkeypatch.setattr(cache, "_save_cache", lambda _cache: None)

    ebook_release = Release(source="irc", source_id="ebook", title="Shared Title", format="epub")
    audiobook_release = Release(source="irc", source_id="audio", title="Shared Title", format="zip")

    cache.cache_results("hardcover", "123", "Shared Title", [ebook_release], content_type="ebook")
    cache.cache_results("hardcover", "123", "Shared Title", [audiobook_release], content_type="audiobook")

    ebook_cached = cache.get_cached_results("hardcover", "123", content_type="ebook", ttl_seconds=60)
    audiobook_cached = cache.get_cached_results("hardcover", "123", content_type="audiobook", ttl_seconds=60)

    assert [release.source_id for release in ebook_cached["releases"]] == ["ebook"]
    assert [release.source_id for release in audiobook_cached["releases"]] == ["audio"]

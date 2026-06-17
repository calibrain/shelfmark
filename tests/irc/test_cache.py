from shelfmark.release_sources import Release
from shelfmark.release_sources.irc import cache


def test_cache_results_round_trip_by_query_identity(monkeypatch):
    """The whole answer (all content types) is cached under one query identity."""
    state = {"entries": {}, "version": 1}

    monkeypatch.setattr(cache, "_load_cache", lambda: state)
    monkeypatch.setattr(cache, "_save_cache", lambda _cache: None)

    ebook_release = Release(
        source="irc", source_id="ebook", title="Shared Title", format="epub", content_type="ebook"
    )
    audiobook_release = Release(
        source="irc",
        source_id="audio",
        title="Shared Title",
        format="mp3",
        content_type="audiobook",
    )

    key = "irc.example.net:ebooks:words of radiance"
    cache.cache_results(key, "words of radiance", [ebook_release, audiobook_release])

    cached = cache.get_cached_results(key, ttl_seconds=60)
    assert {release.source_id for release in cached["releases"]} == {"ebook", "audio"}

    # A different query identity is isolated.
    assert cache.get_cached_results("irc.example.net:ebooks:other query", ttl_seconds=60) is None

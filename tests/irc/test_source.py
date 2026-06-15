from types import SimpleNamespace

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release
from shelfmark.release_sources.irc.parser import SearchResult
from shelfmark.release_sources.irc.source import IRCReleaseSource


def test_convert_to_releases_marks_audiobook_results_and_sorts_audio_before_archives():
    source = IRCReleaseSource()
    source._online_servers = set()

    results = [
        SearchResult(
            server="AudioBot",
            author="Author Name",
            title="Archive Release",
            format="zip",
            size="1.2GB",
            full_line="!AudioBot Author Name - Archive Release.zip ::INFO:: 1.2GB",
        ),
        SearchResult(
            server="AudioBot",
            author="Author Name",
            title="Direct Release",
            format="m4b",
            size="900MB",
            full_line="!AudioBot Author Name - Direct Release.m4b ::INFO:: 900MB",
        ),
    ]

    releases = source._convert_to_releases(results, content_type="audiobook")

    assert [release.format for release in releases] == ["m4b", "zip"]
    assert all(release.content_type == "audiobook" for release in releases)


def test_search_uses_cached_results_without_opening_a_connection(monkeypatch):
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cached_release = Release(
        source="irc",
        source_id="cached-line",
        title="Cached Result",
    )

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda provider, provider_id, *, content_type: {
            "releases": [cached_release],
            "online_servers": ["AudioBot"],
        },
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cache hit should skip IRC connection")
        ),
    )
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)

    book = BookMetadata(provider="hardcover", provider_id="123", title="Cached Book")
    plan = SimpleNamespace(primary_query="Cached Book")

    releases = source.search(book, plan)

    assert releases == [cached_release]
    assert source._online_servers == {"AudioBot"}


def test_search_no_dcc_offer_releases_connection_and_caches_empty_result(monkeypatch):
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cache_calls: list[dict[str, object]] = []
    released_clients: list[object] = []

    class FakeClient:
        online_servers = {"AudioBot"}

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self, *, timeout: float, result_type: bool, expected_senders: object = None
        ) -> None:
            return None

    client = FakeClient()

    # A search bot is required; make config report one so the channel send path runs.
    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
        }.get(key, ""),
    )
    # Ensure no leftover cooldown entry from a previous test blocks the send.
    irc_source._recent_query_times.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda provider, provider_id, *, content_type: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda provider, provider_id, title, releases, *, content_type, online_servers: (
            cache_calls.append(
                {
                    "provider": provider,
                    "provider_id": provider_id,
                    "title": title,
                    "releases": releases,
                    "content_type": content_type,
                    "online_servers": online_servers,
                }
            )
        ),
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda released_client: released_clients.append(released_client),
    )

    book = BookMetadata(provider="hardcover", provider_id="abc", title="Missing Result")
    plan = SimpleNamespace(primary_query="Missing Result")

    releases = source.search(book, plan, content_type="audiobook")

    assert releases == []
    assert released_clients == [client]
    assert cache_calls == [
        {
            "provider": "hardcover",
            "provider_id": "abc",
            "title": "Missing Result",
            "releases": [],
            "content_type": "audiobook",
            "online_servers": ["AudioBot"],
        }
    ]


def test_search_without_search_bot_never_posts_to_channel(monkeypatch):
    """A bare (unaddressed) query must never reach the channel; refuse to connect."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()

    # Force is_available True so we exercise the in-search guard (defense in depth).
    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda provider, provider_id, *, content_type: None,
    )
    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "",  # not configured
        }.get(key, ""),
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("must not connect/post without a search bot")
        ),
    )

    book = BookMetadata(provider="hardcover", provider_id="nobot", title="No Bot")
    plan = SimpleNamespace(primary_query="No Bot")

    assert source.search(book, plan) == []


def test_search_on_cooldown_returns_cache_without_reposting(monkeypatch):
    """An identical query within the cooldown window must not be re-posted to the channel."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cached_release = Release(source="irc", source_id="cached-line", title="Cached Result")

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
        }.get(key, ""),
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda provider, provider_id, *, content_type: {
            "releases": [cached_release],
            "online_servers": ["AudioBot"],
        },
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("cooldown should skip IRC connection")
        ),
    )

    # Pretend the same query was just posted to the channel.
    irc_source._recent_query_times.clear()
    irc_source._record_query_sent(irc_source._query_cooldown_key("ebooks", "Cooldown Book"))

    book = BookMetadata(provider="hardcover", provider_id="cd", title="Cooldown Book")
    plan = SimpleNamespace(primary_query="Cooldown Book")

    # expand_search=True bypasses the normal top-level cache, forcing the cooldown path.
    releases = source.search(book, plan, expand_search=True)

    assert releases == [cached_release]
    assert source._online_servers == {"AudioBot"}

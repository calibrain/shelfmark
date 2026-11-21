import time
from types import SimpleNamespace

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release
from shelfmark.release_sources.irc.parser import SearchResult
from shelfmark.release_sources.irc.source import IRCReleaseSource


def _plan(title, author="", manual_query=None):
    """A plan stub for tests that only need one to reach search().

    Tests that care how a plan is built use build_release_search_plan instead.
    """
    return SimpleNamespace(
        title_variants=[SimpleNamespace(title=title, author=author)],
        author=author,
        manual_query=manual_query,
    )


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
        lambda cache_key, *_args, **_kwargs: {
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
    plan = _plan("Cached Book")

    releases = source.search(book, plan)

    assert releases == [cached_release]
    assert source._online_servers == {"AudioBot"}


def test_search_no_dcc_offer_releases_connection_and_caches_empty_result(monkeypatch):
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    cache_calls: list[dict[str, object]] = []
    released_clients: list[object] = []

    class FakeClient:
        is_connected = True
        online_servers = {"AudioBot"}

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self,
            *,
            timeout: float,
            result_type: bool,
            expected_senders: object = None,
            expected_query: object = None,
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
    # Ensure no leftover send budget from a previous test blocks the send.
    irc_source._recent_message_sends.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda cache_key, title, releases, *, online_servers=None: cache_calls.append(
            {
                "cache_key": cache_key,
                "title": title,
                "releases": releases,
                "online_servers": online_servers,
            }
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
    plan = _plan("Missing Result")

    releases = source.search(book, plan, content_type="audiobook")

    assert releases == []
    assert released_clients == [client]
    # One query maps to one cache entry (the whole, empty answer), keyed by server:channel:query.
    assert cache_calls == [
        {
            "cache_key": "irc.example.net:ebooks:missing result",
            "title": "Missing Result",
            "releases": [],
            "online_servers": ["AudioBot"],
        }
    ]


def test_audiobook_search_routes_to_configured_audiobook_channel_and_bot(monkeypatch):
    """An audiobook request uses the audiobook channel/bot when configured."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    connection_kwargs: dict[str, object] = {}
    cache_calls: list[dict[str, object]] = []

    class FakeClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self,
            *,
            timeout: float,
            result_type: bool,
            expected_senders: object = None,
            expected_query: object = None,
        ) -> None:
            return None

    client = FakeClient()

    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
            "IRC_AUDIOBOOK_CHANNEL": "bookz",
            "IRC_AUDIOBOOK_SEARCH_BOT": "audiosearch",
        }.get(key, ""),
    )
    irc_source._recent_message_sends.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda cache_key, title, releases, *, online_servers=None: cache_calls.append(
            {"cache_key": cache_key}
        ),
    )

    def fake_get_connection(**kwargs: object) -> FakeClient:
        connection_kwargs.update(kwargs)
        return client

    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        fake_get_connection,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda _client: None,
    )

    book = BookMetadata(provider="hardcover", provider_id="ab", title="Audio Book")
    plan = _plan("Audio Book")

    source.search(book, plan, content_type="audiobook")

    # Joined the audiobook channel, addressed the audiobook search bot, and the cache
    # key is namespaced to the audiobook channel (so it won't collide with the ebook one).
    assert connection_kwargs["channel"] == "bookz"
    assert client.channel == "#bookz"
    assert client.message == "@audiosearch Audio Book"
    assert cache_calls == [{"cache_key": "irc.example.net:bookz:audio book"}]


def test_audiobook_search_reuses_main_bot_when_only_channel_configured(monkeypatch):
    """With an audiobook channel but no audiobook bot, reuse the main search bot."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()

    class FakeClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self,
            *,
            timeout: float,
            result_type: bool,
            expected_senders: object = None,
            expected_query: object = None,
        ) -> None:
            return None

    client = FakeClient()

    monkeypatch.setattr(
        irc_source,
        "_config_text",
        lambda key: {
            "IRC_SERVER": "irc.example.net",
            "IRC_CHANNEL": "ebooks",
            "IRC_NICK": "tester",
            "IRC_SEARCH_BOT": "search",
            "IRC_AUDIOBOOK_CHANNEL": "bookz",
            "IRC_AUDIOBOOK_SEARCH_BOT": "",
        }.get(key, ""),
    )
    irc_source._recent_message_sends.clear()

    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda _client: None,
    )

    book = BookMetadata(provider="hardcover", provider_id="ab2", title="Audio Book")
    plan = _plan("Audio Book")

    source.search(book, plan, content_type="audiobook")

    assert client.channel == "#bookz"
    assert client.message == "@search Audio Book"


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
        lambda cache_key, *_args, **_kwargs: None,
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
    plan = _plan("No Bot")

    assert source.search(book, plan) == []


def test_recent_send_count_caps_and_windows():
    """The send budget counts identical queries and prunes entries outside the window."""
    import shelfmark.release_sources.irc.source as irc_source

    irc_source._recent_message_sends.clear()
    key = irc_source._query_identity("irc.example.net", "ebooks", "Dubliners")
    other = irc_source._query_identity("irc.example.net", "ebooks", "Ulysses")

    assert irc_source._recent_send_count(key) == 0
    for expected in range(1, irc_source.MAX_IDENTICAL_SENDS + 1):
        irc_source._record_message_sent(key)
        assert irc_source._recent_send_count(key) == expected

    # A different query has its own independent budget.
    assert irc_source._recent_send_count(other) == 0

    # Timestamps older than the window are pruned and don't count.
    stale = time.time() - irc_source.IDENTICAL_SEND_WINDOW_SECONDS - 10
    irc_source._recent_message_sends[key] = [stale, stale]
    assert irc_source._recent_send_count(key) == 0


def test_search_send_budget_blocks_repost_and_returns_cache(monkeypatch):
    """Once the exact query hit its per-window send limit, don't re-post; serve cache."""
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
        lambda cache_key, *_args, **_kwargs: {
            "releases": [cached_release],
            "online_servers": ["AudioBot"],
        },
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("send budget should skip IRC connection")
        ),
    )

    # Exhaust the budget for this exact query on this server-channel.
    irc_source._recent_message_sends.clear()
    send_key = irc_source._query_identity("irc.example.net", "ebooks", "Budget Book")
    for _ in range(irc_source.MAX_IDENTICAL_SENDS):
        irc_source._record_message_sent(send_key)

    book = BookMetadata(provider="hardcover", provider_id="cd", title="Budget Book")
    plan = _plan("Budget Book")

    # expand_search=True bypasses the top-level cache, forcing the budget path.
    releases = source.search(book, plan, expand_search=True)

    assert releases == [cached_release]
    assert source._online_servers == {"AudioBot"}


def _search_once(monkeypatch, settings: dict[str, str], content_type: str = "ebook"):
    """Run one uncached search and return what it joined, sent and cached."""
    import shelfmark.release_sources.irc.source as irc_source

    source = IRCReleaseSource()
    connection_kwargs: dict[str, object] = {}
    cache_keys: list[str] = []

    class FakeClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, channel: str, message: str) -> None:
            self.channel = channel
            self.message = message

        def wait_for_dcc(
            self,
            *,
            timeout: float,
            result_type: bool,
            expected_senders: object = None,
            expected_query: object = None,
        ) -> None:
            return None

    client = FakeClient()

    monkeypatch.setattr(irc_source, "_config_text", lambda key: settings.get(key, ""))
    irc_source._recent_message_sends.clear()
    monkeypatch.setattr(source, "is_available", lambda: True)
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results",
        lambda cache_key, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda cache_key, title, releases, *, online_servers=None: cache_keys.append(cache_key),
    )

    def fake_get_connection(**kwargs: object) -> FakeClient:
        connection_kwargs.update(kwargs)
        return client

    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        fake_get_connection,
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.release_connection",
        lambda _client: None,
    )

    book = BookMetadata(provider="hardcover", provider_id="1", title="Some Book")
    source.search(book, _plan("Some Book"), content_type=content_type)
    return connection_kwargs["channel"], client.channel, cache_keys


_BASE_SETTINGS = {
    "IRC_SERVER": "irc.example.net",
    "IRC_NICK": "tester",
    "IRC_SEARCH_BOT": "search",
}


def test_channel_setting_with_hash_prefix_joins_the_same_channel(monkeypatch):
    """A stored "#ebooks" joined "##ebooks", an almost empty channel, and found nothing.

    The setting asks for the name without the #, but that is not how people write a
    channel, and nothing reported the mistake.
    """
    bare = _search_once(monkeypatch, {**_BASE_SETTINGS, "IRC_CHANNEL": "ebooks"})
    prefixed = _search_once(monkeypatch, {**_BASE_SETTINGS, "IRC_CHANNEL": " #ebooks "})

    assert prefixed == bare
    assert prefixed == ("ebooks", "#ebooks", ["irc.example.net:ebooks:some book"])


def test_audiobook_channel_setting_with_hash_prefix_joins_the_same_channel(monkeypatch):
    """The audiobook channel is read the same way as the main one, so it had the same trap."""
    settings = {**_BASE_SETTINGS, "IRC_CHANNEL": "ebooks"}
    bare = _search_once(monkeypatch, {**settings, "IRC_AUDIOBOOK_CHANNEL": "bookz"}, "audiobook")
    prefixed = _search_once(
        monkeypatch, {**settings, "IRC_AUDIOBOOK_CHANNEL": "#bookz"}, "audiobook"
    )

    assert prefixed == bare
    assert prefixed == ("bookz", "#bookz", ["irc.example.net:bookz:some book"])


def _configured_search(monkeypatch, client):
    """Wire a search to a fake client, with nothing cached and nothing recorded."""
    import shelfmark.release_sources.irc.source as irc_source

    cache_calls: list[object] = []
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
    irc_source._recent_message_sends.clear()
    monkeypatch.setattr(irc_source, "_enforce_rate_limit", lambda: None)
    monkeypatch.setattr(irc_source, "_emit_status", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results", lambda *_a, **_k: None
    )
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.cache_results",
        lambda *args, **_kwargs: cache_calls.append(args),
    )
    manager = "shelfmark.release_sources.irc.connection_manager.connection_manager"
    monkeypatch.setattr(f"{manager}.get_connection", lambda **_kwargs: client)
    monkeypatch.setattr(f"{manager}.release_connection", lambda _client: None)
    monkeypatch.setattr(f"{manager}.close_connection", lambda _client: None)
    return cache_calls


def test_search_waits_for_a_results_file_named_for_its_own_query(monkeypatch):
    waits: list[object] = []

    class FakeClient:
        is_connected = True
        online_servers = {"AudioBot"}

        def send_message(self, channel: str, message: str) -> None:
            pass

        def wait_for_dcc(self, **kwargs: object) -> None:
            waits.append(kwargs.get("expected_query"))

    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)
    _configured_search(monkeypatch, FakeClient())

    source.search(
        BookMetadata(provider="hardcover", provider_id="a", title="Some Title"), _plan("Some Title")
    )

    assert waits == ["Some Title"]


def test_search_on_a_dropped_connection_is_an_error_and_is_not_cached(monkeypatch):
    """The server dropping the link mid-search is not "no results".

    Cached as an empty answer it hid the book for a day, and returned as an empty list it
    counted towards the request's search backoff.
    """
    import pytest

    from shelfmark.release_sources import SourceUnavailableError

    class FakeClient:
        is_connected = False
        online_servers = {"AudioBot"}

        def send_message(self, channel: str, message: str) -> None:
            pass

        def wait_for_dcc(self, **_kwargs: object) -> None:
            return None

    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)
    cache_calls = _configured_search(monkeypatch, FakeClient())
    closed: list[object] = []
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.close_connection",
        closed.append,
    )

    with pytest.raises(SourceUnavailableError):
        source.search(
            BookMetadata(provider="hardcover", provider_id="a", title="Some Title"),
            _plan("Some Title"),
        )

    assert cache_calls == []
    assert len(closed) == 1  # the dead client is not handed out again


def test_identical_searches_queued_on_the_lock_post_the_query_only_once(monkeypatch):
    """The send budget is checked once the lock is held, not before waiting for it."""
    import threading

    import shelfmark.release_sources.irc.source as irc_source

    first_waiting = threading.Event()
    release_first = threading.Event()
    sent: list[str] = []

    class FakeClient:
        is_connected = True
        online_servers = {"AudioBot"}

        def send_message(self, channel: str, message: str) -> None:
            sent.append(message)

        def wait_for_dcc(self, **_kwargs: object) -> None:
            return None

    client = FakeClient()
    _configured_search(monkeypatch, client)

    def slow_get_connection(**_kwargs: object) -> FakeClient:
        first_waiting.set()  # holding the lock, nothing sent yet
        release_first.wait(5)
        return client

    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.get_connection",
        slow_get_connection,
    )
    monkeypatch.setattr(irc_source, "MAX_IDENTICAL_SENDS", 1)
    book = BookMetadata(provider="hardcover", provider_id="a", title="Some Title")

    def run() -> None:
        IRCReleaseSource().search(book, _plan("Some Title"))

    first = threading.Thread(target=run)
    first.start()
    assert first_waiting.wait(5)
    second = threading.Thread(target=run)
    second.start()
    time.sleep(0.2)  # let it pass the pre-lock budget check and queue on the lock
    release_first.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()  # a lock that was never released would leave it blocked
    assert len(sent) == 1


def test_search_gives_up_waiting_for_a_busy_connection_at_the_deadline(monkeypatch):
    import pytest

    from shelfmark.core import search_deadline
    from shelfmark.release_sources import SourceUnavailableError
    from shelfmark.release_sources.irc.connection_manager import connection_manager

    _configured_search(monkeypatch, object())
    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)
    book = BookMetadata(provider="hardcover", provider_id="a", title="Some Title")

    connection_manager.io_lock.acquire()  # a download is mid-request
    try:
        started = time.monotonic()
        with search_deadline.search_deadline(0.3), pytest.raises(SourceUnavailableError):
            source.search(book, _plan("Some Title"))
        assert time.monotonic() - started < 5
    finally:
        connection_manager.io_lock.release()


def test_search_rechecks_the_cache_once_it_has_the_lock(monkeypatch):
    """A search queued behind an identical one is answered by the cache it just filled."""
    sent: list[str] = []

    class FakeClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, _channel: str, message: str) -> None:
            sent.append(message)

    _configured_search(monkeypatch, FakeClient())
    answers = iter([None, {"releases": [], "online_servers": []}])  # miss, then filled
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.cache.get_cached_results", lambda *_a: next(answers)
    )
    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)

    source.search(BookMetadata(provider="hardcover", provider_id="a", title="T"), _plan("T"))

    assert sent == []


def test_a_connection_failure_is_unavailable_not_no_results(monkeypatch):
    """Failing to connect or to send says nothing about the book, so it is never cached."""
    import pytest

    from shelfmark.release_sources import SourceUnavailableError

    class BrokenClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, _channel: str, _message: str) -> None:
            raise BrokenPipeError

    cache_calls = _configured_search(monkeypatch, BrokenClient())
    closed: list[object] = []
    monkeypatch.setattr(
        "shelfmark.release_sources.irc.connection_manager.connection_manager.close_connection",
        closed.append,
    )
    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)

    with pytest.raises(SourceUnavailableError):
        source.search(BookMetadata(provider="hardcover", provider_id="a", title="T"), _plan("T"))

    assert cache_calls == []
    assert len(closed) == 1


def test_a_denied_search_is_unavailable_not_no_results(monkeypatch):
    import pytest

    from shelfmark.release_sources import SourceUnavailableError
    from shelfmark.release_sources.irc.client import IRCError

    class DeniedClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, _channel: str, _message: str) -> None:
            pass

        def wait_for_dcc(self, **_kwargs: object) -> None:
            msg = "Search results already waiting to be recieved. Search denied."
            raise IRCError(msg)

    cache_calls = _configured_search(monkeypatch, DeniedClient())
    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)

    with pytest.raises(SourceUnavailableError, match="Search denied"):
        source.search(BookMetadata(provider="hardcover", provider_id="a", title="T"), _plan("T"))

    assert cache_calls == []


def _search_with(monkeypatch, client):
    """Run one search against `client`; return (cache calls, error or None)."""
    from shelfmark.release_sources import SourceUnavailableError

    cache_calls = _configured_search(monkeypatch, client)
    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)
    try:
        source.search(BookMetadata(provider="hardcover", provider_id="a", title="T"), _plan("T"))
    except SourceUnavailableError as e:
        return cache_calls, e
    return cache_calls, None


def test_a_failed_results_transfer_is_unavailable_and_keeps_the_connection(monkeypatch):
    import shelfmark.release_sources.irc.source as irc_source
    from shelfmark.release_sources.irc.dcc import DCCError, DCCOffer

    class OfferingClient:
        is_connected = True
        online_servers: set[str] = set()

        def send_message(self, _channel: str, _message: str) -> None:
            pass

        def wait_for_dcc(self, **_kwargs: object) -> DCCOffer:
            return DCCOffer(filename="Bot_results_for__T.txt.zip", ip="8.8.8.8", port=443, size=1)

    def reset(*_args: object, **_kwargs: object) -> None:
        msg = "Failed reading from 8.8.8.8:443: connection reset"
        raise DCCError(msg)

    monkeypatch.setattr(irc_source, "download_dcc", reset)
    closed: list[object] = []
    manager = "shelfmark.release_sources.irc.connection_manager.connection_manager"
    cache_calls = _configured_search(monkeypatch, OfferingClient())
    monkeypatch.setattr(f"{manager}.close_connection", closed.append)
    source = IRCReleaseSource()
    monkeypatch.setattr(source, "is_available", lambda: True)

    import pytest

    from shelfmark.release_sources import SourceUnavailableError

    with pytest.raises(SourceUnavailableError, match="transfer failed"):
        source.search(BookMetadata(provider="hardcover", provider_id="a", title="T"), _plan("T"))

    assert cache_calls == []
    assert closed == []  # the IRC side is fine


class _WaitingClient:
    """Answers nothing: the wait runs to its timeout. Or, with `sorry`, says no results.

    `sorry_left` delays the "Sorry" until that many seconds of the wait remain.
    """

    is_connected = True
    online_servers: set[str] = set()

    def __init__(self, *, sorry: bool = False, sorry_left: float | None = None) -> None:
        self.sorry = sorry or sorry_left is not None
        self.sorry_left = sorry_left
        self.sent: list[str] = []
        self.timeouts: list[float] = []
        self.last_wait_timed_out = False

    def send_message(self, _channel: str, message: str) -> None:
        self.sent.append(message)

    def wait_for_dcc(self, *, timeout: float, **_kwargs: object) -> None:
        self.timeouts.append(timeout)
        self.last_wait_timed_out = not self.sorry
        if not self.sorry:
            time.sleep(timeout)
        elif self.sorry_left is not None:
            time.sleep(max(0.0, timeout - self.sorry_left))


def test_the_wait_for_results_stops_at_the_search_deadline_and_is_not_cached(monkeypatch):
    from shelfmark.core import search_deadline

    client = _WaitingClient()
    with search_deadline.search_deadline(1.5):
        cache_calls, error = _search_with(monkeypatch, client)

    assert error is not None
    assert "ran out of time" in str(error)
    assert client.timeouts and client.timeouts[0] <= 1.5
    assert cache_calls == []


def test_nothing_is_sent_once_the_search_deadline_is_spent(monkeypatch):
    from shelfmark.core import search_deadline

    client = _WaitingClient()
    with search_deadline.search_deadline(0.5):
        cache_calls, error = _search_with(monkeypatch, client)

    assert error is not None
    assert client.sent == []
    assert cache_calls == []


def test_a_real_no_results_inside_a_shortened_wait_is_still_cached(monkeypatch):
    from shelfmark.core import search_deadline

    client = _WaitingClient(sorry=True)
    with search_deadline.search_deadline(30):
        cache_calls, error = _search_with(monkeypatch, client)

    assert error is None
    assert client.timeouts[0] < 60
    assert len(cache_calls) == 1


def test_a_no_results_reply_just_before_the_deadline_is_cached(monkeypatch):
    from shelfmark.core import search_deadline

    client = _WaitingClient(sorry_left=0.3)  # under the 1s margin once the wait ends
    with search_deadline.search_deadline(1.5):
        cache_calls, error = _search_with(monkeypatch, client)

    assert error is None
    assert len(cache_calls) == 1

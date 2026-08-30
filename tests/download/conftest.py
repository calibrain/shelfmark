"""Shared fixtures for the download tests."""

import pytest


@pytest.fixture(autouse=True)
def _clear_warmup_user_search_flag():
    """Start every download test with the warm-up's "a user searched" flag clear.

    The flag is process-global by design - the warm-up is a one-shot per process, and
    once a real search has run there is nothing left to pre-solve. That makes it leak
    between tests: `/api/releases` sets it, so any API test sharing an xdist worker with
    the warm-up tests would otherwise decide the warm-up for them. It surfaced as
    test_search_warmup.py failing only on CI, where the workers divide up differently
    than they happen to locally.
    """
    from shelfmark.download import warmup

    warmup._user_search_seen.clear()
    yield
    warmup._user_search_seen.clear()

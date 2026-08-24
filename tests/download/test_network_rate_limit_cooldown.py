"""Tests for the per-host 429 backoff.

A 429 is the origin throttling this IP, which a challenge solve cannot clear - so the
host is sidelined for a growing window (2 -> 5 -> 10 -> 15 -> 30 min) that escalates
only when the host throttles us again *after* a full window has already elapsed. Mirror
selection skips a cooling-down host; the bypasser refuses to solve one. These guard that
the ladder climbs, caps, resets after a long clear gap, and stays per host.
"""

import shelfmark.download.network as network

MIRRORS = ["https://aa-one.test", "https://aa-two.test", "https://aa-three.test"]
LADDER = (120.0, 300.0, 600.0, 900.0, 1800.0)


def _fresh(monkeypatch, *, urls=None, start=1000.0):
    """Reset cooldown state and install a controllable monotonic clock.

    Returns a ``clock`` list whose single element is the current fake time; mutate
    ``clock[0]`` to advance it.
    """
    clock = [start]
    monkeypatch.setattr(network, "_host_cooldowns", {})
    monkeypatch.setattr(network.time, "monotonic", lambda: clock[0])
    if urls is not None:
        monkeypatch.setattr(network, "_initialized", True)
        monkeypatch.setattr(network, "_aa_urls", list(urls))
        monkeypatch.setattr(network, "_aa_base_url", urls[0])
        monkeypatch.setattr(network, "_current_aa_url_index", 0)
        monkeypatch.setattr(network, "_dead_aa_urls", set())
    return clock


def test_first_429_arms_the_two_minute_step(monkeypatch):
    _fresh(monkeypatch)

    assert network.note_rate_limited("https://h.test/search?q=dune") == 120.0
    # Keyed by host: any URL on the same host reads the same cooldown.
    assert network.is_host_cooling_down("https://h.test/other") is True
    assert network.host_cooldown_remaining("https://h.test") == 120.0


def test_cooldown_expires_after_the_window(monkeypatch):
    clock = _fresh(monkeypatch)

    network.note_rate_limited("https://h.test")
    clock[0] += 121

    assert network.is_host_cooling_down("https://h.test") is False
    assert network.host_cooldown_remaining("https://h.test") == 0.0


def test_re_offense_after_expiry_climbs_the_ladder(monkeypatch):
    clock = _fresh(monkeypatch)

    for expected in LADDER:
        assert network.note_rate_limited("https://h.test") == expected
        # Wait the whole window out, then get throttled again -> next step.
        clock[0] += expected + 1

    # Top step holds: further re-offenses stay at 30 minutes, never beyond.
    assert network.note_rate_limited("https://h.test") == 1800.0


def test_429_while_still_cooling_does_not_escalate(monkeypatch):
    clock = _fresh(monkeypatch)

    assert network.note_rate_limited("https://h.test") == 120.0
    clock[0] += 30  # still inside the first window

    # Same episode: keep the remaining wait, do not advance the ladder.
    assert network.note_rate_limited("https://h.test") == 90.0
    clock[0] += 91  # let the (unchanged) 2-min window lapse
    # The next post-expiry 429 is step 2, proving the mid-window hit did not escalate.
    assert network.note_rate_limited("https://h.test") == 300.0


def test_long_clear_gap_restarts_the_ladder(monkeypatch):
    clock = _fresh(monkeypatch)

    network.note_rate_limited("https://h.test")  # step 1: 120s
    clock[0] += 120 + 1801  # window lapses, then a gap longer than the reset threshold

    assert network.note_rate_limited("https://h.test") == 120.0


def test_backoff_is_per_host(monkeypatch):
    _fresh(monkeypatch)

    network.note_rate_limited("https://a.test")
    assert network.is_host_cooling_down("https://a.test") is True
    assert network.is_host_cooling_down("https://b.test") is False


def test_available_mirrors_skip_cooling_hosts(monkeypatch):
    _fresh(monkeypatch, urls=MIRRORS)

    network.note_rate_limited(MIRRORS[1])
    assert network.get_available_aa_urls() == [MIRRORS[0], MIRRORS[2]]


def test_all_mirrors_cooling_falls_back_to_full_list(monkeypatch):
    _fresh(monkeypatch, urls=MIRRORS)

    for mirror in MIRRORS:
        network.note_rate_limited(mirror)
    # Never leave selection with nowhere to point; the bypasser fail-fast handles this.
    assert network.get_available_aa_urls() == MIRRORS


def test_clear_host_cooldowns_resets_everything(monkeypatch):
    _fresh(monkeypatch)

    network.note_rate_limited("https://h.test")
    network.clear_host_cooldowns()
    assert network.is_host_cooling_down("https://h.test") is False


def test_urls_without_a_host_are_ignored(monkeypatch):
    _fresh(monkeypatch)

    assert network.note_rate_limited("not-a-url") == 0.0
    assert network.is_host_cooling_down("not-a-url") is False

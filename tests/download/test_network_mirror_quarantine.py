"""Tests for AA mirror quarantine: dead mirrors leave the rotation, live ones stay.

The distinction these guard is the whole point of the feature. A mirror that answers
403 (DDoS-Guard) is alive and holds our bypass clearance, so rotating off it makes the
next search solve a fresh challenge on a domain we have no cookie for. A mirror that
NXDOMAINs, refuses the connection, or answers 200 with a parking page is not a mirror
at all and must never be tried again this session.
"""

import requests


def _fresh_network(monkeypatch, urls: list[str], *, auto: bool = True):
    import shelfmark.download.network as network

    monkeypatch.setattr(network, "_initialized", True)
    monkeypatch.setattr(network, "_aa_urls", list(urls))
    monkeypatch.setattr(network, "_aa_base_url", urls[0])
    monkeypatch.setattr(network, "_current_aa_url_index", 0)
    monkeypatch.setattr(network, "_dead_aa_urls", set())
    monkeypatch.setattr(network, "_save_state", lambda **kwargs: None)
    monkeypatch.setattr(network, "is_aa_auto_mode", lambda: auto)
    return network


MIRRORS = ["https://aa-one.test", "https://aa-two.test", "https://aa-three.test"]


def test_quarantined_mirror_leaves_the_available_list(monkeypatch):
    network = _fresh_network(monkeypatch, MIRRORS)

    assert network.mark_aa_url_dead("https://aa-two.test", "NXDOMAIN") is True
    assert network.get_available_aa_urls() == ["https://aa-one.test", "https://aa-three.test"]
    assert network.get_dead_aa_urls() == {"https://aa-two.test"}


def test_quarantine_accepts_a_full_request_url(monkeypatch):
    """Callers hold the failing request URL, not the bare mirror base."""
    network = _fresh_network(monkeypatch, MIRRORS)

    assert network.mark_aa_url_dead("https://aa-two.test/search?q=dune", "parked") is True
    assert "https://aa-two.test" in network.get_dead_aa_urls()


def test_quarantine_is_idempotent(monkeypatch):
    network = _fresh_network(monkeypatch, MIRRORS)

    assert network.mark_aa_url_dead("https://aa-two.test", "NXDOMAIN") is True
    assert network.mark_aa_url_dead("https://aa-two.test", "NXDOMAIN") is False
    assert network.get_available_aa_urls() == ["https://aa-one.test", "https://aa-three.test"]


def test_last_surviving_mirror_is_never_quarantined(monkeypatch):
    """Misclassification must not leave the app with nowhere to search."""
    network = _fresh_network(monkeypatch, MIRRORS)

    assert network.mark_aa_url_dead("https://aa-one.test", "NXDOMAIN") is True
    assert network.mark_aa_url_dead("https://aa-two.test", "NXDOMAIN") is True
    assert network.mark_aa_url_dead("https://aa-three.test", "NXDOMAIN") is False
    assert network.get_available_aa_urls() == ["https://aa-three.test"]


def test_selector_skips_quarantined_mirror_when_rotating(monkeypatch):
    network = _fresh_network(monkeypatch, MIRRORS)
    selector = network.AAMirrorSelector()

    new_base, action = selector.next_mirror_or_rotate_dns(fatal=True, reason="NXDOMAIN")

    assert action == "mirror"
    # Landed on the next live mirror, not skipped past it onto the third.
    assert new_base == "https://aa-two.test"
    assert "https://aa-one.test" in network.get_dead_aa_urls()
    assert selector.rewrite("https://aa-one.test/search") == "https://aa-two.test/search"


def test_non_fatal_rotation_keeps_the_mirror(monkeypatch):
    """A 5xx or a challenge rotates but must not burn the mirror."""
    network = _fresh_network(monkeypatch, MIRRORS)
    selector = network.AAMirrorSelector()

    selector.next_mirror_or_rotate_dns()

    assert network.get_dead_aa_urls() == set()
    assert network.get_available_aa_urls() == MIRRORS


def test_dns_reset_does_not_resurrect_quarantined_mirrors(monkeypatch):
    """A new DNS provider cannot revive a parked domain, so it stays skipped."""
    network = _fresh_network(monkeypatch, MIRRORS)
    monkeypatch.setattr(network, "rotate_dns_provider", lambda: True)
    monkeypatch.setattr(network, "_get_configured_aa_url", lambda: "auto")
    network.mark_aa_url_dead("https://aa-one.test", "parked")

    assert network.rotate_dns_and_reset_aa() is True
    assert network.get_aa_base_url() == "https://aa-two.test"


def test_editing_the_mirror_list_clears_quarantine(monkeypatch):
    """Quarantine decisions were made about a list the user has now changed."""
    network = _fresh_network(monkeypatch, MIRRORS)
    network.mark_aa_url_dead("https://aa-two.test", "parked")
    monkeypatch.setattr(network, "_build_aa_urls", lambda: [*MIRRORS, "https://aa-four.test"])
    monkeypatch.setattr(network, "_get_configured_aa_url", lambda: "auto")
    monkeypatch.setattr(network, "state", {"aa_base_url": "https://aa-one.test"})

    network._initialize_aa_state()

    assert network.get_dead_aa_urls() == set()


def test_reinit_with_an_unchanged_list_keeps_quarantine(monkeypatch):
    """Re-init happens constantly (settings sync, DNS rotation, helper startup).

    Clearing quarantine on every one of those resurrects a parked mirror mid-session,
    which is exactly the bug this guards: the mirror gets re-elected and the next
    search pays for it again.
    """
    network = _fresh_network(monkeypatch, MIRRORS)
    network.mark_aa_url_dead("https://aa-two.test", "parked")
    monkeypatch.setattr(network, "_build_aa_urls", lambda: list(MIRRORS))
    monkeypatch.setattr(network, "_get_configured_aa_url", lambda: "auto")
    monkeypatch.setattr(network, "state", {"aa_base_url": "https://aa-one.test"})

    network._initialize_aa_state()

    assert network.get_dead_aa_urls() == {"https://aa-two.test"}


# --------------------------------------------------------------------------- #
# Failure classification
# --------------------------------------------------------------------------- #
def _http_error(status: int) -> requests.exceptions.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(response=response)


def test_dns_failure_is_fatal_for_the_mirror():
    import shelfmark.download.http as http

    exc = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='aa.test', port=443): Max retries exceeded "
        "(Caused by NameResolutionError(\"Failed to resolve 'aa.test'\"))"
    )
    assert http._fatal_mirror_reason(exc) == "DNS does not resolve"


def test_connection_refused_is_fatal_for_the_mirror():
    import shelfmark.download.http as http

    exc = requests.exceptions.ConnectionError("Connection refused")
    assert http._fatal_mirror_reason(exc) == "connection refused"


def test_gone_and_legal_block_are_fatal():
    import shelfmark.download.http as http

    assert http._fatal_mirror_reason(_http_error(410)) == "HTTP 410"
    assert http._fatal_mirror_reason(_http_error(451)) == "HTTP 451"


def test_timeout_is_not_fatal():
    """A slow mirror is still a mirror - and may hold our bypass clearance."""
    import shelfmark.download.http as http

    assert http._fatal_mirror_reason(requests.exceptions.ConnectTimeout("timed out")) is None
    assert http._fatal_mirror_reason(requests.exceptions.ReadTimeout("timed out")) is None


def test_challenge_and_server_errors_are_not_fatal():
    import shelfmark.download.http as http

    for status in (403, 429, 500, 502, 503):
        assert http._fatal_mirror_reason(_http_error(status)) is None


def test_startup_probe_skips_quarantined_mirrors(monkeypatch):
    """Re-init must not re-probe (or re-elect) a mirror already known to be dead.

    A parking page answers 200, so an unfiltered probe elects it every single time
    the app re-initialises - one wasted request per re-init, forever.
    """
    import requests

    network = _fresh_network(monkeypatch, MIRRORS)
    network.mark_aa_url_dead("https://aa-one.test", "parked")
    probed: list[str] = []

    def fake_get(url, **_kwargs):
        probed.append(url)
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(network.requests, "get", fake_get)
    monkeypatch.setattr(network, "_build_aa_urls", lambda: list(MIRRORS))
    monkeypatch.setattr(network, "_get_configured_aa_url", lambda: "auto")
    monkeypatch.setattr(network, "state", {})
    monkeypatch.setattr(network, "get_proxies", lambda _url: None)
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: True)

    network._initialize_aa_state()

    assert "https://aa-one.test" not in probed
    assert network.get_aa_base_url() == "https://aa-two.test"


def test_startup_probe_does_not_restore_a_quarantined_mirror(monkeypatch):
    """Saved state can name a mirror that has since been quarantined."""
    network = _fresh_network(monkeypatch, MIRRORS)
    network.mark_aa_url_dead("https://aa-one.test", "parked")
    monkeypatch.setattr(network, "_build_aa_urls", lambda: list(MIRRORS))
    monkeypatch.setattr(network, "_get_configured_aa_url", lambda: "auto")
    monkeypatch.setattr(network, "state", {"aa_base_url": "https://aa-one.test"})
    monkeypatch.setattr(network, "get_proxies", lambda _url: None)
    monkeypatch.setattr(network, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(network.requests, "get", lambda *a, **k: (_ for _ in ()).throw(OSError()))

    network._initialize_aa_state()

    assert network.get_aa_base_url() != "https://aa-one.test"

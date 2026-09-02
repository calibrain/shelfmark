"""A challenge page is a failed solve, whatever verdict the solver reports on itself.

Regression for #1292. FlareSolverr answers "Challenge solved!" for anything it does not
recognise as a Cloudflare challenge, and DDoS-Guard's manual CAPTCHA page is one such
thing. The external bypasser logged a warning that the solve had not cleared the
protection and then returned the page as a success anyway, which had three consequences:
the retry-and-rotate loop that could still have reached a working mirror was never
entered, the CAPTCHA page's own __ddg cookies were filed as that host's clearance, and
the user was told to go and check a bypasser that was working perfectly.
"""

import pytest

from shelfmark.bypass import ChallengeNotSolvedError

# Verbatim from the annas-archive.pk page in #1292, trimmed to the markers. This is the
# *manual* CAPTCHA - "could not verify your browser automatically" - not the ~900 byte
# JS interstitial that a browser clears on its own.
DDOS_GUARD_CAPTCHA = (
    '<html><head><title>DDOS-GUARD</title><meta charset="utf-8">'
    '<link rel="stylesheet" href="/.well-known/ddos-guard/ddg-captcha-page/index.css">'
    '<script defer="defer" src="/.well-known/ddos-guard/ddg-captcha-page/index.js"></script>'
    '</head><body><div class="container"><h1 id="title">Checking your browser before '
    'accessing annas-archive.pk</h1><p id="description">Sorry, we could not verify your '
    "browser automatically. Complete the manual check to continue</p>"
    '<div id="ddg-captcha"></div></div></body></html>'
)


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _stub_solution(monkeypatch, external_bypasser, solution: dict) -> None:
    """Answer every bypass with `solution`, with config and SSL stubbed out."""

    def fake_get(key, default=""):
        values = {
            "EXT_BYPASSER_URL": "https://bypass.example",
            "EXT_BYPASSER_PATH": "/v1",
            "EXT_BYPASSER_TIMEOUT": 60000,
        }
        return values.get(key, default)

    monkeypatch.setattr(external_bypasser.config, "get", fake_get)
    monkeypatch.setattr(
        external_bypasser.requests,
        "post",
        # "Challenge solved!" is the solver's verdict; the page is the evidence.
        lambda *_a, **_k: _FakeResponse(
            {"status": "ok", "message": "Challenge solved!", "solution": solution}
        ),
    )
    monkeypatch.setattr(external_bypasser, "get_ssl_verify", lambda _url: False)


def test_a_captcha_page_is_reported_as_unsolved_not_returned(monkeypatch):
    import shelfmark.bypass.external_bypasser as external_bypasser

    _stub_solution(monkeypatch, external_bypasser, {"response": DDOS_GUARD_CAPTCHA})

    with pytest.raises(ChallengeNotSolvedError) as excinfo:
        external_bypasser._fetch_via_bypasser("https://annas-archive.pk/search?q=dune")

    # The marker travels with the failure so the user-facing message can name it.
    assert str(excinfo.value) == "/.well-known/ddos-guard/"


def test_cookies_from_a_captcha_page_are_never_filed_as_clearance(monkeypatch):
    """They belong to an unsolved check, so replaying them only re-arms the gate."""
    import shelfmark.bypass.cookie_store as cookie_store
    import shelfmark.bypass.external_bypasser as external_bypasser

    monkeypatch.setattr(cookie_store, "_cf_cookies", {})
    monkeypatch.setattr(cookie_store, "_cf_user_agents", {})
    _stub_solution(
        monkeypatch,
        external_bypasser,
        {
            "response": DDOS_GUARD_CAPTCHA,
            "userAgent": "Mozilla/5.0 (solver)",
            "cookies": [{"name": "__ddg1_", "value": "from-a-captcha"}],
        },
    )

    with pytest.raises(ChallengeNotSolvedError):
        external_bypasser._fetch_via_bypasser("https://annas-archive.pk/search?q=dune")

    assert cookie_store.get_cf_cookies_for_domain("annas-archive.pk") == {}
    assert cookie_store.get_cf_user_agent_for_domain("annas-archive.pk") is None


class _FakeSelector:
    """Two mirrors, rotated on demand - each is its own DDoS-Guard host."""

    def __init__(self) -> None:
        self.current_base = "https://mirror-one.example"
        self.rotate_calls = 0

    def rewrite(self, url: str) -> str:
        return url.replace("https://orig.example", self.current_base, 1)

    def next_mirror_or_rotate_dns(self) -> tuple[str | None, str]:
        self.rotate_calls += 1
        self.current_base = "https://mirror-two.example"
        return self.current_base, "mirror"


def _no_sleeping(monkeypatch, external_bypasser) -> None:
    monkeypatch.setattr(external_bypasser, "_sleep_with_cancellation", lambda _seconds, _flag: None)


def test_an_unsolved_challenge_rotates_to_the_next_mirror(monkeypatch):
    """The recovery the old code skipped by calling the CAPTCHA page a success."""
    import shelfmark.bypass.external_bypasser as external_bypasser

    _no_sleeping(monkeypatch, external_bypasser)
    fetched: list[str] = []

    def fake_fetch(url: str) -> str | None:
        fetched.append(url)
        if "mirror-one" in url:
            raise ChallengeNotSolvedError("/.well-known/ddos-guard/")
        return "<html>real page</html>"

    monkeypatch.setattr(external_bypasser, "_fetch_via_bypasser", fake_fetch)

    selector = _FakeSelector()
    result = external_bypasser.get_bypassed_page("https://orig.example/search", selector=selector)

    assert result == "<html>real page</html>"
    assert fetched == [
        "https://mirror-one.example/search",
        "https://mirror-two.example/search",
    ]
    assert selector.rotate_calls == 1


def test_every_attempt_challenged_blames_the_host_not_the_bypasser(monkeypatch):
    import shelfmark.bypass.external_bypasser as external_bypasser

    _no_sleeping(monkeypatch, external_bypasser)

    def always_challenged(_url: str) -> str | None:
        raise ChallengeNotSolvedError("/.well-known/ddos-guard/")

    monkeypatch.setattr(external_bypasser, "_fetch_via_bypasser", always_challenged)

    with pytest.raises(ChallengeNotSolvedError) as excinfo:
        external_bypasser.get_bypassed_page("https://orig.example/search", selector=_FakeSelector())

    message = str(excinfo.value)
    assert "manual CAPTCHA" in message
    assert "the bypasser itself is working" in message


def test_an_unreachable_bypasser_still_reports_as_such(monkeypatch):
    """The other cause must stay distinguishable: None, not an unsolved challenge."""
    import shelfmark.bypass.external_bypasser as external_bypasser

    _no_sleeping(monkeypatch, external_bypasser)
    monkeypatch.setattr(external_bypasser, "_fetch_via_bypasser", lambda _url: None)

    assert (
        external_bypasser.get_bypassed_page("https://orig.example/search", selector=_FakeSelector())
        is None
    )


def test_html_get_page_surfaces_the_host_as_the_cause(monkeypatch):
    """The message the user actually reads must not send them to fix FlareSolverr.

    `_run_bypasser`'s generic handler says "the protection bypasser failed", and the
    search layer's give-up used to add "check that the bypasser is reachable and
    working" - which is what #1292 spent its investigation doing.
    """
    import shelfmark.download.http as http
    import shelfmark.download.network as network

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: True)
    monkeypatch.setattr(http.network, "should_rotate_dns_for_url", lambda _url: True)

    def challenged(*_args, **_kwargs):
        msg = "the site kept answering with a protection challenge - manual CAPTCHA"
        raise ChallengeNotSolvedError(msg)

    monkeypatch.setattr(http, "get_bypassed_page", challenged)

    statuses: list[tuple[str, str | None]] = []
    selector = network.AAMirrorSelector()

    html = http.html_get_page(
        "https://annas-archive.pk/search?q=dune",
        retry=1,
        selector=selector,
        status_callback=lambda stage, detail: statuses.append((stage, detail)),
        use_bypasser=True,
        success_delay=0,
    )

    assert html == ""
    assert selector.last_failure is not None
    assert "manual CAPTCHA" in selector.last_failure
    assert "reachable" not in selector.last_failure
    assert ("error", "the site kept answering with a protection challenge - manual CAPTCHA") in (
        statuses
    )

"""Cluster 2/3 — search relevance and Anna's Archive HTML-parse robustness.

The recurring root cause: AA changes its DOM and the hardcoded-index parser
silently returns zero rows, which users experience as "All download sources
failed" (#1028) or "book not found" (#198, #293). Evidence of brittleness:
#878/#879/#880 (hardcoded indices/selectors), plus the repeated
"Fix AA ... after they changed layout" PRs.

These run under ``baseline`` (direct connection to the fake AA) so the assertions
isolate parsing from egress concerns.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.profiles("baseline")


def test_search_returns_parsed_releases(client) -> None:
    """Happy path: the parser turns the AA results table into releases."""
    resp = client.direct_search("Mistborn")
    assert resp.status_code == 200, resp.text
    releases = client.releases_from(resp)
    assert releases, "expected at least one parsed release from the AA results table"
    titles = " ".join(str(r.get("title", "")) for r in releases)
    assert "Mistborn" in titles, f"query not reflected in parsed titles: {titles[:200]}"


def test_layout_drift_fails_loudly_not_silently(client) -> None:
    """When AA's DOM changes so no row parses, the app must surface a clear
    failure — NOT an empty 200 that reads as 'book does not exist'.

    Regression guard for #878/#879/#880 and the layout-change PRs.
    """
    resp = client.direct_search("Mistborn", inject="layout_drift")
    releases = client.releases_from(resp)
    # Acceptable behaviours: an explicit error status, OR a 200 with an error
    # field. NOT acceptable: 200 + empty releases with no signal.
    if resp.status_code == 200:
        body = (
            resp.json()
            if resp.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        has_error_signal = bool(body.get("error")) or bool(body.get("source_errors"))
        assert not releases, "parser unexpectedly produced releases from drifted DOM"
        assert has_error_signal, (
            "layout drift produced a silent empty 200 — the app must signal that "
            "the source could not be parsed (regression for #878/#879/#880)"
        )
    else:
        assert resp.status_code >= 400, resp.status_code


def test_no_files_string_alongside_real_results(client) -> None:
    """A real results table that also contains the literal 'No files found.'
    must still yield releases (false-positive guard)."""
    resp = client.direct_search("Mistborn", inject="no_files")
    assert resp.status_code == 200, resp.text
    assert client.releases_from(resp), (
        "'No files found.' substring caused a false-positive empty result"
    )


def test_genuinely_empty_results_handled_cleanly(client) -> None:
    """A true 'No files found.' page yields zero releases without a 500."""
    resp = client.direct_search("Mistborn", inject="empty")
    assert resp.status_code in (200, 404), resp.status_code
    assert client.releases_from(resp) == []

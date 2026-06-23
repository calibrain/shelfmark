"""Cluster 7 (audiobook/ABB) parse-contract guards.

ABB forces ``https://`` for search and detail fetches, so it can't be exercised
hermetically in the HTTP e2e docker platform. Its recurring bugs are instead in
*parsing*: magnet/info-hash extraction ("Fix ABB magnet parsing", and the
qbittorrent hash-length issue #386) and DOM/layout drift. These contract tests
feed golden HTML through the real scraper — the same fail-on-drift philosophy as
the AA layout-drift guard — and run in normal CI.

They deliberately cover cases the existing ``test_scraper.py`` does not: info-hash
*normalization* (whitespace/case), the in-page magnet *fallback*, and a layout
drift that must degrade to an empty result rather than crash.
"""

from __future__ import annotations

import re
from unittest.mock import patch

from shelfmark.release_sources.audiobookbay import scraper

# Detail page where the Info Hash is lowercase and split by whitespace/newlines —
# the exact shape that produced malformed magnets / wrong hash lengths (#386).
DETAIL_HTML_MESSY_HASH = """
<html><body><table>
  <tr><td>Info Hash</td><td>abc123def456789012345678
  901234567890abcd</td></tr>
  <tr><td>Tracker 1</td><td>udp://tracker.openbittorrent.com:80</td></tr>
</table></body></html>
"""

# Info Hash cell is junk, but a full magnet link is posted elsewhere on the page.
DETAIL_HTML_MAGNET_FALLBACK = """
<html><body>
  <table><tr><td>Info Hash</td><td>n/a</td></tr></table>
  <p>Mirror: magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=x</p>
</body></html>
"""

# DOM drift: results are present but the .post / .postTitle structure changed.
SEARCH_HTML_LAYOUT_DRIFT = """
<html><body>
  <article class="result-card">
    <header><a href="/abss/drifted/">Drifted Audiobook - Author</a></header>
    <span class="lang">English</span>
  </article>
</body></html>
"""


def _patch_detail(html: str):
    return patch(
        "shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page",
        return_value=html,
    )


def test_info_hash_is_normalized_to_canonical_btih() -> None:
    """Whitespace/newlines are stripped and the hash upper-cased to a valid
    40-char btih (regression for #386 / 'Fix ABB magnet parsing')."""
    with _patch_detail(DETAIL_HTML_MESSY_HASH):
        magnet = scraper.extract_magnet_link("https://audiobookbay.lu/abss/x/", "audiobookbay.lu")
    assert magnet is not None, "messy-but-valid info hash should still yield a magnet"
    btih = re.search(r"xt=urn:btih:([0-9A-Fa-f]+)", magnet)
    assert btih is not None, magnet
    assert btih.group(1) == "ABC123DEF456789012345678901234567890ABCD"
    assert len(btih.group(1)) == 40
    assert "tr=" in magnet  # tracker carried through


def test_magnet_fallback_when_info_hash_cell_is_junk() -> None:
    """When the Info Hash cell is invalid, the scraper recovers the hash from an
    in-page magnet link rather than failing."""
    with _patch_detail(DETAIL_HTML_MAGNET_FALLBACK):
        magnet = scraper.extract_magnet_link("https://audiobookbay.lu/abss/y/", "audiobookbay.lu")
    assert magnet is not None
    assert "btih:1111111111111111111111111111111111111111" in magnet


def test_missing_info_hash_returns_none_not_crash() -> None:
    """No hash anywhere -> None (clean failure), never an exception."""
    with _patch_detail("<html><body><p>nothing here</p></body></html>"):
        assert (
            scraper.extract_magnet_link("https://audiobookbay.lu/abss/z/", "audiobookbay.lu")
            is None
        )


def test_search_layout_drift_degrades_to_empty() -> None:
    """A changed results DOM yields zero parsed results without raising — the
    ABB analogue of the AA layout-drift guard."""
    with (
        patch(
            "shelfmark.release_sources.audiobookbay.scraper.downloader.html_get_page",
            return_value=(SEARCH_HTML_LAYOUT_DRIFT, "https://audiobookbay.lu/?s=test"),
        ),
        patch(
            "shelfmark.release_sources.audiobookbay.scraper.config.get",
            return_value=0.0,
        ),
    ):
        results = scraper.search_audiobookbay("test", max_pages=1, hostname="audiobookbay.lu")
    assert results == [], f"drifted DOM should parse to no results, got {results!r}"

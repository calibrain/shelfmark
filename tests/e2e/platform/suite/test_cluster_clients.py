"""Cluster 5 — real download clients (torrent), end to end.

The `full` and `client-*` profiles each point shelfmark at a *real* torrent client
(qBittorrent / Transmission / Deluge / rTorrent) plus a mock Prowlarr that returns a
tracker-less BEP-19 webseed `.torrent` sourced from mock-aa. The client downloads
the payload over HTTP (no tracker/peer/seeder) and shelfmark's completion detection
+ post-process move lands Moby-Dick in `/books`.

The test is **client-agnostic** — the active profile's env selects the client
(`PROWLARR_TORRENT_CLIENT` + that client's URL/creds) — so one test covers the whole
client matrix.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.profiles("full", "client-transmission", "client-deluge")

BOOK = "Moby Dick"


def _books_dir() -> Path | None:
    raw = os.environ.get("E2E_BOOKS_DIR")
    return Path(raw) if raw else None


def _book_files(books: Path) -> set[str]:
    return {
        p.name for p in books.rglob("*") if p.is_file() and p.suffix.lower() in {".epub", ".pdf"}
    }


def _prowlarr_search(client, query: str):
    return client.get(
        "/api/releases",
        params={
            "provider": "manual",
            "book_id": "e2e-manual-1",
            "source": "prowlarr",
            "title": query,
            "manual_query": query,
        },
        timeout=60,
    )


def test_prowlarr_to_real_torrent_client_download(client, active_profile) -> None:
    """Prowlarr release -> real torrent client (per profile) -> file in /books."""
    books = _books_dir()
    if books is None or not books.exists():
        pytest.skip("E2E_BOOKS_DIR not visible to the test runner")
    before = _book_files(books)

    resp = _prowlarr_search(client, BOOK)
    assert resp.status_code == 200, f"[{active_profile}] prowlarr search failed: {resp.text[:300]}"
    releases = client.releases_from(resp)
    assert releases, f"[{active_profile}] mock prowlarr returned no releases"

    queued = client.queue_download(releases[0])
    assert queued.status_code in (200, 201, 202), (
        f"[{active_profile}] queue refused the release: {queued.status_code} {queued.text[:300]}"
    )
    # The prowlarr source serializes download_url=None and resolves the real URL
    # from its cache by source_id at download time.
    book_id = releases[0].get("source_id") or releases[0].get("id") or releases[0].get("guid")
    assert book_id, f"[{active_profile}] release missing a trackable id: {releases[0]!r}"

    state, info = client.wait_for_terminal(str(book_id))
    assert state in {"complete", "done", "available"}, (
        f"[{active_profile}] real torrent-client download did not complete: "
        f"state={state} info={info!r}"
    )

    deadline = time.time() + 30
    new_files: set[str] = set()
    while time.time() < deadline:
        new_files = _book_files(books) - before
        if new_files:
            break
        time.sleep(2)
    assert new_files, f"[{active_profile}] client completed but no file landed in /books"
    assert all(Path(n).suffix for n in new_files), f"file without extension: {new_files}"

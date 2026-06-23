"""Cluster 4 — download execution + file placement/permissions.

Two halves, by what each profile can hermetically prove:

* **baseline (no bypasser, AA-only):** AA's slow-download sources require a
  Cloudflare bypass (``_CF_BYPASS_REQUIRED``), so a download here *cannot* succeed
  and the app must say so cleanly — this is exactly the real-world #1028 shape
  ("All download sources failed"). We assert that the failure is surfaced as a
  terminal ``error`` with a message, not a hang/crash, and that nothing is left
  orphaned in staging (#1040).
* **successful download + file move** (extension preserved #214, no orphaned
  staging dir #1040) is proven for real in the ``full`` profile, where a real
  qBittorrent completes a webseed torrent — see ``test_cluster_full_pipeline.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.profiles("baseline")


def _staging_leftovers() -> list[Path]:
    tmp_raw = os.environ.get("E2E_TMP_DIR")
    if not tmp_raw or not Path(tmp_raw).exists():
        return []
    tmp = Path(tmp_raw)
    return [p for p in tmp.rglob("*") if p.is_file() and p.suffix.lower() in {".epub", ".pdf"}]


def test_no_bypass_download_fails_cleanly(client) -> None:
    """Without a bypasser, AA is undownloadable — the app must report a clear
    terminal error (the #1028 shape), not hang or crash."""
    resp = client.direct_search("Mistborn")
    releases = client.releases_from(resp)
    assert releases, "search should still return parsed releases even if undownloadable"

    queued = client.queue_download(releases[0])
    assert queued.status_code in (200, 201, 202), (
        f"queue refused the release: {queued.status_code} {queued.text[:300]}"
    )
    book_id = releases[0].get("id") or releases[0].get("source_id") or releases[0].get("md5")
    assert book_id, f"release missing an id to track: {releases[0]!r}"

    state, info = client.wait_for_terminal(str(book_id))
    assert state == "error", (
        f"expected a clean terminal error without a bypasser, got state={state} info={info!r}"
    )
    message = str(info.get("status_message") or info.get("last_error_message") or "")
    assert message.strip(), "download failed but surfaced no status message to the user"


def test_no_orphaned_staging_dir(client) -> None:
    """#1040 guard: a failed/aborted download must not leave book payloads behind
    in the staging/tmp area."""
    if not os.environ.get("E2E_TMP_DIR"):
        pytest.skip("E2E_TMP_DIR not provided")
    leftovers = _staging_leftovers()
    assert not leftovers, f"book payload left orphaned in staging dir: {leftovers}"

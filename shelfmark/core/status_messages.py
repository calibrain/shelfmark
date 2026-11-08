"""Plain wording for a download that failed or stopped: what happened and what comes next.

The saved message is a log line ("Peer transfer failed: Errored"). Auto-download reads it to
tell a failed release from an interrupted one, so it is never rewritten; only what the activity
list shows is.
"""

from __future__ import annotations

from typing import Any

from shelfmark.core.config import config as app_config
from shelfmark.core.request_helpers import coerce_int

FAILURE_RETRY_MINUTES_SETTING = "AUTO_DOWNLOAD_FAILURE_RETRY_MINUTES"

_SHOWN_FOR = frozenset({"error", "cancelled"})


def _next_step(*, request_linked: bool, retry_minutes: int) -> str:
    if request_linked and retry_minutes > 0:
        return f" Another copy will be tried in about {retry_minutes} minutes."
    if request_linked:
        return " Use Retry to try again, or reopen the request to try another copy."
    return " Use Retry to try again, or pick another copy."


def explain(
    message: str | None,
    *,
    request_linked: bool,
    retry_minutes: int | None = None,
) -> str | None:
    """The message in plain words with the next step, or it unchanged when nothing is known."""
    if not isinstance(message, str) or not message.strip():
        return message
    text = message.strip()
    lowered = text.lower()
    minutes = (
        retry_minutes
        if retry_minutes is not None
        else coerce_int(app_config.get(FAILURE_RETRY_MINUTES_SETTING, 0), 0)
    )
    nxt = _next_step(request_linked=request_linked, retry_minutes=minutes)

    if lowered.startswith("superseded"):
        return "This book was already delivered by another download."
    if lowered.startswith("interrupted"):
        return "Interrupted by a restart. Use Retry to run it again."
    if lowered.startswith("peer transfer failed"):
        detail = text.split(":", 1)[1].strip() if ":" in text else ""
        suffix = f" ({detail})" if detail else ""
        return f"The Soulseek peer dropped the transfer{suffix}." + nxt
    if lowered.startswith("failed to add to slskd"):
        if "direct or indirect" in lowered:
            return (
                "Could not reach the Soulseek peer, directly or through the Soulseek server "
                "(tried twice)." + nxt
            )
        if '("' in text and "timed out" not in lowered:
            # slskd gave a reason, and it is not a timeout.
            return "slskd would not queue the download (tried twice)." + nxt
        # A timeout, or the bare 500 an older attempt saved before slskd's reason was kept; every
        # one of those that slskd's own log explained was a peer that did not answer in time.
        return "The Soulseek peer did not answer slskd's queue request (tried twice)." + nxt
    if lowered.startswith("download failed:"):
        detail = text.split(":", 1)[1].strip()
        return f"The download hit an internal error ({detail})." + nxt
    if lowered.startswith("retrying after"):
        return "Retrying automatically after a failed download."
    if lowered.startswith("download stalled"):
        return "Nothing was downloading, so it was stopped." + nxt
    if lowered.startswith("no seeders"):
        return text.rstrip(".") + "; the torrent was removed." + nxt
    if lowered.startswith("all download sources failed"):
        return "None of the download sources could provide this book." + nxt
    return text


def explain_request_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The request rows with ``last_failure_reason`` in plain words (copies; the stored text stays)."""
    minutes = coerce_int(app_config.get(FAILURE_RETRY_MINUTES_SETTING, 0), 0)
    explained: list[dict[str, Any]] = []
    for row in rows:
        reason = row.get("last_failure_reason")
        if not isinstance(reason, str) or not reason.strip():
            explained.append(row)
            continue
        # A request that was reopened after a restart is searched again by the next pass.
        text = (
            "Interrupted by a restart; it will be tried again."
            if reason.strip().lower().startswith("interrupted by a restart")
            else explain(reason, request_linked=True, retry_minutes=minutes)
        )
        explained.append({**row, "last_failure_reason": text})
    return explained


def explain_buckets(status: dict[str, dict[str, Any]]) -> None:
    """Rewrite the messages of the error and cancelled entries of a status dict, in place."""
    minutes = coerce_int(app_config.get(FAILURE_RETRY_MINUTES_SETTING, 0), 0)
    for bucket in _SHOWN_FOR:
        entries = status.get(bucket, {})
        for task_id, payload in list(entries.items()):
            if not isinstance(payload, dict):
                continue
            # A copy, so a payload the queue hands out again is never explained twice.
            entries[task_id] = {
                **payload,
                "status_message": explain(
                    payload.get("status_message"),
                    request_linked=bool(payload.get("request_id")),
                    retry_minutes=minutes,
                ),
            }

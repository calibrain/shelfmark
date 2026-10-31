"""The info hashes of the torrents this instance added, and which task added them.

The stale-torrent sweep may only remove a torrent Shelfmark itself put in the client: a category
says nothing about who else shares it. Every torrent an add returns a hash for is recorded here,
with the task that added it, and the sweep touches nothing that is not in the ledger. A torrent
added before the ledger existed is never in it, so it is never swept.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from typing import TYPE_CHECKING

from shelfmark.config.env import CONFIG_DIR
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from pathlib import Path

logger = setup_logger(__name__)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS torrent_ledger ("
    "hash TEXT PRIMARY KEY, task_id TEXT NOT NULL, added_at REAL NOT NULL)"
)


def _path() -> Path:
    return CONFIG_DIR / "torrent_ledger.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_path())
    conn.execute(_SCHEMA)
    return conn


def record(torrent_hash: str, task_id: str) -> None:
    """Note that ``task_id`` added the torrent. A ledger failure never fails the download."""
    if not torrent_hash:
        return
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "INSERT INTO torrent_ledger (hash, task_id, added_at) VALUES (?, ?, ?) "
                "ON CONFLICT(hash) DO UPDATE SET task_id = excluded.task_id",
                (torrent_hash.lower(), task_id, time.time()),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Could not record torrent %s in the ledger: %s", torrent_hash, exc)


def claim(torrent_hash: str, task_id: str) -> None:
    """A task joined a torrent that is already in the ledger: it owns it now.

    A torrent the ledger does not hold stays out of it, so joining something Shelfmark did
    not add never makes it sweepable.
    """
    if not torrent_hash:
        return
    try:
        with closing(_connect()) as conn, conn:
            conn.execute(
                "UPDATE torrent_ledger SET task_id = ? WHERE hash = ?",
                (task_id, torrent_hash.lower()),
            )
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Could not update torrent %s in the ledger: %s", torrent_hash, exc)


def entries() -> dict[str, str]:
    """Every recorded hash with the id of the task that last owned it."""
    try:
        with closing(_connect()) as conn:
            return {
                str(row[0]): str(row[1])
                for row in conn.execute("SELECT hash, task_id FROM torrent_ledger")
            }
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Could not read the torrent ledger: %s", exc)
        return {}


def forget(*torrent_hashes: str) -> None:
    """Drop rows, once their torrent has been removed or is gone from the client."""
    hashes = [(h.lower(),) for h in torrent_hashes if h]
    if not hashes:
        return
    try:
        with closing(_connect()) as conn, conn:
            conn.executemany("DELETE FROM torrent_ledger WHERE hash = ?", hashes)
    except (sqlite3.Error, OSError) as exc:
        logger.warning("Could not update the torrent ledger: %s", exc)

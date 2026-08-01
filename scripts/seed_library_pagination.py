# ruff: noqa: E402, I001
"""Reset a disposable local Shelfmark database with 100 pagination demo books.

Run from the repository root with:
``CONFIG_DIR=$PWD/.local/config uv run python scripts/seed_library_pagination.py``.
This deletes the selected ``users.db`` and its sibling ``seed-files`` directory.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))
os.environ.setdefault("LOG_ROOT", str(REPOSITORY_ROOT / ".local/log"))

from shelfmark.core.user_db import UserDB


CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", ".local/config"))
DB_PATH = CONFIG_DIR / "users.db"
FILES_DIR = CONFIG_DIR.parent / "seed-files"
DEMO_USERNAME = "demo"
DEMO_PASSWORD = "demo"
BOOK_COUNT = 100


def main() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    shutil.rmtree(FILES_DIR, ignore_errors=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)

    user_db = UserDB(str(DB_PATH))
    user_db.initialize()
    demo_user = user_db.create_user(
        username=DEMO_USERNAME,
        password_hash=generate_password_hash(DEMO_PASSWORD),
        role="admin",
    )
    demo_user_id = int(demo_user["id"])
    start = datetime(2026, 1, 1, tzinfo=UTC)

    conn = sqlite3.connect(DB_PATH)
    try:
        for number in range(1, BOOK_COUNT + 1):
            title = f"Pagination Demo Book {number:03}"
            cursor = conn.execute(
                """
                INSERT INTO books (metadata_provider, provider_book_id, title, author)
                VALUES (?, ?, ?, ?)
                """,
                ("pagination-demo", f"book-{number:03}", title, f"Demo Author {number % 10}"),
            )
            book_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO user_library (user_id, book_id, added_at) VALUES (?, ?, ?)",
                (demo_user_id, book_id, (start + timedelta(minutes=number)).isoformat()),
            )
            if number % 4 == 0:
                file_path = FILES_DIR / f"book-{number:03}.epub"
                file_path.write_text(f"Placeholder for {title}\n")
                cursor = conn.execute(
                    """
                    INSERT INTO download_history (
                        task_id, user_id, source, title, format, final_status, download_path, book_id
                    ) VALUES (?, ?, 'demo', ?, 'epub', 'complete', ?, ?)
                    """,
                    (f"pagination-demo-{number:03}", demo_user_id, title, str(file_path), book_id),
                )
                conn.execute(
                    "INSERT INTO user_downloads (user_id, history_id) VALUES (?, ?)",
                    (demo_user_id, cursor.lastrowid),
                )
        conn.commit()
    finally:
        conn.close()

    print(
        f"Seeded {BOOK_COUNT} books in {DB_PATH} for {DEMO_USERNAME}/{DEMO_PASSWORD} "
        "(25 have EPUB files)."
    )


if __name__ == "__main__":
    main()

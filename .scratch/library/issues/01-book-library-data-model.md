Type: grilling
Status: resolved
Blocked by:

# Model the book / library / file relationship

## Question

Pin down the canonical data model that the rest of the map depends on.

The architecture survey (`shelfmark/core/user_db.py:27-116`) confirms there is no `books`, `authors`, or `sources` table today. `download_history` rows (keyed by `task_id`, the source's release ID) are the closest thing to a book entity but are per-download.

The grilling session settled the following invariants; this ticket turns them into a concrete schema decision:

1. Books are keyed on **(metadata_provider, provider_book_id)**. Uniqueness is per-pair — same work in two providers lives as two book rows.
2. The library is **per-user**: a `user_library` link table from `users.id` to `books.id`. Library entries can exist with zero files on disk (wishlist semantics) — files on disk (`download_history`) are orthogonal.
3. `download_history`.

Designs can legitimately diverge (e.g. whether `books` stores denormalized author/title or fetches from the metadata provider on demand; whether `authors` is its own table or just a column on `books`; how 1-book→N-downloads is reflected — FK on `download_history` to `books.id`, or a join table for future flexibility). Decide and defend each. The output is a schema sketch — table definitions, key FKs, indexes (especially `(metadata_provider, provider_book_id)` and `(user_id, book_id)` on the link table) — written into the resolution, with the migration approach per `shelfmark/core/user_db.py:196-271` style (conditional `ALTER TABLE` awaiting review).

## Outcome of this ticket

A documented schema decision: every later ticket that touches the data model assumes this is settled. Implementing the migration is a separate ticket (#03); this one decides the shape.

## Answer

Resolved 2026-07-20 by grilling session. Each sub-decision is recorded with its rationale; the full schema sketch follows. An ADR captures the cross-cutting choice (`docs/adr/0001-books-as-denormalized-snapshot.md`).

### Sub-decisions

1. **Book row is a denormalized snapshot (copy-on-add), not a lazy fetch.** On add, the provider's `get_book` result is written into `books` columns; book detail / bookshelf never call the provider. Matches `download_history`'s snapshot precedent (`user_db.py:70-91`), decouples the library UX from provider availability/rate-limits, makes #12's Hardcover-bibliography cross-ref tractable. Raw payload is preserved in `metadata_json`. Staleness accepted; refresh is a deferred explicit action.

2. **Authors: column on `books`, no `authors` table.** `books.author TEXT` holds the *primary* author only; the full ordered author list lives in `metadata_json.authors`. A separate `authors` table is parked in fog for #12 to revisit — the table only earns its keep if #12 demands an instance-local, provider-agnostic author route. Today #12 is Hardcover-only (`hardcover.py:399-437`) and uses the provider's own `author_id`; no instance-local author identity is needed.

3. **Natural key: `UNIQUE(metadata_provider, provider_book_id)`, both `NOT NULL`.** Matches `task_id UNIQUE NOT NULL` precedent (`user_db.py:72`). SQLite UNIQUE allows multiple NULLs, so a nullable `provider_book_id` would silently permit duplicates — that's why the NOT NULL is deliberate. Direct-mode downloads have no `books` row at all (universal-mode-only feature, per map fog section), so NOT NULL doesn't break direct mode. The external route shape (`/library/42` vs slug) is #07's call, not #01's.

4. **`user_library(user_id, book_id, added_at)`, PK `(user_id, book_id)`, both FKs `ON DELETE CASCADE`, hard delete.** Matches the grilling session's "removal deletes the row; files stay" invariant. Cascade on both FKs because the link *is* the user's library (vs `download_history.user_id` which uses `SET NULL` to preserve history). No speculative `notes`/`tags` columns — mint when a ticket needs them. No `removed_at` — a future "restore removed" UX is a fresh ticket that adds soft-delete then.

5. **`download_history` link: nullable `book_id INTEGER REFERENCES books(id) ON DELETE SET NULL`.** Sets NULL on book delete to preserve history (matches existing `user_id` FK action). Backfill strategy (best-effort from `download_requests.book_data`, or lazy-link on next access) is #03's call — #01 decides only the shape. NULL `book_id` on old rows is acceptable; the library is universal-mode-going-forward. Rejected alternatives: a `book_files` join table (same JOIN, more moving parts, contradicts "no new tables without a ticket weighing them"); no link (a Book row has no `(source, task_id)` to match against, so "files for this book" *can't* be computed at read-time).

6. **Indexes: implicit unique/PK + one new `idx_download_history_book_id` on `download_history(book_id, final_status)`.** The new index covers the hot reverse-lookup path (#02's "files on disk globally for this book", #09's dedup). Existing user-scoped indexes (`idx_download_history_user_status`, `idx_download_history_recent`) don't cover the cross-user "files for this book" query. No speculative index on `books.author` or `user_library.book_id` — let #04/#06 settle the query contracts first.

7. **`metadata_json` policy: promote canonical queryable/render fields to columns, store the *complete raw* `BookMetadata` JSON payload in `metadata_json`.** Promoted: `subtitle`, `publish_year`, `isbn_13`, `cover_url`, `series_name`, `series_position`, `language`. Keeping the raw payload intact means future column promotions are pure schema migrations with no provider re-fetch — important since the provider may be down or rate-limited at migration time. `metadata_json.authors` preserves the full ordered author list.

8. **`books.updated_at` bumps on metadata refresh only.** Add-to-library sets `created_at = updated_at = now`. Cover re-fetches (if they ever exist) write to `cover_url` without touching `updated_at`, keeping the metadata-freshness signal clean for a future "auto-refresh stale metadata" ticket. Bumping on any write (B) entangles cover and metadata freshness; two timestamps (C) is speculative — no ticket asks "when was cover last refreshed?".

### Schema sketch (final)

```sql
CREATE TABLE IF NOT EXISTS books (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    metadata_provider  TEXT    NOT NULL,
    provider_book_id   TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    author             TEXT,                       -- primary author only; full list in metadata_json.authors
    subtitle           TEXT,
    publish_year       INTEGER,
    isbn_13            TEXT,
    cover_url          TEXT,
    series_name        TEXT,
    series_position    REAL,
    language           TEXT,
    metadata_json      TEXT    NOT NULL DEFAULT '{}',  -- complete raw BookMetadata payload
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (metadata_provider, provider_book_id)
);

CREATE TABLE IF NOT EXISTS user_library (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id    INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    added_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, book_id)
);

-- on the existing download_history table:
ALTER TABLE download_history ADD COLUMN book_id INTEGER REFERENCES books(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_download_history_book_id
    ON download_history (book_id, final_status);
```

### Key invariants (downstream tickets assume these)

- A `books` row is self-sufficient for rendering — never re-fetch from a provider on the read path.
- Library membership is orthogonal to files: a Book can be in a user's library with zero files anywhere.
- Files (`download_history`) are global; the library merely surfaces them. Adding to library never creates a file; downloading (going forward) writes the `book_id` FK but doesn't create a library entry.
- A `download_history` row may have `book_id IS NULL` (legacy / direct-mode / un-lined); the library queries filter `book_id IS NOT NULL`.
- Uniqueness is per `(metadata_provider, provider_book_id)` — same work in two providers lives as two book rows (cross-provider merge is out of scope).
- `metadata_json.authors` is the ordered full author list; `books.author` is the primary for rendering.
- `updated_at > created_at` iff metadata has been explicitly refreshed.

### What this ticket does NOT decide (deferred to other tickets)

- The actual migration code + backfill strategy → #03.
- External route shape (`/library/42` vs slug) → #07.
- "Files on disk globally" exact predicate + in-flight handling → #02.
- Admin/scope rules, pagination, refresh endpoint → #04.
- Send-to-Kindle per-user recipient storage → #05.
- Authors table (if #12 needs one) → fresh ticket when #12 lands.

### Context assets

- `docs/adr/0001-books-as-denormalized-snapshot.md` — cross-cutting snapshot decision.
- `CONTEXT.md` — glossary terms for Book / Library / File, sharpened during this session.

Type: task
Status: resolved
Blocked by: 03, 04
Assignee: glm-5.2-fast (claimed 2026-07-20; resolved 2026-07-20)

# Implement the library API per contract

## Question

Manual execution ticket: write the Flask routes/blueprint per the API contract from #04, against the schema from #03. The architectural survey establishes the precedent of `shelfmark/core/activity_routes.py` as a blueprint-style module — follow that pattern.

Implementation scope:

- Library blueprint at `shelfmark/core/library_routes.py` (or co-locate in `activity_routes.py` if that reads cleaner — #04 decides) wired in `shelfmark/main.py`.
- A `LibraryService` (likely in `shelfmark/core/library_service.py`) for the SQL queries against `books`, `user_library`, and `download_history`. Mirror the layering of `DownloadHistoryService` at `shelfmark/core/download_history_service.py`.
- Reuse `UserDB.get_user_settings` / `set_user_settings` (`shelfmark/core/user_db.py:438-475`) for per-user Kindle address storage once #05 settles the field.
- Send-to-Kindle endpoint must call into the existing SMTP pipeline (`shelfmark/download/outputs/email.py`) directly with the resolved file path — do not re-route through the download queue.
- Per-format download endpoint adapts `GET /api/localdownload` (`shelfmark/main.py:1580-1615`); ownership rules from `_download_row_owned_by_actor` (`shelfmark/main.py:1467-1481`) must apply.

Knock-on changes:

- The `/api/localdownload` endpoint currently serves from `task.download_path` or `download_history.download_path` (fall-back). For library per-format downloads, the input is `(book_id, format)` not `task_id` — wire up a lookup that resolves to a `download_history` row with that book+format, then reuse the existing path-serve code.
- `login_required` enforcement (`shelfmark/main.py:802-838`) carries over.
- The add-to-library endpoint must invoke the existing metadata provider lookup (the same one that powers `DetailsModal`'s fetch from `/api/metadata/book/<provider>/<book_id>` at `shelfmark/main.py:2671`) to cache book metadata into `books` at add-time. Decide (with #04) whether the metadata fetch is synchronous or fires-and-caches-on-first-read.

Verification: `make python-checks` passes; endpoints return 401 without a session, 403 when scoping rules (from #04) are violated, correct payloads on the happy path.

## Outcome of this ticket

Code on the feature branch. No UI yet — that's #07/#08.

## Answer

Resolved 2026-07-20 by task execution on branch `feature/library-api-implementation`. Implements the full #04 route table against the #03 schema, with all eight endpoints live and the data layer isolated in `LibraryService`.

### Files touched

- **`shelfmark/core/library_service.py`** (new) — `LibraryService` mirrors `DownloadHistoryService`'s layering: `db_path` + `threading.Lock` + `_connect()` + row→dict helpers. Methods: `upsert_book_from_metadata` (idempotent on `UNIQUE(metadata_provider, provider_book_id)`), `add_to_library` / `remove_from_library` (hard `INSERT OR IGNORE` / `DELETE`), `is_in_library`, `get_book`, `list_library_books` (admin sees all; non-admin scoped; `?q=` fuzzy `LIKE` on title/author per #04 sub-decision 10), `get_files_on_disk` + `get_in_flight_files` (global union across users), `files_exist_globally` / `in_flight_globally` (two distinct flags per sub-decision 12), `link_download_to_user` / `unlink_download_from_user` (idempotent link / hard unlink per #04 sub-decision 3), `download_linked_to_user`, `resolve_kindle_format` (#05 priority algorithm), `get_download_history_row`.
- **`shelfmark/core/library_routes.py`** (new) — `register_library_routes(app, user_db, *, library_service, download_history_service, resolve_auth_mode, resolve_metadata_book)` follows the project's `register_*_routes` convention; explicitly NOT a Flask Blueprint (matches #04 sub-decision 1). All 8 routes from #04's route table implemented: `POST /api/library/books` (503 on provider failure per sub-decision 13), `GET /api/library/books` (?q= filter), `GET /api/library/books/:id` (full metadata + files list + in-flight list with `downloadable_by_me` per sub-decision 6), `DELETE /api/library/books/:id`, `GET /api/library/books/:id/download?format=` (gates on `user_library` membership, not `download_history.user_id` — closes the existing cross-user byte-exposure leak per sub-decision 7), `POST /api/library/books/:id/send-to-kindle` (fail-fast ordering per sub-decision 16: 403 non-member → 404 no compatible file → 400 no per-user recipient → 500 SMTP), `POST /api/library/books/:id/downloads/:history_id` (idempotent link with book-id mismatch → 404), `DELETE /api/library/books/:id/downloads/:history_id` (idempotent unlink). Send-to-Kindle additionally requires `user_downloads` ownership — file serving gates on library membership, but Send-to-Kindle is stricter: only files the user has linked are sendable. Actor resolution mirrors `activity_routes._resolve_activity_actor` — auth-mode "none" is admin-equivalent, admin `owner_scope=None` for instance-wide read.
- **`shelfmark/main.py`** — imports `LibraryService`/`register_library_routes`; instantiates `library_service` alongside `download_history_service`; adds `_resolve_metadata_book_for_library` helper that fetches `BookMetadata` via the provider, runs the same `transform_cover_url` cache-id pattern as `/api/metadata/book` (`main.py:2671`), synthesizes `books.author` from `BookMetadata.authors[0]`, and stashes the full raw payload in `metadata_json`. Wired into the `if user_db is not None` route-registration block (next to `register_activity_routes` / `register_request_routes`, gated on a non-`None` `library_service` + `download_history_service` so the route module only loads when multi-user infra is available).
- **`shelfmark/download/outputs/email.py`** — added `send_file_to_email(file_path, recipient, *, label=None, subject=None) -> str` (sub-decision 17) and `_mask_recipient(recipient) -> str` (MVP masker mirroring `output_args["label"]`). Reuses `build_email_smtp_config` + `_get_email_settings()` + `send_email_message`; composes an `EmailMessage` inline (no synthetic `DownloadTask`). Raises `EmailOutputError` on config/SMTP failure — library routes translate into 500 via `_OPERATIONAL_ERRORS`. Returns the masked recipient for the API success response.
- **`shelfmark/core/user_db.py`** and **`shelfmark/core/self_user_routes.py`** — provide the dedicated per-user `kindle_address` recipient preference, distinct from `EMAIL_RECIPIENT` (the download-output email) and with no fallback.
- **`tests/core/test_library_service.py`** (new, 11 tests) — service-tier coverage: upsert idempotency on natural key, distinct-key inserts, add-to-library idempotency, hard-delete membership, admin-sees-all vs scoped-own list, `?q=` LIKE on title and author, files-on-disk globability (linked rows with `final_status='complete'` + `download_path`), in-flight detection (active rows don't count as files), link/unlink idempotency with `download_history` row untouched, kindle format priority resolves epub-over-mobi, kindle format returns None when no files.
- **`tests/core/test_library_routes.py`** (new, 19 tests) — routes-tier coverage: 401 on every route without session, add returns idempotent payload + caches metadata, add 503 when provider stub returns `None`, add 400 on missing payload fields, list scoped to own library, list admin sees all, detail 403 for non-member, detail returns full metadata for member, detail 404 for missing book id, delete scoped to own library, download gates on library membership (non-member 403 even when they own the row), download 404 when no matching format, send-to-kindle fail-fast for no files (404), send-to-kindle 400 when the personal recipient is unset, send-to-kindle happy path with masked recipient + patched SMTP, link endpoint inserts `user_downloads` row, link 404 when `book_id` mismatch, unlink idempotent, 401 on all five routes.
- **`tests/download/test_send_file_to_email.py`** (new, 4 tests) — `send_file_to_email` + `_mask_recipient` coverage.

### Settlements of #04's open questions made during implementation

- **Send-to-Kindle ownership gate**: library membership is sufficient for file serving (sub-decision 7) but Send-to-Kindle additionally requires `user_downloads` ownership — the row must be linked to the actor. Without this, a non-member who happens to know a `history_id` tied to a book they don't have in their library could not download (blocked by the membership gate), but the Send-to-Kindle path is stricter because it performs an external side-effect (SMTP send). Admin bypasses the `user_downloads` check (instance-wide read + support): admin can send any file from any book in any user's library, mirroring the file-serving behavior for admins.
- **`metadata_json` payload shape**: `_resolve_metadata_book_for_library` stores the raw `BookMetadata` payload (minus the synthesized `author` field) in `books.metadata_json` — preserves every provider-specific field for future book-detail rendering. `BookMetadata.authors` (list) → `books.author` (TEXT, primary only) normalization lives at Add time, matching #01 / #03's precedent.
- **Cover URL**: mirrored `/api/metadata/book/<provider>/<book_id>` exactly — `transform_cover_url` runs with `cache_id = f"{provider}_{provider_book_id}"`. The book-detail response surface `cover_url` can be either a `/api/covers/...` cached URL or the raw provider URL (when caching is off).

### Knock-on effects on other tickets

- **#08** (book detail page UI) — the per-file list payload is `files: [{history_id, format, size, indexer_display_name, protocol, downloaded_at, downloadable_by_me}]`. `downloadable_by_me` is `True` when the file is linked to the actor via `user_downloads` (or admin). The book-detail aware unlink mid-flight UX decision was parked on #08; this ticket leaves the backend permissive (unlinking an active row is allowed — the queue keeps running, `download_path` populates later, the user can re-link via `INSERT OR IGNORE`).
- **#09** (release-list dedup indicator) — `GET /api/library/books/:book_id` surfaces `in_flight` list with `history_id`, `format`, `source_display_name` — enough to render the cross-user in-flight indicator on the release-list page.
- **#10** (bookshelf UI) — the list-books response shape is `{books: [{book_id, title, author, cover_url, formats_on_disk: [{format, size}], added_at}]}`. `formats_on_disk` is the per-Book global union (#04 sub-decision 3) — no per-user attribution.
- **#11** (search integration) — the Add endpoint is synchronous + idempotent (#04 sub-decision 13). The frontend invokes `POST /api/library/books` at click time, shows loading during the metadata fetch, then navigates to `/library/:bookId` on success. The 503 path lets the frontend show a retry affordance.
- **#12** (author browse MVP) — the data model (#03) + this API surface keep `books.author TEXT` non-normalized; `/authors/:authorId` as an instance-local route still requires the `authors` table that #01 ruled out — remains fog until #12 is worked.

### Verification

- `make python-checks`: ruff, ruff-format, basedpyright (shelfmark + tests), vulture — all clean (0 errors, 0 warnings, 0 notes).
- `make python-test`: 1955 tests pass (34 new — 11 service + 19 routes + 4 email; the rest are the existing suite, including the `test_admin_users_api` delivery-preferences assertion update).

### Downstream tickets now unblocked

- **#08** (book detail page UI), **#09** (release-list dedup indicator), **#10** (bookshelf UI), **#11** (search integration impl), **#12** (author browse MVP) — the API surface they code against is live.

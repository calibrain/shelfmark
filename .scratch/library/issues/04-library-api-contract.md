Type: grilling
Status: resolved
Blocked by: 01, 02
Assignee: glm-5.2-fast (claimed 2026-07-20; resolved 2026-07-20)

# Design the library backend API surface

## Question

Pin down the Flask API surface for the library. No code yet — this ticket decides the routes, request/response shapes, scoping rules. Every later UI ticket codes against this.

Required endpoints (working set; refine during ticket):

- **Add to library** — `POST /api/library/books` with `{metadata_provider, provider_book_id}` (or whatever key the data model in #01 settled on). Idempotent: returns the existing book row if already in the user's library. Must invoke the metadata provider lookup to mint/cache book metadata. Returns `{book_id, files_exist_globally: bool, in_flight_globally: bool}` so the frontend can branch per the flow contract (#02).
- **List my library** — `GET /api/library/books` with filters (`?author=`, `?q=`, `?has_files=true|false`, `?page=`, `?page_size=`). Returns paginated book rows plus per-book `formats_on_disk: [epub, mobi, azw3, ...]` (globally, not per-user — files are global), `in_my_library: bool` (always true for this endpoint but shaped for reuse on author browse #10), `cover_url`, `cover_thumbnail_url`.
- **Book detail** — `GET /api/library/books/:book_id` returning full metadata (denormalized), `formats_on_disk` (with `download_url` per format — see #08), `in_my_library`, `send_to_kindle_compatible_formats`, and any "in-flight downloads" attached to the book across users (for the dedup indicator in #09).
- **Remove from library** — `DELETE /api/library/books/:book_id`. Removes the `user_library` row. Does not touch `books` row or files (per #01 / fog-out-of-scope).
- **Send to Kindle** — `POST /api/library/books/:book_id/send-to-kindle` with optional `{format: "epub|pdf|..."}`. Resolves the format per priority (#05), calls into the existing email-output pipeline (`shelfmark/download/outputs/email.py`) pointed at the per-user Kindle address. Returns the email result.
- **Per-format download** — already exists as `GET /api/localdownload?id=...` (`shelfmark/main.py:1580-1615`); adapt or wrap. See #08.

Open questions this ticket must resolve:

- **Scoping.** The architectural survey shows per-user enforcement in three places (`BookQueue.get_status`, `DownloadHistoryService.list_recent`, ownership re-checks in `main.py:1467-1518`). Library endpoints need the analogous treatment: non-admin sees only their own library rows. But files-on-disk are global — does the API return file paths to a non-owner user, or only "yes/no file exists" + a download URL that goes through ownership-aware serving? Decide and defend.
- **Admin powers.** Does an admin see all users' libraries (admin = instance-wide read for support/debug), or are they scoped to their own like everyone else? The existing activity API treats admins as instance-wide (`activity_routes.py:267-273`) — does the library API mirror that?
- **Routing & content negotiation**. Where do these endpoints live — new blueprint, or extend the monolith in `shelfmark/main.py`? The activity API lives in `shelfmark/core/activity_routes.py` as a blueprint — that's the existing precedent.
- **Pagination strategy.** Cursor vs offset. Reflect on how many books the library might hold (10? 10k?) and pick accordingly.
- **Metadata refresh.** When metadata provider returns richer data after the row was created, do we refresh on read? On a manual button? Never? Define a contract; otherwise we'll drift.

The output of this ticket is a documented API contract: route table, request/response shapes, scoping rules. Implementation lives in #10 (no — wait, see #11).

## Outcome of this ticket

API contract document. Implementation ticket #11 picks this up.

## Answer

Resolved 2026-07-20 by grilling session. The contract is shaped as routes living in `shelfmark/core/library_routes.py` (the project's existing `register_*_routes(app, ...)` pattern — not a Flask Blueprint; `activity_routes.py`, `request_routes.py`, `admin_routes.py`, `self_user_routes.py` all follow this). Each sub-decision recorded with rationale.

### Sub-decisions

1. **Routes live in `shelfmark/core/library_routes.py` registered via `register_library_routes(app, user_db, *, download_history_service, ...)`.** Called from `main.py:516-541` next to the existing conditional registrations. Follows the established precedent (every API surface since the monolith has been extracted to a `register_*_routes` module). Flask Blueprint explicitly rejected because none exists in the codebase today and introducing one would break the convention. Cite: `shelfmark/core/activity_routes.py:504-515`, `main.py:516-541`.

2. **Admin scoping: instance-wide read, scoped-to-self mutations.** Admin sees every user's Library + every user's file list (per-Book `formats_on_disk`, release-level metadata). Mutations (Add/Remove/Send-to-Kindle) stay scoped to admin's own library — cannot remove another user's library row or push to another user's Kindle. Rationale: matches activity API for read (`activity_routes.py:215-273` `owner_scope=None` for admin); for mutations the cost asymmetry is real — dismissing an activity card is reversible UI state, removing a Library row is hard-delete, Send-to-Kindle consumes an SMTP send and pushes to someone's device. If an admin needs to fix a user's library for support, the existing tool is direct DB surgery, same as today. Cite: `activity_routes.py:215-273`, `activity_routes.py:285-292` (`_check_item_ownership`).

3. **Shared global file pool; file visibility is per-user-per-release via `user_downloads` link table.** API returns the union of `download_history` rows linked to the Book across **all users** — no per-user attribution, no username surfaced. Per-user-per-release link lives in new table:

   ```sql
   CREATE TABLE IF NOT EXISTS user_downloads (
       user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
       history_id   INTEGER NOT NULL REFERENCES download_history(id) ON DELETE CASCADE,
       added_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
       PRIMARY KEY (user_id, history_id)
   );
   CREATE INDEX IF NOT EXISTS idx_user_downloads_history
       ON user_downloads (history_id);
   ```

   Unlinking a release from a user's library is a hard `DELETE FROM user_downloads WHERE user_id=? AND history_id=?`; the `download_history` row and file on disk are untouched. Multiple users can link the same `download_history` row. ADR `docs/adr/0002-library-file-visibility.md` captures the rejected alternatives (hidden-files mask table = "tombstone through the back door" — violates #01's "no tombstone" rule; promoting `user_library` to include `download_history_id` — would break ADR 0001's Book-level membership invariant). #01 and ADR 0001 stay valid.

4. **`download_history.user_id` becomes an audit field; the load-bearing column for library file visibility is `user_downloads.user_id`.** Today `download_history.user_id` means "the auth identity who triggered the download" (`main.py:1321-1349` queue hook). Under the new contract it stays as-is for the existing download pipeline; library file visibility uses `user_downloads.user_id` exclusively. The "triggering user" identity is not exposed via the library API.

5. **Any user can grab another release anytime; the Q3-grab gate is repealed.** Non-admins are NOT blocked from grabbing a competing release when files already exist. Sub-decision 6 of #02 ("in-flight counts as files exist") stays valid for the **auto-open Find Releases modal** on the book-detail page navigation; it does NOT gate the manual Find Releases button. The manual button is ungated for all users. #02 sub-decision 8 ("race condition moot") stays valid — and is now deliberately moot (the in-flight predicate still prevents auto-open races). Rationale: Books are not single-release; epubs are sometimes broken, users need to grab another release and select between them; the union-of-formats is the high-level view, the release-level detail (indexer, protocol) is first-class on book-detail. The admin override modal from the earlier discussion is scrapped (no gate to override).

6. **Release-level metadata surfaces on book-detail per file.** A `download_history` row linked to a Book produces a file entry with: `{format, size, indexer_display_name, protocol, downloaded_at, downloadable_by_me: bool}` (the `downloadable_by_me` flag mirrors whether the user has the row in their `user_downloads`). The union-of-formats collapse for the high-level view is the unique set of formats across all rows. The file list is the per-row detail.

7. **File serving gates on `user_library` membership (NOT `download_history.user_id`).** The current `GET /api/localdownload?id=...` path (
   `main.py:1580-1627`) has a zero-ownership leak on the in-flight queue path: `backend.get_book_data` reads `task.download_path` and serves bytes to ANY authenticated user (`orchestrator.py:342-360`, `queue.py:117-120` — `get_book_data` and `get_task` have no user filter). The library `GET /api/library/books/:book_id/download?format=...` endpoint fixes this structurally — gate is Library membership for the Book (the user has the Book in their `user_library`), not File ownership. Closes the existing cross-user byte-exposure bug as a side-effect. Whether the fix to `get_book_data` lands in #04's resolution or as a separate bug ticket is deferred to declaration time; either way, the new library serve endpoint enforces the correct gate.

8. **Orphan concept introduced.** An Orphan is a `download_history` row with zero `user_downloads` entries (and not in-flight). Orphans are admin-cleanup candidates, deferred to a future ticket. The concept is added to `CONTEXT.md` as a fourth term. The cleanup mechanism is parked on the map's "Not yet specified" — not decided here.

9. **No pagination for MVP.** `GET /api/library/books` returns the full library. Tens-to-low-hundreds of Books serialize to ~100KB of JSON; acceptable for personal libraries. Pagination + structured filtering (`?author=`, `?has_files=`, `?page=`, `?page_size=`) deferred to post-MVP — parked on the map's "Not yet specified", surfaces when a user feels the absence.

10. **Fuzzy search via plain `LIKE` on `books.title` and `books.author`.** `GET /api/library/books?q=ender` returns the subset matching `books.title LIKE :q OR books.author LIKE :q` (case-insensitive via SQLite's default case-insensitive `LIKE` on TEXT). FTS5 explicitly rejected for MVP — short strings don't benefit from tokenization, and the apostrophe problem ("Ender's Game" tokenizes but "enders" input doesn't match) makes FTS5 worse-than-LIKE for short-title lookups. FTS5 is parked as a graduation candidate if LIKE proves too dumb. Ordered by `user_library.added_at DESC`.

11. **Add to library — idempotent, response shape fixed.** `POST /api/library/books` with `{metadata_provider, provider_book_id}` returns `{book_id, files_exist_globally, in_flight_globally, in_my_library: true}` — same payload whether the link was newly inserted or already existed. No 409. The "already in library" button state on the search-results card / `DetailsModal` is driven by a separate `GET /api/library/books/:book_id` lookup (the membership check), not by the Add response — the button renders before Add is clicked. Cite: `DetailsModal.tsx:404-422`, `App.tsx:1512`.

12. **`files_exist_globally` and `in_flight_globally` are two distinct flags.** #02's sub-decision 6 predicate merges them into one "files exist" boolean; the API response splits them:

    ```sql
    -- files_exist_globally
    SELECT 1 FROM download_history
    WHERE book_id = :book_id AND final_status = 'complete' AND download_path IS NOT NULL
    LIMIT 1;

    -- in_flight_globally
    SELECT 1 FROM download_history
    WHERE book_id = :book_id AND final_status = 'active'
    LIMIT 1;
    ```

    Covered by `idx_download_history_book_id` on `(book_id, final_status)` from #01. #02's single-boolean predicate stays valid for the "should Find Releases auto-open?" decision; the API's response payload splits the two.

13. **Metadata fetch failure during Add → 503, no row inserted.** The entire point of ADR 0001's "denormalized snapshot" invariant is that the `books` row is self-sufficient. A row with NULL title/author is useless to render. The Add endpoint invokes `prov.get_book(book_id)` (the server-side equivalent of `/api/metadata/book/<provider>/<book_id>` — `main.py:2671-2709`); on provider unavailability or rate-limit, return 503 with `{"error": "Metadata provider unavailable"}`. No `books` row inserted; no `user_library` link. User can retry. Lenient variant (stub row with NULLs) explicitly rejected — creates persistent junk data and conflicts with ADR 0001.

14. **Send-to-Kindle: required per-user recipient; no fallback to `EMAIL_RECIPIENT`.** The recipient is stored as `kindle_address` in `user_preferences` and is visible in My Settings for every authenticated user, including administrators. `EMAIL_RECIPIENT` remains the default download-output email. Two distinct concerns; no fallback.

15. **`EMAIL_FROM` is admin-set, required at instance level.** Env var takes priority and locks out UI edits; UI editable when not env-set. Matches existing convention at `shelfmark/config/settings.py`. If `EMAIL_FROM` is unset at both env and UI, Send-to-Kindle is unavailable for all users — but the API does not invent a new "not configured" error code; instead `send_file_to_email` raises an operational error and the route returns `{"error": str(e)}, 500` via `_OPERATIONAL_ERRORS`. All other SMTP options (`EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, etc.) remain as-is per `email.py:130-141`.

16. **Send-to-Kindle endpoint follows existing error conventions — no invented codes.** Route: `POST /api/library/books/:book_id/send-to-kindle` with optional body `{format?: "epub|mobi|azw3|pdf|..."}`. Fail-fast in this order, all following existing activity/main conventions:
    - User lacks `user_library` membership → 403 `{"error": "Forbidden"}`.
    - No compatible file (`download_history.final_status='complete'` AND `download_path IS NOT NULL` AND format in Amazon's accepted Kindle formats AND `user_downloads` links the row to this user) → 404 `{"error": "No compatible file found"}`.
    - Per-user recipient unset → 400 `{"error": "No email recipient configured"}` (mirrors `_post_process_email`'s message at `email.py:339`).
    - SMTP error bubbles via `_OPERATIONAL_ERRORS` → 500 `{"error": str(e)}`.
    Success response: `{"status": "sent", "recipient": "<masked>", "format": "<chosen>"}`. Masking reuses the existing `label` mechanism (`email.py:335-346`) — not a custom masker for MVP.

17. **Implementation mechanism: new `send_file_to_email(...)` in `shelfmark/download/outputs/email.py`.** Signature: `send_file_to_email(file_path: Path, recipient: str, *, label: str | None = None, subject: str | None = None) -> None`. Reuses existing `compose_email_message` + `send_email_message` + `_get_email_settings()` internals — no new mailer, no synthetic `DownloadTask`. Library routes call this directly; email-module concerns stay in the email module. Cite: `email.py:130-141` (settings), `email.py:421-428` (existing compose+send call).

18. **Refresh metadata: out of scope.** No manual refresh button, no `POST /api/library/books/:book_id/refresh-metadata`, no scheduled refresh — for this entire effort. The `books` row written at Add time is the final word on metadata. Future "scheduled refresh like Sonarr/Radarr" lands as a fresh effort, not as a graduation from this map. Removed from the map's "Not yet specified" entirely (was previously fogged there in #01's "what this ticket does NOT decide" — now explicitly out of scope for the whole effort).

### Route table (final)

| Method | Path | Auth/scope | Response shape |
|---|---|---|---|
| `POST` | `/api/library/books` | Auth | `{book_id, files_exist_globally, in_flight_globally, in_my_library}`; 503 on provider down |
| `GET` | `/api/library/books` | Auth; admin sees all, user sees own | `{books: [{book_id, title, author, cover_url, formats_on_disk: [{format, size}], added_at}]}`; `?q=` for fuzzy LIKE on title/author |
| `GET` | `/api/library/books/:book_id` | Auth + `user_library` membership (admin reads any) | Full metadata + per-file list with release-level metadata (indexer, protocol, downloadable_by_me) |
| `DELETE` | `/api/library/books/:book_id` | Auth; admin scoped to own | `{status: "removed"}` |
| `POST` | `/api/library/books/:book_id/send-to-kindle` | Auth + `user_library` membership | `{status: "sent", recipient: "<masked>", format}`; 400/404/500 per sub-decision 16 |
| `GET` | `/api/library/books/:book_id/download?format=...` | Auth + `user_library` membership (gates on Library membership, NOT file ownership) | File bytes via `send_file` |
| `POST` | `/api/library/books/:book_id/downloads/:history_id` | Auth + `user_library` membership | `{status: "linked"}` — inserts `user_downloads(user_id, history_id)`; used at download finalize time |
| `DELETE` | `/api/library/books/:book_id/downloads/:history_id` | Auth + `user_downloads` ownership | `{status: "unlinked"}` — hard deletes the per-user link; `download_history` row + file untouched |

### What this ticket does NOT decide (deferred to other tickets)

- The `user_downloads` migration code itself → #03 (amended to add the new table).
- The Send-to-Kindle format-priority algorithm (which format to send when multiple are available) → #05 (narrowed scope — only the algorithm; setting + endpoint shape live here).
- The frontend routing for `/library` and `/library/:bookId` → #07.
- The book-detail page UX (union-of-formats display, release-level metadata rendering, in-flight indicator, unlink UX, Send-to-Kindle button) → #08.
- The release-list dedup indicator → #09 (unchanged).
- The bookshelf rendering → #10 (unchanged).
- Search-results card + `DetailsModal` Add/In-Library button behavior → #11.
- Author browse → #12 (unchanged).

### Knock-on effects on other tickets (to be applied when those tickets are worked)

- **#02 sub-decisions 6 & 8**: stay valid as written. The in-flight predicate gates auto-open behavior only; the manual Find Releases button is ungated for all users. Q3's earlier "non-admin can't grab another release when files exist" gate is **repealed** — any user can grab another release anytime.
- **#03 inherits**: new `user_downloads` table + `idx_user_downloads_history` index (in addition to #01's `books`, `user_library`, `download_history.book_id` + `idx_download_history_book_id`). Migration approach unchanged (hand-rolled conditional creates per `user_db.py:196-271`).
- **#05 narrows**: only the format-priority algorithm is #05's call. The per-user recipient, endpoint shape, masking choice, and new `send_file_to_email` function are settled here in #04. #05 decides the default format priority list (likely `["epub"]` given Amazon's accepted formats; azw3/mobi were deprecated by Amazon for Send-to-Kindle in 2022).
- **#07**: routing is `/library` + `/library/:bookId` only (+ `/authors/:authorId` from #12). No `/find` route per #02 sub-decision 4.
- **#08**: scope restated — book-detail must host the union-of-formats display (high-level) AND the per-file list (release-level: format, size, indexer_display_name, protocol, downloadable_by_me, downloaded_at). In-flight indicator stays. Manual refresh button is NOT in #08 (out of scope per this ticket). Unlink mid-flight behavior (the user unlinks an active `final_status='active'` row) is parked as a UX decision for #08.
- **#09**: unchanged.
- **#10**: unchanged.
- **#11 inherits**: Add response shape is `{book_id, files_exist_globally, in_flight_globally, in_my_library: true}` — idempotent. Already-in-library button state driven by a separate `GET /api/library/books/:book_id` lookup, not by Add's response. Add-to-Library endpoint invoked at click time; loading state shown during the metadata fetch; navigation to `/library/:bookId` after response.
- **#12**: unchanged.

### Open edge cases deferred (NOT decided in this ticket)

- **Unlink mid-flight** (in-flight `final_status='active'` row gets unlinked from user's `user_downloads`) — UX decision: disallow with error, or allow with warning. Deferred to #08.
- **Admin "download on behalf of user X"** — under the new model, the queue action may need to insert both `user_downloads(admin, R)` AND `user_downloads(user_x, R)`. Out of scope for MVP; parked on the map.
- **Orphan cleanup admin task** — mechanism, schedule, retention window all undefined. Parked on the map's "Not yet specified".
- **Pagination + structured filtering** — surfaces when a user feels the absence. Parked on the map's "Not yet specified".
- **FTS5** — graduation candidate if LIKE proves too dumb. Parked on the map's "Not yet specified".
- **Non-admin metadata refresh** — out of scope entirely (per sub-decision 18).

### Context assets

- `docs/adr/0001-books-as-denormalized-snapshot.md` — valid and unchanged; the Book-membership invariant holds, and the Book row's snapshot-at-add is final.
- `docs/adr/0002-library-file-visibility.md` — new — captures why `user_downloads` was chosen over the rejected alternatives (mask table, promoting `user_library`).
- `CONTEXT.md` — gains a fourth term (`Orphan`); the `File` entry is amended to reflect `user_downloads` as the load-bearing link; the `Library` entry is unchanged.
- `shelfmark/core/activity_routes.py:215-292` — admin vs. user scoping precedent (`_resolve_activity_actor`, `_check_item_ownership`).
- `shelfmark/main.py:1580-1627`, `shelfmark/download/orchestrator.py:342-360`, `shelfmark/core/queue.py:117-120` — the existing `GET /api/localdownload` serve path and the zero-ownership leak in `backend.get_book_data` (closed structurally by the new library serve endpoint's Library-membership gate).
- `shelfmark/download/outputs/email.py:130-141, 335-346, 421-428, 474-490` — the SMTP settings, recipient resolution, compose+send internally, and the `process_email_output` task-staging shape that the new `send_file_to_email` bypasses.
- `shelfmark/download/orchestrator.py:79-94` — existing `_resolve_email_destination` precedent for per-user email resolution via `config.get("EMAIL_RECIPIENT", user_id=user_id)`.
- `shelfmark/core/self_user_routes.py`, `shelfmark/core/user_db.py` — the per-user recipient settings and persistence path.
- `shelfmark/core/download_history_service.py:23-25, 66-75, 126-130` — `final_status` value set and the existing `_resolve_existing_download_path` filesystem-stat precedent.
- `shelfmark/core/user_db.py:27-116` — the existing 5-table schema; the new `user_downloads` table slots in alongside.

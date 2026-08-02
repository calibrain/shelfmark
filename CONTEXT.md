# Context

A glossary of the canonical terms in the Shelfmark fork's library effort. Devoid of implementation detail — for schema and implementation history, see the linked GitHub Issues; for decisions, see `docs/adr/`.

## Book

A **Book** is a metadata-provider-backed work — an instance of "this work exists in provider X with provider id Y". A Book is **not** a release and **not** a file on disk; it is the catalog identity. The `(metadata_provider, provider_book_id)` pair is the natural key (both NOT NULL); the same work added via two providers lives as two Book rows (cross-provider merge is out of scope for the library effort).

A Book row is a **denormalized snapshot** of the provider's `BookMetadata` at add-time: title, author (primary), subtitle, publish_year, isbn_13, cover_url, series_name, series_position, language are copied into columns. The row is self-sufficient — reading book detail or the bookshelf never calls the provider. The complete raw provider payload is preserved in `metadata_json` for fields not promoted to columns (including the full ordered `authors` list). Staleness is accepted; refresh is a deferred explicit action, not a read-time behaviour. `books.updated_at` bumps on metadata refresh only — cover re-fetches (if they ever exist) don't touch it.

## Library

A user's **Library** is the set of Book rows that user has chosen to track — a per-user link onto Books (`user_library`, PK `(user_id, book_id)`, both FKs `ON DELETE CASCADE`). Library membership is orthogonal to files on disk: a Book can be in a user's Library with zero files anywhere (wishlist semantics). Removing a Book from a Library is a hard `DELETE` of the link row — no `removed_at`, no tombstone. When that link is the final membership, the canonical Book and its Requests are deleted; Files become detached audit activity and remain on disk. Future "restore removed" UX is a fresh ticket that would introduce soft-delete then.

## Library Capability

A user's **Library Capability** is the administrator-assigned access level for the library workflow. A **download-capable user** may search releases and queue Downloads. A **request-only user** may add Books to their Library and submit book-level Requests, but cannot search or select releases. Admin status is a separate privilege that permits administrative operations and does not form a third Library Capability.

## Request

A **Request** is a request-only user's explicit signal that one Book in their Library has no completed Files available. It belongs to the requester and the canonical Book; adding a Book with completed Files creates no Request. A Request is `pending`, `fulfilled`, `rejected`, or `cancelled`; `fulfilled` means Files are available, not that an admin has selected a release. One selected release fulfils all still-pending Requests for that same Book and links its Files to each requester when the Download finalizes. Any path that makes Files available fulfils the pending Requests for that Book. A pending Request may be cancelled without affecting a shared Download.

## Notification

A **Notification** communicates a library event. Personal Notifications are sent to a User through one selected email or Apprise transport. Administrator Notifications are instance-level operational alerts sent through administrator-configured email or Apprise transports; they are separate from personal Notifications.

## Source Release

A **Source Release** is the durable identity of retained original download contents: the physical torrent or equivalent download source and its members. It is provenance, not Book-scoped. One Source Release can be selected for multiple Books and can produce multiple Import Activities. Shelfmark never alters or copies its unselected contents; it is available for selection only while the download client retains the original data.

## Import Activity

An **Import Activity** is one Book-specific attempt to select files from one Source Release and place the selected Files into immutable library storage. Each explicit selection creates a new Import Activity, while operational retries remain part of that same activity. It keeps an immutable Book target and selection/evidence snapshot for deterministic retry and audit. Its transient states are `matching` and `importing`; `needs review` is non-terminal; `completed`, `failed`, and `cancelled` are terminal. Files produced by an Import Activity belong to that activity; the original source contents remain with its Source Release. For a correction of the same Book from the same Source Release, the latest successful activity is the complete import: it supersedes every earlier activity's Files, which are removed from library availability and storage while their detached history remains for audit.

## File / Download

A **File** is a concrete downloaded artifact — one `download_history` row with its own `download_path`, `format`, and `size`. Files are global (per-instance, not per-user); the Library merely surfaces them. Adding to the Library never creates a File; downloading never creates a Library entry.

A single Import Activity may produce **multiple Files**. Files belonging to the same activity share a `task_id`; its source provenance is a distinct Source Release rather than a derived grouping of the File rows. `download_history.task_id` is therefore non-UNIQUE. At queue time, one `'active'` sentinel row per `task_id` (per-file columns NULL) stands in for the in-flight activity; at finalize, the sentinel is deleted and replaced by N concrete file rows.

`download_history.book_id` (nullable, `ON DELETE SET NULL`) links a File to a Book. Shelfmark creates no direct or non-library Downloads: every new Import Activity and File is Book-targeted. A cleared Book link is retained audit history after Book or Release Deletion, not a direct-download mode.

A user's library surfaces a File via the **`user_downloads(user_id, history_id)` link table** — the load-bearing column for file visibility in a user's library. Multiple users can link the same `download_history` row. Library file serving gates on `user_library` Book-membership (any user with the Book in their library can download any of its files), not on `download_history.user_id`. The `download_history.user_id` column stays as an audit field for "the auth identity who triggered the download" — not exposed via the library API.

## Release Deletion

A **Release Deletion** is an administrator-only, Import-Activity-atomic destruction of every File in an Import Activity (all `download_history` rows sharing a `task_id`). It deletes the immutable library artifacts, removes every `user_downloads` link, and clears the retained history rows' `book_id` and `download_path`. The history rows remain as audit records, but their Files are unavailable and the Import Activity is no longer associated with the Book for any user. Superseding an activity during correction uses the same deletion semantics. It does not destroy the retained Source Release; a later selection may create and link a new Import Activity for that Book.

`user_downloads` links are created at **finalize time** (when N file rows are concrete), not at queue time: one row per File for every applicable recipient: the administrator who initiated the Import Activity and every requester with a fulfilled Request for that Book.

## Orphan

An **Orphan** is a `download_history` row (a File) with zero `user_downloads` entries across all users AND not currently in-flight (`final_status != 'active'`). Orphans are admin-cleanup candidates; the cleanup mechanism (schedule, retention window, what deletes — file only vs file + row) is deferred and parked on the map's Not yet specified.

## Author

The **primary author** is a denormalized `books.author TEXT` column (one string). The full ordered author list lives in `metadata_json.authors`. There is **no `authors` table** — author identity is provider-specific (Hardcover numeric ids, Open Library `/authors/OL*` keys, Google Books none), so an instance-local author table would buy a join without buying cross-provider identity. A future authors table is parked in fog for the author-browse ticket to revisit if it demands an instance-local author route.

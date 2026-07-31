# Library Release Availability and Realtime Research

Research date: 2026-07-31. This note inspects the current source tree and tests only. Line references refer to the checked-out `main` worktree.

## Current Availability Model

A Book detail's `files` are instance-wide completed `download_history` rows for that Book with a non-null on-disk path; the query deliberately does not filter by user. The detail route derives `downloadable_by_me` separately from the returned file IDs, but the library file-serving gate itself is Book-library membership rather than ownership of a `user_downloads` link. [Source: `shelfmark/core/library_service.py:309-337`; `shelfmark/core/library_routes.py:397-416`; `shelfmark/core/library_routes.py:137-157`; `CONTEXT.md:29-35`]

The library overview renders a Book as having files when `formats_on_disk` is non-empty. Its server-side list endpoint populates that list from the same global completed-file query. [Source: `src/frontend/src/library/LibraryPage.tsx:9-13,147-148`; `shelfmark/core/library_routes.py:341-372`; `shelfmark/core/library_service.py:309-337`]

On adding a Book, the API returns two global booleans: `files_exist_globally` is a complete row with a path, and `in_flight_globally` is an active row. The search page uses both only to set one navigation-time `autoFindReleases` intent. [Source: `shelfmark/core/library_service.py:359-391`; `shelfmark/core/library_routes.py:281-339`; `src/frontend/src/library/SearchPage.tsx:225-239`]

For a request-only member with no files, Book Detail shows request state. A request-only member who already has files instead sees normal file controls, but not release discovery or release deletion. [Source: `src/frontend/src/library/BookDetailPage.tsx:86-131,460-503`; `src/frontend/src/tests/BookDetailPage.test.tsx:46-81`]

## Find Another Release UI

`AvailableFiles` renders **Find another release** whenever `canFindReleases` is true, without checking whether a download is in flight. `App` grants that capability to administrators and download-capable library users; request-only users do not receive it. [Source: `src/frontend/src/library/BookDetailPage.tsx:134-180`; `src/frontend/src/App.tsx:425-435`]

The control calls `onFindReleases(toReleaseBook(book))`; `App` stores that Book as `releaseBook` and renders `ReleaseModal`. The same modal is used for initial release discovery and this additional-release path. [Source: `src/frontend/src/library/BookDetailPage.tsx:44-56,479-502`; `src/frontend/src/App.tsx:327-329,440-461`]

The modal's release-search request uses the canonical `library_book_id`, not provider identity supplied by the client. The endpoint requires download capability, requires library membership for non-admins, loads the persisted Book, and derives provider identity from it. [Source: `src/frontend/src/tests/releaseModalRequestSearch.test.tsx:20-65`; `shelfmark/main.py:2722-2735,2796-2833`]

Selecting a release from the normal modal routes to `handleReleaseDownload`, which puts the canonical `book.book_id` in `library_book_id`, queues the release, then refreshes queue status. The download API validates that ID and membership for non-admins; the orchestrator copies it onto the queued `DownloadTask`. [Source: `src/frontend/src/components/ReleaseModal.tsx:1155-1172`; `src/frontend/src/App.tsx:214-248`; `shelfmark/main.py:980-1043`; `shelfmark/download/orchestrator.py:182-255`]

## Download Finalization

At queue time, the registered queue hook writes one active sentinel row containing the release-level fields, including `library_book_id`; it has no path, format, or size. [Source: `shelfmark/main.py:1287-1345,1478-1479`; `shelfmark/core/download_history_service.py:260-350`]

When a task first transitions to a terminal queue status, the queue invokes the terminal hook outside its lock. The hook builds one file row per `task.library_paths` (falling back to `download_path`) and calls `finalize_download_files`. [Source: `shelfmark/core/queue.py:132-172`; `shelfmark/main.py:1241-1284,1347-1369`]

With paths, finalization deletes the sentinel, inserts one terminal history row per file sharing the task ID, and links each row to the triggering user. If the terminal status is `complete` and the Book has pending requests, it links every inserted file to every pending requester and changes those requests to `fulfilled` in the same transaction. [Source: `shelfmark/core/download_history_service.py:381-400,484-546,548-608`; `tests/core/test_download_history_service.py:97-160`]

Without paths, finalization updates the sentinel to a terminal row and returns no fulfilled requests. Failed shared downloads consequently leave requests pending for a later successful release. [Source: `shelfmark/core/download_history_service.py:457-482`; `tests/core/test_download_history_service.py:163-218`]

After successful history finalization, the terminal hook emits request updates for fulfilled requests and emits a `download_terminal` activity update to admins and the task owner. It does not emit a library-file or Book-availability event. [Source: `shelfmark/main.py:1223-1238,1370-1402,1482-1505`]

## Current Realtime Flow and Gap

Socket connection and `request_status` place each SID in exactly one room: `admins` for administrators, otherwise `user_<db_user_id>`. Initial and requested queue status are sent directly to the socket; broadcast status sends full data to `admins` and filtered data to each active user room. [Source: `shelfmark/main.py:3218-3282`; `shelfmark/api/websocket.py:95-109,132-167`]

The frontend socket provider connects with the session cookie. `useRealtimeStatus` subscribes to `status_update` and `download_progress`, and falls back to polling `GET /api/status`; the release modal uses this status to change a release button through queued, resolving, locating, downloading, complete, or error. [Source: `src/frontend/src/contexts/SocketContext.tsx:22-64`; `src/frontend/src/hooks/useRealtimeStatus.ts:21-145`; `src/frontend/src/components/ReleaseModal.tsx:1118-1152`]

The activity hook subscribes to `activity_update`, `request_update`, and `new_request`, then refreshes activity endpoints. No frontend code subscribes to a library availability event, and Book Detail loads only at mount and after its own mutations. Therefore a completed release from another tab/client will not refresh the open Book Detail or Library Page through the current event flow. [Source: `src/frontend/src/hooks/useActivity.ts:408-434`; `src/frontend/src/library/BookDetailPage.tsx:327-355,384-392`; `src/frontend/src/hooks/useRealtimeStatus.ts:67-120`]

## Recommended Implementation Design

Emit a new `library_book_availability` Socket.IO event only after `finalize_download_files` has returned successfully, the terminal status is `complete`, `task.library_book_id` is valid, and at least one final file row was published. This placement means recipients cannot observe availability before the database transaction that makes the files queryable. It also avoids false availability for failed, cancelled, or no-path terminal tasks. [Source: `shelfmark/main.py:1347-1402`; `shelfmark/core/download_history_service.py:457-482,548-608`; `shelfmark/core/library_service.py:309-337`]

Recommended payload:

```json
{
  "book_id": 42,
  "task_id": "release-source-id",
  "availability": "available"
}
```

`book_id` is the invalidation key. `task_id` is useful for diagnostics and future release-specific UI, but clients should not infer file counts or permissions from it. `availability` makes the event extensible for a later `unavailable` event after release deletion.

Recommended recipients: emit once to `admins` and once to each `user_<id>` room for every current `user_library` member of that Book. Add a narrow `LibraryService` query returning those member IDs, rather than broadcasting globally: the current detail response exposes global availability only to members, and the room convention already isolates user-specific events. Do not limit recipients to the triggering user or fulfilled requesters, because any member sees global files on the Book detail and overview. [Source: `shelfmark/api/websocket.py:95-109`; `shelfmark/core/library_routes.py:374-416`; `shelfmark/core/library_service.py:309-337`; `CONTEXT.md:35,41`]

Recommended frontend invalidation: subscribe once through the existing shared socket mechanism and invalidate by `book_id`, not by trusting the event as file data. On receipt, re-fetch `GET /api/library/books/:book_id` if that Book Detail is open, and re-fetch `GET /api/library/books` for the visible library scope so `formats_on_disk` and the filters update. The activity hook may also refresh its snapshot, but it is not a replacement for these library reads. Keep `status_update` handling for modal button state; it is a separate queue-state concern. [Source: `src/frontend/src/contexts/SocketContext.tsx:22-64`; `src/frontend/src/library/BookDetailPage.tsx:327-355`; `shelfmark/core/library_routes.py:341-416`; `src/frontend/src/hooks/useActivity.ts:408-434`; `src/frontend/src/hooks/useRealtimeStatus.ts:67-120`]

Tests should cover successful multi-file finalization emitting the exact payload to `admins` and each member room, no emission for error/cancelled/no-path completion, and frontend re-fetches of an open matching detail plus the library list. Existing service tests already establish the multi-file/request transaction and terminal-snapshot tests establish room-targeted activity emission patterns. [Source: `tests/core/test_download_history_service.py:97-218`; `tests/core/test_activity_terminal_snapshots.py:204-269`; `src/frontend/src/tests/BookDetailPage.test.tsx:84-139`]

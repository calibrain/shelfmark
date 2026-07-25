Type: task
Status: ready-for-agent
Blocked by:

# Implement Library Capability and Request lifecycle

## Question

Implement the Library Capability and Book-level Request contracts settled by [Reconcile request policy with library-first user capabilities](01-reconcile-request-policy-with-user-capabilities.md) and [Define request lifecycle and ownership](08-define-request-lifecycle-and-ownership.md).

Replace the existing per-user request-policy machinery with the required, administrator-managed `library_capability` value: `download-capable` or `request-only`. Keep administrator status separate: administrators can operate the request queue and queue Downloads regardless of their assigned Library Capability.

Create the persistent Request model and API/service behavior. A Request belongs to a request-only user's Library Book and has only `pending`, `fulfilled`, `rejected`, and `cancelled` states. It may be created only while the Book has no completed Files. Adding a Book never implicitly creates a Request. A request-only user may create, read, and cancel only their own Requests; they cannot search or select releases. A download-capable user can use Book-scoped release discovery and queue Downloads, but cannot create Requests.

Implement the shared fulfilment path: an administrator may reject one requester, fulfil every pending Request for a Book from existing Files, or select one release for that Book. Requests remain pending while that shared Download runs. When Files become available by any path, fulfil every still-pending Request for the Book and link every resulting File to every requester. Cancellation does not alter an in-flight Download; a failed Download leaves Requests pending. Preserve terminal Request history and allow a new Request after rejected or cancelled states only while no completed Files exist.

Remove legacy request-policy rules, settings, endpoints, release-level Requests, and compatibility paths. This is greenfield: do not migrate legacy policy or Request data.

## Acceptance criteria

- User persistence and administrator account APIs expose and validate the two-value Library Capability without conflating it with admin status.
- Request routes and services enforce Library membership, exact canonical `book_id` matching, ownership, state transitions, and role boundaries.
- Shared Download finalization and any other path that creates completed Files atomically fulfil pending same-Book Requests and create the required `user_downloads` links.
- An administrator can fulfil from existing Files, reject an individual requester with an optional note, and retry after a failed shared Download.
- Legacy request-policy implementation and tests are removed rather than hidden or retained for compatibility.
- Focused backend, route, and state-transition tests cover capability permissions, cancellation during a Download, shared fulfilment, failure, and existing-File fulfilment.

## Implementation state

The canonical server-side foundation is implemented on draft PR [#6](https://github.com/muneebabbas/shelfmark/pull/6), branch `library-capability-request-lifecycle` (commit `3a941ae`). Backend core/config tests, Ruff, basedpyright, and frontend type checking passed at that commit.

The PR is intentionally not merge-ready. Review found that the old frontend still calls retired Request and fulfilment routes, does not receive `library_capability`, and permits request-only users to begin release discovery. These are migration/cutover gaps, not reasons to restore a legacy compatibility API. Tickets 17, 12, 13, 15, and 18 deliver the replacement client paths and final cutover. Keep this ticket claimed until ticket 18 verifies the complete role matrix; do not resolve it or merge PR #6 early.

## References

- [Library Capability decision](01-reconcile-request-policy-with-user-capabilities.md)
- [Request lifecycle decision](08-define-request-lifecycle-and-ownership.md)
- [Library domain vocabulary](../../../CONTEXT.md)
- [Library API and multi-file finalize foundations](../../library/issues/06-library-api-implementation.md)

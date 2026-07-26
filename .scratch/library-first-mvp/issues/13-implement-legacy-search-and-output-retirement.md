Type: task
Status: resolved
Blocked by: 17

# Implement legacy search and output retirement

## Question

Implement the deletion and role boundaries from [Define legacy search and output retirement boundaries](06-define-legacy-search-and-output-retirement-boundaries.md). Remove direct search completely: its UI, route/API, environment override, `SEARCH_MODE`, query builder, onboarding, configuration path, and tests. `Add New` remains metadata-provider Book lookup, not release search.

Keep release discovery strictly Book-scoped. A download-capable user may use the Book-derived query and queue a selected release. Only an administrator may edit a custom-query override inside that Book's release modal. Remove all generic output modes, browser auto-download, and per-user delivery/output destinations and overrides, including `BOOKS_OUTPUT_MODE` branches. Keep only administrator-managed instance configuration required to run shared Downloads. Completed Files are reached through explicit library download and Send to Kindle, not generic output routing.

## Acceptance criteria

- No standalone direct-search UI, route, API, configuration setting, or test remains.
- Book-scoped release discovery enforces Library Capability; the custom query control is administrator-only and unavailable elsewhere.
- Per-user search preferences, metadata-provider selections, output modes, and destinations are removed from persistence, APIs, and settings.
- Instance-level provider credentials, enabled providers, release-source configuration, and shared Download storage/destination configuration remain administrator-only.
- Tests cover deleted paths, role-gated Book-scoped release discovery, and retained administrator operations.

## References

- [Search and output retirement decision](06-define-legacy-search-and-output-retirement-boundaries.md)
- [Library Capability implementation](10-implement-library-capability-and-request-lifecycle.md)
- [Authenticated Library Capability client contract](17-expose-library-capability-to-authenticated-clients.md)

## Answer

The authenticated client now exposes only library workflows: metadata-backed Add New, Library and Book Detail routes, Book-scoped release discovery, Activity, and settings. The dormant global/direct search UI, route, URL bootstrap, query builder, search-mode state, and related client tests are deleted. Release discovery remains gated by Library Capability, and its custom-query control is restricted to administrators.

Shared Downloads now use the instance destination without user-scoped path expansion or generic output selection. The legacy email output handler was removed while retaining the SMTP helper used by Send to Kindle. Completed activity items no longer expose the legacy local-download endpoint; that route is removed and Library file downloads are the explicit file-serving path.

Verification: Ruff passed; 107 focused Python tests passed; frontend typecheck, focused capability tests, and production build passed.

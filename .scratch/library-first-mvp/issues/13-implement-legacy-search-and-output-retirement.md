Type: task
Status: resolved

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

## Resolution

Removed standalone direct search and generic output routing. Release discovery now requires a Library Book and capability, with administrator-only manual query overrides. Settings, onboarding, client contracts, documentation, and tests no longer expose legacy search modes, per-user provider/output choices, browser delivery, or Booklore/email download outputs.

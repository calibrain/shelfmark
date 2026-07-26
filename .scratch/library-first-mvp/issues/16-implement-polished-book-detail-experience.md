Type: task
Status: resolved
Blocked by: 11, 13

# Implement the polished book-detail experience

## Question

Implement [Design the polished book-detail experience](04-design-polished-book-detail-experience.md) as production-quality code, using the API-backed prototype on [`prototype/book-detail-experience`](https://github.com/muneebabbas/shelfmark/tree/prototype/book-detail-experience) only as a visual and interaction reference. Do not promote its development-gated code directly.

Keep the editorial-first hierarchy: Book identity, metadata-provider ratings, and description lead; `Available files` follows. Default to the newest completed File per format with direct download. Put every release, every File in a multi-File release, indexer, grab date, protocol, file-level download, and release unlinking behind collapsed `Advanced: show all releases`. Keep Send to Kindle as a distinct panel beside default files on desktop and stacked on mobile, with EPUB selected by default. `Find another release` is an advanced Book-scoped action.

Preserve one-shot Find Releases navigation: auto-open only after a successful first add with no global Files; direct links, refreshes, and later visits stay quiet. Add the request-only presentation from the Capability and Request contract: explicit add/request controls and pending/cancelled/fulfilled states, with no release, indexer, or download-machine controls.

## Acceptance criteria

- The detail page is responsive and follows the settled default and advanced information hierarchy.
- Latest-by-format direct downloads, multi-File release detail, exact-file downloads, release-atomic unlinking, and Send to Kindle work against live APIs.
- One-shot Find Releases behavior is preserved and release discovery obeys the retired-direct-search and role contracts.
- Request-only users see only settled Request state and actions; download-capable users and administrators see only the controls their capability permits.
- Focused frontend and route/API tests cover production behavior, role visibility, and the first-add navigation intent.

## References

- [Book-detail design decision](04-design-polished-book-detail-experience.md)
- [Library Capability and Request lifecycle implementation](10-implement-library-capability-and-request-lifecycle.md)
- [Persistent shell implementation](11-implement-persistent-library-app-shell.md)
- [Search and output retirement implementation](13-implement-legacy-search-and-output-retirement.md)
- [Prior closed planning-only ticket](09-implement-polished-book-detail-experience.md)

## Answer

The production Book Detail page now uses the settled prototype's editorial-first presentation: cached provider description and ratings lead, newest Files per format sit beside Send to Kindle on desktop, and release-level operations are collapsed under **Advanced: show all releases**. The page now consumes canonical request creation/list/cancellation APIs for request-only users and hides download, Kindle, and release controls from them. Successful first adds navigate with consumable router state, so Find Releases auto-opens only once for a download-capable user when no global Files or Download exist; links, refreshes, and later visits are quiet.

Verification: frontend typecheck, all 96 frontend unit tests, and production build passed; 31 focused library/request backend route tests passed. `npm run lint` still reports four pre-existing App shell violations unrelated to this ticket.

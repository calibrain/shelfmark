# Wayfinder Map: Library-First MVP

## Destination

A build-ready MVP specification for a library-first Shelfmark: persistent responsive navigation, `/library` as home, a polished book-detail experience, role-based download/request capabilities, and a simplified user-facing settings surface. It preserves the existing library and request foundations while removing legacy download-machine choices from normal users.

## Notes

- **Domain**: per-user ebook library management. A **download-capable user** may search releases and queue downloads; a **request-only user** may browse, add books, and submit book-level requests. Admins can download and fulfil requests from Activity.
- **Map mode**: planning only. This map produces decisions and a build-ready MVP specification; implementation is separate work after the route is clear.
- **Existing foundations**: reuse Shelfmark's request workflow (admin queue, fulfil/reject, WebSocket updates) rather than create a parallel request system. Replace the existing per-user policy machinery with Library Capability.
- **Navigation**: the persistent left sidebar has Library, Add New, and Settings, in that order, with no nested destination hierarchy. Add New opens the book-add modal. It becomes a top-left-menu drawer on narrow screens. `/library` is the default authenticated route. Activity stays as the existing stateful right-side sidebar, not a navigation destination or full page.
- **Settled constraints**: Direct search is removed entirely. Find Releases opens automatically only after a successful first add with no global files, carried as one-shot navigation state; all later detail navigation is quiet. Admins fulfil requests in Activity.
- **User settings**: every user may control theme, display name, Kindle address, notification transport/destination, and notification enablement. User notifications use exactly one email or Apprise transport and have one enable/disable switch; their events are request rejected and requested book available. Admins control usernames, passwords, and instance configuration. Delivery Preferences, output modes, unrelated per-user destinations, and metadata-provider configuration do not belong in a normal user's settings experience.
- **Skills every session should consult**: `/domain-modeling` for capability, request, and settings terminology; `/grilling` for all product decisions. Work later UI decisions against the existing UI or through discussion; do not build standalone in-app prototypes that duplicate the shell.
- **Tracker**: local markdown under `.scratch/library-first-mvp/`. Map = this file. Tickets = `.scratch/library-first-mvp/issues/NN-<slug>.md`.
- **Delivery recovery (2026-07-25)**: ticket 10's draft implementation PR, [#6](https://github.com/muneebabbas/shelfmark/pull/6), established the canonical server contract but removed client-facing legacy routes before their replacement clients existed. Do not merge it to `main` yet. Treat its branch, `library-capability-request-lifecycle`, as the shared integration branch. First expand the authenticated client contract (ticket 17), then migrate settings (12), release discovery (13), and Activity (15). Ticket 18 performs the final legacy-client cutover and end-to-end role verification. A ticket may not remove a live client contract unless its replacement is delivered in that same ticket or ticket 18.
- **Recovery delivery (2026-07-25)**: [Expose Library Capability to authenticated clients](issues/17-expose-library-capability-to-authenticated-clients.md) is resolved and committed directly to the shared `library-capability-request-lifecycle` branch, which remains the branch behind draft PR #6. [Implement the simplified settings surface](issues/12-implement-simplified-settings-surface.md) is the next dependent recovery ticket; ticket 11 remains independently available for app-shell work.

## Decisions so far

<!-- the index — one line per closed ticket: enough to judge relevance, then zoom the link for the detail the ticket holds -->

- [Reconcile request policy with library-first user capabilities](issues/01-reconcile-request-policy-with-user-capabilities.md) — Replace request-policy settings with an admin-managed two-value Library Capability; book-level Requests are explicit, Book-identified, and shared fulfilment links Files to every requester.
- [Design the persistent library app shell](issues/02-design-persistent-library-app-shell.md) — Keep the existing Activity sidebar; add a persistent Library/Add New/Settings drawer that becomes a top-left-menu drawer on narrow screens.
- [Specify the simplified settings surface](issues/03-specify-simplified-settings-surface.md) — Retain the admin settings shell and instance controls; replace per-user overrides with one explicit self-settings pane, while admins own account access and Library Capability.
- [Define request lifecycle and ownership](issues/08-define-request-lifecycle-and-ownership.md) — Requests exist only while a Book has no Files; they remain pending through a shared download and fulfil atomically when Files become available.
- [Design the polished book-detail experience](issues/04-design-polished-book-detail-experience.md) — Keep a traditional editorial-first detail page, with latest Files by format by default and multi-File releases plus operational actions in an advanced section.
- [Design Activity as the request fulfilment experience](issues/05-design-activity-request-fulfilment-experience.md) — Keep Activity as the stateful drawer: grouped Book work drives admin fulfilment, while request-only users see only their own request history.
- [Define legacy search and output retirement boundaries](issues/06-define-legacy-search-and-output-retirement-boundaries.md) — Remove direct search and generic per-user outputs; retain Book-scoped release selection, with custom-query override restricted to admins.
- [Specify the user notification contract](issues/07-specify-user-notification-contract.md) — Separate role-relevant events from a single selected email or Apprise transport, with SMTP shared across all system email delivery.
- [Expose Library Capability to authenticated clients](issues/17-expose-library-capability-to-authenticated-clients.md) — The authenticated bootstrap contract and typed client state retain the canonical capability separately from administrator status.

## Not yet specified

## Out of scope

- **Author browse.** Deferred completely from the preceding Library map; it needs a fresh effort if reprioritized.
- **Implementation.** This planning map ends with a build-ready specification, not feature branches or production changes.
- [Implement the polished book-detail experience](issues/09-implement-polished-book-detail-experience.md) — Closed here because feature implementation begins only after this map is handed off as a build-ready specification.
- **Release-quality reporting and re-requesting another release.** Deferred from this MVP; Requests solve Book availability, not file-quality feedback or release replacement.

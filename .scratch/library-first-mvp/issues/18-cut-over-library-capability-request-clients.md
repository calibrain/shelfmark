Type: task
Status: resolved
Blocked by: 12, 13, 15, 17

# Cut over Library Capability and Request clients

## Question

Complete the integration branch cutover after the replacement settings, Book release-discovery, and Activity experiences are delivered. This ticket is the only final deletion boundary for the live client contract replaced by the Library Capability and canonical Book Request work.

Work on draft PR [#6](https://github.com/muneebabbas/shelfmark/pull/6), branch `library-capability-request-lifecycle`. Do not merge to `main` until this ticket is complete. Remove remaining frontend callers, stale UI branches, and tests that depend on retired request-policy or release-level Request behavior. Do not introduce a legacy compatibility API merely to make old callers pass.

## Acceptance criteria

- No shipped frontend path calls retired Request batch, per-request fulfilment, or request-policy APIs.
- The complete role matrix works end-to-end: request-only users request/view/cancel Book Requests without release information; download-capable users discover Book releases and queue Downloads but cannot create Requests; administrators manage capabilities and fulfil grouped Book work regardless of assigned capability.
- Request rejection, existing-File fulfilment, shared-download finalization, cancellation during a Download, and failed-download retry retain the lifecycle settled in ticket 08.
- Direct/release-discovery access is capability-gated on both server and client; the client is not relied upon as the authorization boundary.
- Full relevant backend and frontend suites pass, including lint/typecheck, and PR #6 has no release-blocking review findings.
- Only after the above verification: resolve ticket 10, append the map decision pointer, and mark PR #6 ready for review.

## References

- [Capability and Request foundation](10-implement-library-capability-and-request-lifecycle.md)
- [Simplified settings implementation](12-implement-simplified-settings-surface.md)
- [Legacy search retirement implementation](13-implement-legacy-search-and-output-retirement.md)
- [Activity implementation](15-implement-activity-request-fulfilment-experience.md)
- [Request lifecycle decision](08-define-request-lifecycle-and-ownership.md)

## Answer

The integration branch now has no frontend caller for batch Requests, per-request fulfilment, or request-policy behavior. It removes the unused release-level request confirmation flow and stale release-request controls; administrators now either mark a Book available from existing Files or choose a Book-scoped release. Request-only users remain on Book-only request, history, and cancellation flows, while discovery remains capability-gated client- and server-side.

Verification passed: frontend lint, typecheck, 86 unit tests, and production build; Ruff, basedpyright, and 22 focused canonical Request/download lifecycle tests. Draft PR [#6](https://github.com/muneebabbas/shelfmark/pull/6) has no reviews, comments, or checks reporting release-blocking findings. Ticket 10 remains claimed for its separate closure, preserving the one-ticket-per-session wayfinding rule.

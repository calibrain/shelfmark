Type: task
Status: ready-for-agent
Blocked by:

# Expose Library Capability to authenticated clients

## Question

On draft PR [#6](https://github.com/muneebabbas/shelfmark/pull/6), `library-capability-request-lifecycle`, make the canonical, server-managed Library Capability available to the authenticated frontend as one reliable source of truth. This is the expand step before the settings, release-discovery, and Activity migrations.

The backend foundation already persists and validates `download-capable` and `request-only`, but the shipped client neither receives nor types the value. As a result, it currently hard-codes the old download flow and still calls retired Request APIs. Do not restore a legacy request-policy field, old endpoints, or compatibility adapter. Do not implement new Activity or settings screens in this ticket.

Use the existing authenticated user/bootstrap contract that initializes the application. It must expose the capability together with the authenticated user's existing identity/admin information, and the frontend must consume the typed value without guessing a default. Preserve the rule that administrator status is separate from Library Capability: administrators retain operational access regardless of their assigned capability.

## Acceptance criteria

- The authenticated application receives a validated `library_capability` value from the canonical server contract, and the frontend has a matching non-optional type.
- The client retains the capability in its authenticated state so later tickets can gate UI actions without an additional policy fetch or a hard-coded mode.
- Missing or invalid stored values are handled by the same server-side validation/default behavior used for user persistence; the UI does not silently choose download-capable access.
- Backend/API and frontend tests demonstrate both capability values and the independence of administrator status.
- No legacy request-policy client API, endpoint, type, or compatibility path is restored.

## Starting context

- Work on `library-capability-request-lifecycle`, not `main`; it is the branch for draft PR #6.
- Commit `3a941ae` is the current baseline. The working tree was clean when this recovery lane was written.
- Review findings to eliminate in later tickets: the frontend posts to removed `/api/requests/batch`, calls a removed per-request fulfil route, hard-codes download behavior, and lacks capability data. This ticket addresses only the last two prerequisites: the authenticated contract and typed client state. Ticket 13 owns release discovery, ticket 15 owns Request/Activity actions, and ticket 18 owns final verification and removal of remaining legacy callers.
- Before coding, re-read the capability decision in ticket 01 and the lifecycle decision in ticket 08. The map is authoritative for the recovery order.

## Verification

- Run focused backend/API tests and focused frontend tests for the authenticated contract.
- Run `npm run typecheck` from `src/frontend`.
- Run the relevant formatter/linter commands for edited areas.

## References

- [Library Capability decision](01-reconcile-request-policy-with-user-capabilities.md)
- [Request lifecycle decision](08-define-request-lifecycle-and-ownership.md)
- [Capability and Request foundation](10-implement-library-capability-and-request-lifecycle.md)
- [Recovery order](../map.md)

Type: task
Status: resolved
Blocked by: 12

# Implement the user notification contract

## Question

Implement [Specify the user notification contract](07-specify-user-notification-contract.md). Personal Notifications use one saved transport, SMTP email or a user-provided Apprise URL, represented by `notifications_enabled`, `notification_transport`, and `notification_destination`. Changing the transport replaces the prior destination; enabling requires a valid destination. `PUT /api/users/me` is the self-settings contract and an enabled saved destination may receive a transport-neutral test notification.

Send no personal event picker. Send only `Requested book available` when a pending Request becomes fulfilled because completed Files are available, and `Request rejected` when an administrator rejects it. Reuse the single SMTP configuration for Kindle, Personal Notifications by email, and administrator email targets. Preserve administrator instance-level targets with email/Apprise transport and any subset of `New request submitted`, `Download complete`, and `Download failed`.

Delete the old per-user notification routes, event selectors, overrides, and test endpoints. A non-admin must never see or subscribe to operational administrator events.

## Acceptance criteria

- Personal Notification persistence, validation, self-settings, and active-destination test behavior match the settled single-transport contract.
- Request fulfilment and rejection emit exactly the applicable personal notifications, with Book context and a direct detail link for availability.
- Queueing, failure, cancellation, request creation, and other requesters' transitions produce no personal notification.
- Administrator target configuration remains instance-level and isolated from personal events.
- Obsolete notification preferences and endpoints are removed; focused tests cover transport validation, role isolation, and event mapping.

## References

- [Notification contract decision](07-specify-user-notification-contract.md)
- [Library Capability and Request lifecycle implementation](10-implement-library-capability-and-request-lifecycle.md)
- [Simplified settings implementation](12-implement-simplified-settings-surface.md)

## Answer

Personal Notifications now use each user's saved enabled email or Apprise destination, with validation, transport replacement, and an authenticated saved-destination test action. They send only Book-contextual Request rejection and availability messages; download lifecycle events no longer notify the queue owner.

Administrator targets are instance-level email or Apprise targets for new requests and download completion/failure only. Legacy per-user route settings and endpoints are removed. The delivery implementation is commit `6145209` on `library-capability-request-lifecycle`; focused backend tests, frontend unit tests, typecheck, and production build pass. Frontend lint still reports four pre-existing `src/App.tsx` violations.

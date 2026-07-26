Type: task
Status: claimed
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

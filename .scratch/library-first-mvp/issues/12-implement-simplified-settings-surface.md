Type: task
Status: ready-for-agent
Blocked by: 17

# Implement the simplified settings surface

## Question

Implement [Specify the simplified settings surface](03-specify-simplified-settings-surface.md). Preserve the administrator Settings modal and its instance-level operational configuration, while replacing the normal-user settings experience with one explicit self-settings pane.

The self-settings pane shows username/account email read-only and permits only display name, Kindle address, Personal Notification transport/destination, and notification enablement; theme remains local. Administrators manage usernames, password reset, active/admin state, and Library Capability, but do not edit another user's personal preferences. The final notification field shape and behavior are supplied by [Implement the user notification contract](14-implement-user-notification-contract.md).

Remove the generic per-user override model and its delivery, search, request-policy, output, destination, and metadata-provider sections, stored keys, endpoints, and administrator editing UI. Do not add migration behavior.

## Acceptance criteria

- Regular users see only the settled self-settings controls and read-only account identity.
- Administrator settings preserve instance configuration and add Library Capability administration from the core capability contract.
- Server-backed self-settings use one explicit self-settings contract rather than generic override categories.
- Obsolete per-user settings storage, routes, controls, and tests are deleted.
- Frontend and API tests enforce personal-preference versus administrator-access boundaries.

## References

- [Simplified settings decision](03-specify-simplified-settings-surface.md)
- [Library Capability implementation](10-implement-library-capability-and-request-lifecycle.md)
- [Authenticated Library Capability client contract](17-expose-library-capability-to-authenticated-clients.md)
- [User notification implementation](14-implement-user-notification-contract.md)

Type: task
Status: resolved
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

## Answer

The generic per-user override system is removed. `GET` and `PUT /api/users/me` now expose one explicit self-settings contract: read-only username and account email, plus display name, Kindle address, and the persisted personal-notification transport, destination, and enablement fields. Theme remains local. Normal users cannot update account-access fields, and no delivery, search, request-policy, output, destination, or metadata-provider override route, UI, or storage remains.

The administrator Settings shell and instance configuration remain. Administrators now manage username, password, active state, role, and Library Capability, but cannot edit another user's personal preferences. Send to Kindle reads the explicit personal Kindle address.

The notification fields persist the settled shape; validation of an enabled destination, sending, and event delivery remain with [Implement the user notification contract](14-implement-user-notification-contract.md).

Focused backend tests (31), frontend typecheck, all 105 frontend unit tests, frontend lint, Ruff, and `git diff --check` pass.

Type: task
Status: ready-for-agent
Blocked by: 11, 13, 17

# Implement Activity request fulfilment experience

## Question

Implement [Design Activity as the request fulfilment experience](05-design-activity-request-fulfilment-experience.md) in the existing stateful Activity drawer. Do not create an Activity route or full page.

For request-only users, Activity contains only `Requests`, with pending work before durable terminal history. Show only request-oriented states: `Awaiting availability`, `Available`, `Not approved` with an optional administrator note, and `Cancelled`. Only pending Requests expose `Cancel request`; do not expose release, indexer, source, Download progress, or other download-machine details.

For administrators, open `Requests` when Book work is pending and otherwise retain `All`. Group pending work by canonical Book, showing Book identity, requester count, and an expandable requester list. Provide `Find release`, `Mark available` for existing Files, and selected-requester `Reject`. `Find release` opens the Book-scoped release picker above the still-open drawer and carries Book/requester context. A selected release begins one shared Download; failures return the Book card as retryable pending work. Reuse WebSocket updates for request cancellation, fulfilment, and queue changes.

## Acceptance criteria

- Activity remains a stateful right-side drawer across shell navigation.
- Request-only users have no operational Download or release information and can cancel only pending Requests.
- Administrator pending work is grouped by Book, supports the settled actions, and updates live as requesters cancel or Files finalize.
- Shared Download failure and the zero-pending-requester case have the settled behavior: retryable Book work when Requests remain, Download continues when none remain.
- UI and route/service tests cover role visibility, grouped work, actions, and live-update states.

## References

- [Activity fulfilment design decision](05-design-activity-request-fulfilment-experience.md)
- [Library Capability and Request lifecycle implementation](10-implement-library-capability-and-request-lifecycle.md)
- [Authenticated Library Capability client contract](17-expose-library-capability-to-authenticated-clients.md)
- [Persistent shell implementation](11-implement-persistent-library-app-shell.md)

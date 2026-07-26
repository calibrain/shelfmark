# Give book-detail calls to action a consistent affordance

Status: ready-for-agent

## Problem

Shelfmark generally highlights calls to action, but newer book-detail controls do not consistently communicate that they are actionable. Mouse users also do not consistently receive a pointer cursor.

## Deliverable

Audit the interactive controls on the book-detail page and make every call to action use the established visual emphasis plus `cursor: pointer`. Preserve the existing visual language and disabled/loading behavior.

Start with book detail only. Do not broaden the change to every screen; create a follow-up ticket if the shared pattern exposes a clearly scoped wider rollout.

## Acceptance criteria

- Book-detail CTAs are visually distinguishable from static content using the project's existing action styling.
- Hoverable enabled CTAs use a pointer cursor.
- Disabled controls retain their non-actionable behavior and do not show a misleading pointer cursor.
- Keyboard focus and existing mobile behavior remain intact.
- Relevant frontend checks pass.

# Make available-file metadata meaningful

Status: ready-for-agent

## Problem

The Available files UI can show a format followed by an unexplained number and `Prowlarr`, for example:

```
EPUB
779412
Prowlarr
```

The number has no obvious user value and appears to expose an internal identifier.

## Deliverable

Trace the available-file payload and rendering to identify the source of each displayed value. Remove or replace internal identifiers with concise, usable file information, following the established book-detail presentation. Retain provenance only when it helps a user understand the available file.

## Acceptance criteria

- Available-file metadata contains no unexplained internal numeric identifier.
- Each displayed value has a clear user-facing meaning, such as format, readable filename, size, date, or useful source provenance.
- API data required by other clients is not removed accidentally; change presentation only unless the field is demonstrably dead.
- Relevant frontend and backend checks pass.

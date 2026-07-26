# Present downloaded books with readable filenames

Status: ready-for-agent

## Problem

A real Prowlarr plus qBittorrent download produced a correct EPUB whose filename was a UUID. The artifact should instead have a presentable, book-identifying name.

## Deliverable

Trace the completed-download and file-serving paths to determine why the UUID name survives. Apply the repository's existing filename-formatting conventions so completed book artifacts are named from the Book metadata, using a readable `Title - Author`-style filename and preserving the actual extension.

Cover the relevant path used by real Prowlarr/qBittorrent downloads, including safe filename normalization and a deterministic fallback when metadata is incomplete. Avoid changing file content or relying on release-provider filenames as the display name.

## Acceptance criteria

- A completed EPUB for a Book is stored and/or offered to the user with a readable Book-derived filename rather than a UUID.
- The extension remains correct.
- Title and primary author are used when available; incomplete metadata produces a safe deterministic fallback.
- Existing filename-formatting examples and tests are followed or extended.
- Relevant backend checks pass.

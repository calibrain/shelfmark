# Library Check

Shelfmark can mark search results you already own, so you do not download a second copy
of a book that is already on your shelf. The check is read only and off by default.

## Calibre

Point Shelfmark at the `metadata.db` of a Calibre library (Calibre, Calibre-Web,
Calibre-Web-Automated, anything that keeps the standard Calibre format) and it reads the
database directly. No HTTP call, no API token, and nothing is ever written back.

1. Mount the library folder into the container read only, for example
   `/path/to/calibre-library:/calibre-library:ro`. Mount the folder rather than the file
   so the `-wal` and `-shm` sidecars are visible, otherwise a library that is being
   written to can read as out of date.
2. In **Settings, General**, turn on **Mark books already in your Calibre library**.
3. Leave **Calibre metadata.db path** at `/calibre-library/metadata.db` unless you mounted
   it somewhere else.
4. Press **Test Calibre library**. It reports how many books it indexed.

A result that matches the library then carries an **In library** badge in the card, list
and compact views, and in the details dialog.

## How a match is decided

In order of confidence:

1. An external id the metadata provider and the library agree on.
2. An ISBN, compared in both ISBN-10 and ISBN-13 form.
3. Fuzzy title tokens plus the author surname, the same rule the rest of the app uses for
   book matching.

## Behaviour worth knowing

- **It fails open.** If the database cannot be read, Shelfmark logs a warning, reuses the
  last successful read if it has one, and otherwise treats the book as not owned. A broken
  path degrades the badge, it never blocks a search.
- **Results are cached** for ten minutes, and refreshed early when the database file
  changes, so a large library costs one read rather than one per search.
- **Ebooks only.** A Calibre library holds ebooks, so the badge answers for ebooks. The
  provider interface in `shelfmark/core/library_providers/` takes more libraries: add a
  module with the `LibraryProvider` shape and list it in `all_providers()`. Nothing above
  that function knows which libraries exist.

# Collection Torrent Book Matching Research

Research date: 2026-08-01. This note compares the current Shelfmark fork with
`../shelfmark-upstream`, examines the seeded local database, and proposes work
to import only Files that belong to the selected Book.

## Finding

Book `15` is Hardcover `312460`, *Dune* by Frank Herbert. Its selected
Prowlarr/MyAnonamouse release is `https://www.myanonamouse.net/t/261248`.
The task completed with `Complete (38 files)` and every resulting history row
was assigned `book_id = 15`.

Those files comprise 19 EPUB and 19 MOBI files. Only two are *Dune*; the rest
include *Dune Messiah*, *Children of Dune*, prequels, sequels, and companion
material. The UI summary shows EPUB and MOBI because it selects the newest
file per format. The advanced section groups all 38 rows by their shared
`task_id`, so it correctly displays the full physical download activity.

The local server was not listening on port 8084 during research, so this was
verified read-only against `.local/config/users.db` (`PRAGMA integrity_check`
returned `ok`) and the persisted task data, rather than through the live API.

## Cause

The chosen search result is associated with the Book once, at queue time.
`queue_release()` copies `library_book_id` onto `DownloadTask`; the postprocess
scanner recursively returns every file whose extension is an enabled ebook
format; and the folder output transfers every returned path. At terminal
handling, Shelfmark creates one File/history row for every transferred path,
all inheriting the active task's Book association. There is no per-file title,
author, or identifier comparison. [Source: `shelfmark/download/orchestrator.py:173-245`; `shelfmark/download/postprocess/scan.py:119-310`; `shelfmark/download/outputs/folder.py:200-271`; `shelfmark/main.py:1263-1405`]

This follows the current domain model: a release is all Files sharing a
`task_id`. That is valid for a multi-format release but is too broad when a
torrent is a collection. [Source: `CONTEXT.md:27-35`]

## Upstream Comparison

Upstream does not have per-file Book matching, ISBN extraction from downloaded
files, a collection selector, or a per-file selection UI. Its scanner and
multi-file transfer behavior also import every supported file. Therefore there
is no upstream feature to restore for this bug. [Source:
`../shelfmark-upstream/shelfmark/download/postprocess/scan.py:119-395`;
`../shelfmark-upstream/shelfmark/download/postprocess/transfer.py:163-272`]

Upstream does contain a separate useful Prowlarr fix that this fork has not
absorbed. Commit `a4086f5` qualifies a Prowlarr release identity with its
indexer ID and deduplicates by `(indexerId, strong identity)`, avoiding cache
collisions and hidden results when the same tracker is configured more than
once. This should be ported independently; it improves the correctness of
which release is queued, but cannot decide which files inside it belong to a
Book. [Source: `../shelfmark-upstream/shelfmark/release_sources/prowlarr/utils.py:35-54`; `../shelfmark-upstream/shelfmark/release_sources/prowlarr/source.py:86-118,944-1018`; current `shelfmark/release_sources/prowlarr/source.py:390-394,862-900`]

Both projects use provider ISBN values only for metadata lookup and release
search planning. Neither validates or normalizes ISBNs as a post-download
matching signal. [Source: `shelfmark/metadata_providers/__init__.py:186-220`;
`shelfmark/core/search_plan.py:130-184`]

## Recommended Design

Place one deep `book_selection` module between candidate discovery and file
transfer. Its interface should be limited to:

```python
def select_files_for_book(candidates: list[Path], target: ImportTarget) -> SelectionResult:
    ...
```

`ImportTarget` is immutable Book evidence loaded by the server from the
persisted Book: Book ID, canonical title, primary/ordered authors, subtitle,
and normalized ISBNs. `SelectionResult` returns only selected paths, compact
per-candidate evidence for diagnostics, and a safe no-match reason. The module
owns parsing, normalization, evidence precedence, and rejection. Its callers
only decide whether to transfer `selected` files.

Apply this selection only to library-targeted torrent downloads initially.
Direct/legacy downloads retain their current all-supported-files behavior.
Never transfer an unselected collection member, and never create its history
or user-download row.

This is deliberately an affirmative, not fuzzy, matcher:

1. A checksum-valid embedded ISBN which the selected provider resolves to the
   same canonical work is strongest. A different valid ISBN alone is neither a
   match nor a rejection: ebook format/DRM editions can legitimately have a
   different ISBN.
2. An EPUB's complete embedded title and primary creator must exactly match the
   target after normalization; containment does not match. `Dune Messiah` is
   therefore not `Dune`, although both contain `Dune` and `Frank Herbert`.
3. A structured filename must yield an exact title and author as distinct
   fields. When it starts with the target's known series label and ordinal,
   such as `Dune 01 Dune - Frank Herbert`, require the ordinal to equal the
   target's persisted `series_position` and then compare the remaining title.
   Series position corroborates an exact title-author match; it never selects
   a file on its own because collection numbering is uploader convention.
4. Reject a file only when its ISBN has been provider-resolved to a different
   canonical work, or when its complete parsed title/author contradicts the
   target.
5. Title-only names, series/folder names, token overlap, and fuzzy matching
   never select a file.

Comparison needs Unicode case-folding and normalized whitespace/punctuation,
but no fuzzy matching. A no-match completes no transfer and reports that the
torrent was retained for another selection. This favors a false negative over
putting a wrong Book in a user's library.

For this torrent, `Dune 01 Dune - Frank Herbert.epub` and its MOBI equivalent
qualify for *Dune*; `Dune 02 Dune Messiah - Frank Herbert.epub` does not.
Selecting the same source release later from the *Dune Messiah* Book should
evaluate the retained torrent again and import only its Messiah files.

## Metadata Extraction

Implement EPUB first without a new runtime dependency. EPUB is a ZIP archive:
read only `META-INF/container.xml` and its declared OPF package using
`zipfile` and the existing `defusedxml` dependency. Extract `dc:title`,
`dc:creator`, and typed identifiers. EPUB's required identifier is not
necessarily an ISBN, so accept ISBNs only from typed ISBN identifiers,
`urn:isbn:` values, or clearly marked legacy OPF identifiers. [Source: EPUB
3.3, <https://www.w3.org/TR/epub-33/#sec-opf-dcmes-required-hd>]

Normalize ISBN punctuation, validate ISBN-10 and ISBN-13 checksums, and
convert a valid ISBN-10 to canonical ISBN-13 for lookup. ISBN denotes an
edition/product, not automatically the provider's work identity: distinct
ebook formats and DRM variants can have separate ISBNs, and Kindle editions
need not have one. Resolve an extracted ISBN through the provider before using
it as a positive or negative work-match signal; retain the provider-backed
Book as canonical. [Source: International ISBN Agency,
<https://www.isbn-international.org/content/what-isbn/10>]

Use strict filename evidence for MOBI/AZW in the first delivery. Python has no
stdlib reader for their metadata. A later optional bounded parser can inspect
MOBI PalmDB record zero and EXTH title/creator/ISBN records, but must validate
all offsets and reject encrypted/malformed files. Do not add `EbookLib` or the
`mobi` package for this work: their AGPL/GPL licenses respectively do not fit
an unexamined production dependency choice. [Source: EbookLib metadata,
<https://pypi.org/pypi/ebooklib/json>; mobi metadata,
<https://pypi.org/pypi/mobi/json>]

Downloaded torrents are untrusted. EPUB parsing must inspect ZIP metadata
before reads and cap outer size, entry count, compressed/uncompressed sizes,
compression ratio, and XML bytes. Do not extract archives. A later MOBI parser
should run with bounded input, timeout, resource limits, no network, and
read-only input. [Source: Python `zipfile` decompression pitfalls,
<https://docs.python.org/3/library/zipfile.html#decompression-pitfalls>; Python
XML security, <https://docs.python.org/3/library/xml.html#xml-vulnerabilities>]

## Concrete Work Plan

1. Port the upstream Prowlarr qualified-identity change separately, adapting
   it to this fork's current qBittorrent expected-info-hash reuse. Cover
   distinct indexer entries, cache refresh, and duplicate-collapse behavior.

2. Add a pure ISBN utility and pure EPUB evidence extractor with synthetic
   fixtures. Cover OPF2/OPF3 typed identifiers, ISBN-10 conversion, invalid
   checksums, malformed XML, duplicate ZIP members, and every resource limit.

3. Add `ImportTarget`, populated server-side from the stored Book, and the
   `book_selection` module. Wire it after `collect_staged_files()` and before
   `transfer_book_files()`. Initially activate it for library torrent tasks
   only. Cover matching EPUB, strict filename fallback, conflicting ISBN,
   corrupt EPUB, title-only rejection, and mixed Dune collections.

4. Separate physical source-release identity from Book-specific import
   activity. Preserve `source_release_id` for Prowlarr cache/retry and client
   lookup, while making the activity/history `task_id` Book-scoped. Add a
   nullable `source_release_id` history column and backfill it from legacy
   `task_id`. Persist the immutable target and compact selection result in the
   retry payload. This prevents selecting the same torrent for Messiah from
   colliding with the completed Dune activity.

5. Make an empty selection terminally fail with a precise message and zero
   File rows, while leaving torrent data intact. Report selected-file count on
   success. A later release selection can reuse the existing complete torrent
   by info hash and rerun selection.

6. Add integration coverage for: a 38-file collection importing only Dune;
   the same completed torrent later importing only Messiah; multiple valid
   Dune formats; no match preserving the torrent and pending requests; retry
   serialization; migration/backfill; and unchanged behavior for direct and
   non-library multi-file downloads.

7. After EPUB selection proves reliable, decide whether to add bounded native
   MOBI/AZW EXTH extraction. It should be an enhancement, not a prerequisite;
   strict filename matching keeps the first release safe.

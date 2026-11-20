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

## Audiobookshelf

Shelfmark asks the Audiobookshelf server over its REST API for the title, author, ISBN and
ASIN of every item, and nothing is written back.

1. In **Settings, General**, turn on **Mark audiobooks already in your Audiobookshelf
   library**.
2. Set **Audiobookshelf URL** to an address the Shelfmark container can reach (often the
   host's LAN IP, not the public URL), for example `http://10.0.0.91:13378`.
3. Paste an **Audiobookshelf API Token** (Audiobookshelf **Settings, Users, your user, API
   Token**). Read access is enough.
4. Leave **Audiobookshelf Library IDs** blank to check every book library, or list the IDs
   to check (comma separated).
5. Press **Test Audiobookshelf library**. It reports how many items it indexed, using the
   values on the form, so it works before you save.

Audiobookshelf titles are often raw folder names such as `Alex Cross 02: Kiss the Girls`.
When the server reports a series for the item, the leading label and number are read as the
series, not as a different book, so a search for `Kiss the Girls` still matches.

## How a match is decided

In order of confidence:

1. An external id the metadata provider and the library agree on.
2. An ISBN, compared in both ISBN-10 and ISBN-13 form.
3. Fuzzy title tokens plus the author surname, the same rule the rest of the app uses for
   book matching.

## Behaviour worth knowing

Sequels and other near-miss titles fail the title rule; the same title by a different author fails the surname rule. A series label in front of a library title ("Alex Cross 02: Kiss the Girls", "HHG 5: Mostly Harmless") is read as the series, not as part of the title, when the entry is in a series, so renaming a shelf to show reading order doesn't make its books look unowned. A shelf title whose bracketed words name another edition ("Full Cast Edition", "Dramatized Adaptation") is not the book either: owning the adaptation is not owning the recording asked for. Printing words ("Illustrated Edition", "Unabridged") change nothing, and in a Calibre title a numbered parenthetical ("(Alex Cross Series #11)") is read as the series, not as an edition.

- **It fails open for badges.** If a library cannot be read, Shelfmark logs a warning, reuses
  the last successful read if it has one, and otherwise treats the book as not owned. A
  broken path degrades the badge, it never blocks a search.
- **It fails closed for anything that acts on the answer.** The Hardcover sync and automatic
  downloads would otherwise request or download books the library already holds, so when an
  enabled library cannot be read and has not been read before, the sync cycle is skipped and
  the request stays pending until the next one. A library that listed items a moment ago and
  now lists none is treated as unreadable too, not as an emptied shelf.
- **Results are cached** for ten minutes, and refreshed early when the database file
  changes, so a large library costs one read rather than one per search.
- **Each library answers for its own format.** Calibre holds ebooks and Audiobookshelf holds
  audiobooks, so an ebook result is only checked against Calibre and an audiobook against
  Audiobookshelf. The provider interface in `shelfmark/core/library_providers/` takes more libraries: add a
  module with the `LibraryProvider` shape and list it in `all_providers()`. Nothing above
  that function knows which libraries exist.

## Cross-Format Markers

With both library checks on, Shelfmark can mark the works you hold in both formats, so a reader in Audiobookshelf sees that the ebook is in Calibre, and a reader in Calibre-Web sees that the audiobook is in Audiobookshelf.

A Calibre book and an Audiobookshelf item are a **pair** when either one, asked for by its own title, author and identifiers (ISBN, ASIN), is found in the other library by the matching rules above. The same exceptions apply: a series label or numbered parenthetical in front of a title is the series, not another work; a different edition ("Full Cast Edition", "Dramatized Adaptation") is a different recording and not a pair; an omnibus contains the work but is not its copy; a sequel whose title extends the other ("Dune", "Dune Messiah") is a different book. Two editions on one side both pair with the one copy on the other.

### Audiobookshelf tags

Shelfmark writes one tag (`ebook-in-library` by default) to every paired audiobook and removes it from any audiobook that has lost its pair. No other tag is touched: the item's current tag list is read back from Audiobookshelf just before each write, so a tag you added a minute ago is kept, and running the sync twice changes nothing the second time.

| Setting | Description | Default |
|---------|-------------|---------|
| Tag audiobooks whose ebook is in Calibre | Write the tag at the end of every sync cycle | Off |
| Audiobookshelf tag | The tag to write | `ebook-in-library` |

**Preview Audiobookshelf tags** is a dry run: it counts the pairs and lists what would be added and removed, without writing. It works with the toggle off and uses the form's unsaved values. **Apply Audiobookshelf tags now** writes the changes with the saved settings and needs the toggle on.

With the toggle on, the tags are also refreshed at the end of every [Hardcover sync](hardcover-sync.md) cycle. The cycle's log reports the counts, and a failure in this pass is logged and counted but never stops the cycle.

The token's user needs Audiobookshelf's **Can update** permission. Without it Audiobookshelf answers 403 (`PATCH /api/items/<id>/media`); Shelfmark stops at the first refusal, logs it, and says so in the settings button's message and the endpoint's answer. Each write sends the item's whole tag list, as the Audiobookshelf API requires, which is why the fresh read comes first.

Environment variables: `CROSS_FORMAT_ABS_TAGGING_ENABLED`, `CROSS_FORMAT_ABS_TAG`.

### Endpoints

Both are admin endpoints, so an [API key](api-access.md) can call them from a script.

`GET /api/admin/library/cross-format` returns the pairs, flat and indexed from both sides:

```json
{
  "ebooks_indexed": 412,
  "audiobooks_indexed": 97,
  "pair_count": 1,
  "pairs": [
    {"ebook": {"id": "118", "title": "Dune", "author": "Frank Herbert"},
     "audiobook": {"id": "li_8gch9ve09orgn4fdz8", "title": "Dune", "author": "Frank Herbert"}}
  ],
  "by_calibre_id": {"118": [{"id": "li_8gch9ve09orgn4fdz8", "title": "Dune", "author": "Frank Herbert"}]},
  "by_audiobookshelf_id": {"li_8gch9ve09orgn4fdz8": [{"id": "118", "title": "Dune", "author": "Frank Herbert"}]}
}
```

`POST /api/admin/library/cross-format/audiobookshelf-tags` with body `{"dry_run": true}` (the default when the body is empty) reports; `{"dry_run": false}` writes. The answer lists the `added` and `removed` items with `already_current`, `errors` and `forbidden` counts, plus a one-line `message`. Status codes: 400 when either library check is off, 409 when writing with the toggle off, 503 when a library cannot be read, 502 when Audiobookshelf refused the write.

Both hold rather than guess when a library cannot be read: an empty index would look like "no pairs" and remove every tag.

### Marking the Calibre side

Shelfmark only reads `metadata.db`, so the Calibre side is marked outside Shelfmark by a script that reads `by_calibre_id` from the endpoint and runs `calibredb` wherever Calibre's tools and the library are (a Calibre-Web container that ships them, or a host with calibre installed). A yes/no custom column is the natural marker. Create it once:

```bash
calibredb add_custom_column --library-path /calibre-library audiobook "Audiobook in library" bool
```

Then set it on each Calibre id the endpoint lists and clear it on books that are no longer listed:

```bash
calibredb set_custom --library-path /calibre-library audiobook 118 true
calibredb set_custom --library-path /calibre-library audiobook 118 ""
```

`calibredb list --library-path /calibre-library --fields id,#audiobook` shows what is set, so the script can change only what differs. Do not run `calibredb` against a library that the calibre desktop application has open; Calibre-Web and Calibre-Web Automated read the database and may need their library refresh to show the column.

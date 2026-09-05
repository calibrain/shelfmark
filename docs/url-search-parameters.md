# URL Search Parameters

You can trigger searches directly via URL. This enables bookmarking searches and sharing links.

Parameters live in the URL **hash** (`#…`), so they stay in the browser and are never sent to
the server. Shelfmark also keeps the hash in sync as you search, so the address bar always
holds a shareable link to what you're looking at.

## Basic Usage

```
http://your-server:8084/#q=harry+potter
```

Older query-string links (`/?q=harry+potter`) still work: they're read once on load and
rewritten to the hash form.

## Supported Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `q` or `query` | Main search query | `/#q=dune` |
| `author` | Filter by author name | `/#author=frank+herbert` |
| `title` | Filter by book title | `/#title=foundation` |
| `isbn` | Filter by ISBN | `/#isbn=978-0747532699` |
| `lang` | Filter by language (ISO 639-1 code) | `/#lang=en` |
| `format` | Filter by file format | `/#format=epub` |
| `content` | Filter by content type | `/#content=fiction` |
| `content_type` | Select media type (`ebook`, `audiobook`, or `combined`) in Universal mode only | `/#q=dune&content_type=audiobook` |
| `sort` | Sort order for results | `/#sort=newest` |
| `search_by` | "Search By" target the query applies to (`general`, `author`, `title`, `isbn`, a metadata provider field like `series`, or `manual`) | `/#search_by=author&q=frank+herbert` |

## Multiple Values

Some parameters support multiple values by repeating the parameter:

```
/#lang=en&lang=de&lang=fr
/#format=epub&format=mobi&format=azw3
```

## Examples

**Simple search:**
```
/#q=lord+of+the+rings
```

**Search with author filter:**
```
/#q=dune&author=frank+herbert
```

**Search with format and language:**
```
/#q=harry+potter&format=epub&lang=en
```

**Author search with multiple formats:**
```
/#author=stephen+king&format=epub&format=mobi
```

**Search with sort order:**
```
/#q=science+fiction&sort=newest
```

**Universal search as audiobook:**
```
/#q=dune&content_type=audiobook
```

**Universal search forcing combined (ebook + audiobook):**
```
/#q=dune&content_type=combined
```

## Search Mode Behavior

### Direct Mode

When Search Mode is set to Direct, all parameters are used to filter results from the configured direct source.
`content_type` is ignored in Direct mode.

### Universal Mode

`q`, `search_by`, `sort`, and `content_type` are used. Other parameters (author, title, format, etc.) are silently ignored since metadata providers have their own search capabilities — except when `search_by` names one of the provider's own search fields, in which case `q` is sent as that field's value.

`content_type=combined` forces combined mode (search ebook and audiobook providers together), overriding the last-used preference. It is silently ignored if combined mode is unavailable (e.g. the combined selector is disabled in settings, or either content type is blocked by request policy).

## Search By

`search_by` picks which target the `q` value is applied to, matching the selector next to the
search box. It can be deep-linked on its own (`/#search_by=manual`) to open the app in that
mode with an empty query.

`search_by=manual` fills the search box but does not auto-run: manual search opens the release
browser from an explicit submit.

A `search_by` naming a target that isn't available (wrong search mode, or a metadata provider
that doesn't offer that field) is ignored, and the query falls back to a general search.

## Notes

- URL parameters are read once on page load, and again if the hash is replaced in an open tab
  (e.g. pasting a shared link into the address bar)
- The hash is kept in sync with the search box, Search By target and filters as you search
- Spaces should be encoded as `+` or `%20`
- Invalid or unknown parameters are silently ignored
- Your last-used Search By target is remembered in browser storage and used when a link
  doesn't specify one

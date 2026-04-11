# CWA sidecar manifest

When Shelfmark delivers a downloaded book into the folder output destination, it can also emit:

- `<delivered filename>.cwa.json`

This sidecar exists for the Calibre-Web-Automated ingest pipeline. CWA reads the sidecar, persists exact identifiers into Calibre, and can then use exact Hardcover lookup instead of fuzzy title/author guessing.

## When it is written

Shelfmark writes the sidecar from the folder output handler after the delivered file paths are known.

The sidecar is only written when Shelfmark has exact trusted Hardcover provenance from the original request/book metadata.

## Manifest shape

```json
{
  "provenance": {
    "provider": "hardcover",
    "provider_id": "379631",
    "hardcover_edition": "91234",
    "hardcover_slug": "mort"
  },
  "identifiers": {
    "hardcover-id": "379631",
    "hardcover-edition": "91234",
    "hardcover-slug": "mort"
  }
}
```

Only trustworthy fields are emitted. If exact Hardcover provenance is unavailable, no `.cwa.json` file is written.

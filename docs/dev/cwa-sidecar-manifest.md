# CWA sidecar manifest

When Shelfmark delivers a downloaded book into the folder output destination, it can also emit:

- `<delivered filename>.cwa.json`

This sidecar exists for the Calibre-Web-Automated ingest pipeline. CWA reads the sidecar, persists exact identifiers into Calibre, and can then use exact Hardcover lookup instead of fuzzy title/author guessing.

## When it is written

Sidecar emission is optional and disabled by default.

Enable it with:

- Settings -> Downloads -> `Emit CWA Sidecar Manifest`
- Environment variable: `ENABLE_CWA_SIDECAR_MANIFEST=true`

Shelfmark writes the sidecar from the folder output handler after the delivered file paths are known.

This behavior is additive only. Ordinary file delivery is unchanged when the setting is disabled.

When enabled, the sidecar is only written if Shelfmark also has exact trusted Hardcover provenance from the original request/book metadata. If exact provenance is unavailable, no `.cwa.json` file is written.

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

Only trustworthy fields are emitted.

## When to enable it

Enable this only if your downstream ingest workflow expects Shelfmark to hand off exact Hardcover provenance to Calibre-Web-Automated alongside the delivered file.

Example emitted files:

- `Mort (1987).epub`
- `Mort (1987).epub.cwa.json`

## Boundary

Shelfmark's responsibility here is narrow:

- when enabled, emit an additive sidecar with exact trusted Hardcover provenance
- leave ordinary Shelfmark delivery unchanged when disabled or when exact provenance is unavailable
- hand off the delivered file plus sidecar for downstream CWA ingest

This feature does not add:

- auto-download policy changes
- release-selection UI changes
- source/provider preference UI changes
- CWA-side request impersonation or request attribution changes

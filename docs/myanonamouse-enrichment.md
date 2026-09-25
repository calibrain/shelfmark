# MyAnonamouse Enrichment

Prowlarr's MyAnonamouse indexer keeps the title, author, language and file type of each result but drops the narrator and series, and Torznab has no field to carry them. With a MyAnonamouse session ID, Shelfmark looks those details up directly from MyAnonamouse and adds them to the Prowlarr release list.

## What you get

| Column | Shown for | Source |
|--------|-----------|--------|
| Series | Books and audiobooks | MAM series info, e.g. `The Sun Eater #1` |
| Narrator | Audiobooks | MAM narrator info |
| Bitrate | Audiobooks | Parsed from the uploader's tags (e.g. `64 kbps`), so some releases have none |

Only MyAnonamouse results are enriched. Other Prowlarr indexers fill the bitrate column only if they report a Torznab `bitrate` attribute, which most don't. AudiobookBay's bitrate column is unaffected and follows the same toggle.

## Setup

1. On MyAnonamouse, open **Preferences > Security** and create a session for Shelfmark. Lock it to the public IP (or ASN) Shelfmark connects from.
2. In Shelfmark, open **Settings > Prowlarr > MyAnonamouse Enrichment**, paste the `mam_id` value into **MAM Session ID** (or set `PROWLARR_MAM_ID`), and click **Test MAM Session**.
3. Choose which columns appear under **Settings > Search Mode > Release List Columns** (`SHOW_SERIES_COLUMN`, `SHOW_NARRATOR_COLUMN`, `SHOW_BITRATE_COLUMN`). Users can override these for their own account.

## Use a session made for Shelfmark

MyAnonamouse locks each session to one IP or ASN. Reusing the session Prowlarr (or a seedbox script) uses often fails with **403**:

- If Shelfmark reaches MAM from a different IP than that session was created for (another host, a VPN container, or a proxy set under **Settings > Network**), MAM rejects it.
- If the existing session is ASN-locked to another network, it will not work from Shelfmark's network either.

In either case, create a separate session for Shelfmark. To see the IP Shelfmark connects from:

```bash
docker exec shelfmark curl -s https://api.ipify.org
```

Compare it with the IP listed next to the session on MAM's security page.

## How it works

After a Prowlarr search, Shelfmark sends the same query text to MAM's JSON search API (normally one request) and matches torrents back to Prowlarr's results by their MAM torrent ID. Lookups are cached for an hour, stay inside the Prowlarr search time budget, and never fail the search: if MAM errors or rejects the session, the release list simply shows without the extra columns filled in.

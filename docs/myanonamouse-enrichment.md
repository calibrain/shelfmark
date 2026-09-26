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

After a Prowlarr search, Shelfmark reruns the search Prowlarr sent to MyAnonamouse's JSON search API: the same cleaned-up query text, the same categories (audiobooks, or e-books), and your MyAnonamouse indexer's own options from Prowlarr (search type, search in description/series/filenames, languages). That normally returns the same torrents in one request per title. Shelfmark then matches them back to Prowlarr's results by their MAM torrent ID. Only results from the MyAnonamouse indexer are looked up, and the session ID is only ever sent to `https://www.myanonamouse.net`.

If some torrents are missing from the first page, Shelfmark reads further pages, up to 4 requests per search. Lookups are cached for an hour, stay inside the Prowlarr search time budget, and never fail the search: if MAM errors or rejects the session, the release list simply shows without the extra columns filled in.

After a failed request (a 403, a timeout, an unexpected reply), Shelfmark waits before trying MAM again: 1 minute, then 2, 4 and so on, up to 30 minutes. After 10 failures in a row it stops using MAM. **Test MAM Session** (once it succeeds), a new session ID, or a restart turns it back on. Each failure is logged with the reason.

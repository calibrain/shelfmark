# Environment Variables

This document lists all configuration options that can be set via environment variables.

> **Auto-generated** - Do not edit manually. Run `python scripts/generate_env_docs.py` to regenerate.

## Table of Contents

- [Bootstrap Configuration](#bootstrap-configuration)
- [General](#general)
- [Search Mode](#search-mode)
- [Downloads](#downloads)
- [Network](#network)
- [Advanced](#advanced)
- [IRC](#irc)
- [Metadata Providers](#metadata-providers)
  - [Hardcover](#metadata-providers-hardcover)
  - [Open Library](#metadata-providers-open-library)
  - [Google Books](#metadata-providers-google-books)
- [Direct Download](#direct-download)
  - [Download Sources](#direct-download-download-sources)
  - [Cloudflare Bypass](#direct-download-cloudflare-bypass)
  - [Mirrors](#direct-download-mirrors)
- [Prowlarr](#prowlarr)
  - [Configuration](#prowlarr-configuration)
  - [Download Clients](#prowlarr-download-clients)

---

## Bootstrap Configuration

These environment variables are used at startup before the settings system loads. They typically configure paths and server settings.

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `CONFIG_DIR` | Directory for storing configuration files and plugin settings. | string (path) | `/config` |
| `LOG_ROOT` | Root directory for log files. | string (path) | `/var/log/` |
| `TMP_DIR` | Staging directory for downloads before moving to destination. | string (path) | `/tmp/shelfmark` |
| `ENABLE_LOGGING` | Enable file logging under LOG_ROOT/shelfmark/ (including shelfmark.log and startup logs). | boolean | `true` |
| `FLASK_HOST` | Host address for the Flask web server. | string | `0.0.0.0` |
| `FLASK_PORT` | Port number for the Flask web server. | number | `8084` |
| `SESSION_COOKIE_SECURE` | Enable secure cookies (requires HTTPS). | boolean | `false` |
| `CWA_DB_PATH` | Path to the Calibre-Web database for authentication integration. | string (path) | `/auth/app.db` |
| `DOCKERMODE` | Indicates the application is running inside a Docker container. | boolean | `false` |
| `ONBOARDING` | Show the onboarding wizard on first run. Set to false to skip (useful for ephemeral storage). | boolean | `true` |

<details>
<summary>Detailed descriptions</summary>

#### `CONFIG_DIR`

Directory for storing configuration files and plugin settings.

- **Type:** string (path)
- **Default:** `/config`

#### `LOG_ROOT`

Root directory for log files.

- **Type:** string (path)
- **Default:** `/var/log/`

#### `TMP_DIR`

Staging directory for downloads before moving to destination.

- **Type:** string (path)
- **Default:** `/tmp/shelfmark`

#### `ENABLE_LOGGING`

Enable file logging under LOG_ROOT/shelfmark/ (including shelfmark.log and startup logs).

- **Type:** boolean
- **Default:** `true`

#### `FLASK_HOST`

Host address for the Flask web server.

- **Type:** string
- **Default:** `0.0.0.0`

#### `FLASK_PORT`

Port number for the Flask web server.

- **Type:** number
- **Default:** `8084`

#### `SESSION_COOKIE_SECURE`

Enable secure cookies (requires HTTPS).

- **Type:** boolean
- **Default:** `false`

#### `CWA_DB_PATH`

Path to the Calibre-Web database for authentication integration.

- **Type:** string (path)
- **Default:** `/auth/app.db`

#### `DOCKERMODE`

Indicates the application is running inside a Docker container.

- **Type:** boolean
- **Default:** `false`

#### `ONBOARDING`

Show the onboarding wizard on first run. Set to false to skip (useful for ephemeral storage).

- **Type:** boolean
- **Default:** `true`

</details>

## General

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `CALIBRE_WEB_URL` | Adds a navigation button to your book library (Calibre-Web Automated, Booklore, etc). | string | _none_ |
| `AUDIOBOOK_LIBRARY_URL` | Adds a separate navigation button for your audiobook library (Audiobookshelf, Plex, etc). When both URLs are set, icons are shown instead of text. | string | _none_ |
| `SUPPORTED_FORMATS` | Book formats to include in search results. ZIP/RAR archives are extracted automatically and book files are used if found. | string (comma-separated) | `epub,mobi,azw3,fb2,djvu,cbz,cbr` |
| `SUPPORTED_AUDIOBOOK_FORMATS` | Audiobook formats to include in search results. ZIP/RAR archives are extracted automatically and audiobook files are used if found. | string (comma-separated) | `m4b,mp3` |
| `BOOK_LANGUAGE` | Default language filter for searches. | string (comma-separated) | `en` |

<details>
<summary>Detailed descriptions</summary>

#### `CALIBRE_WEB_URL`

**Library URL**

Adds a navigation button to your book library (Calibre-Web Automated, Booklore, etc).

- **Type:** string
- **Default:** _none_

#### `AUDIOBOOK_LIBRARY_URL`

**Audiobook Library URL**

Adds a separate navigation button for your audiobook library (Audiobookshelf, Plex, etc). When both URLs are set, icons are shown instead of text.

- **Type:** string
- **Default:** _none_

#### `SUPPORTED_FORMATS`

**Supported Book Formats**

Book formats to include in search results. ZIP/RAR archives are extracted automatically and book files are used if found.

- **Type:** string (comma-separated)
- **Default:** `epub,mobi,azw3,fb2,djvu,cbz,cbr`

#### `SUPPORTED_AUDIOBOOK_FORMATS`

**Supported Audiobook Formats**

Audiobook formats to include in search results. ZIP/RAR archives are extracted automatically and audiobook files are used if found.

- **Type:** string (comma-separated)
- **Default:** `m4b,mp3`

#### `BOOK_LANGUAGE`

**Default Book Languages**

Default language filter for searches.

- **Type:** string (comma-separated)
- **Default:** `en`

</details>

## Search Mode

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `SEARCH_MODE` | How you want to search for and download books. | string (choice) | `direct` |
| `AA_DEFAULT_SORT` | Default sort order for search results. | string (choice) | `relevance` |
| `METADATA_PROVIDER` | Choose which metadata provider to use for book searches. | string (choice) | `openlibrary` |
| `METADATA_PROVIDER_AUDIOBOOK` | Metadata provider for audiobook searches. Uses the book provider if not set. | string (choice) | _empty string_ |
| `DEFAULT_RELEASE_SOURCE` | The release source tab to open by default in the release modal. | string (choice) | `direct_download` |

<details>
<summary>Detailed descriptions</summary>

#### `SEARCH_MODE`

**Search Mode**

How you want to search for and download books.

- **Type:** string (choice)
- **Default:** `direct`
- **Options:** `direct` (Direct), `universal` (Universal)

#### `AA_DEFAULT_SORT`

**Default Sort Order**

Default sort order for search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `newest` (Newest (publication year)), `oldest` (Oldest (publication year)), `largest` (Largest (filesize)), `smallest` (Smallest (filesize)), `newest_added` (Newest (open sourced)), `oldest_added` (Oldest (open sourced))

#### `METADATA_PROVIDER`

**Book Metadata Provider**

Choose which metadata provider to use for book searches.

- **Type:** string (choice)
- **Default:** `openlibrary`
- **Options:** `""` (No providers enabled)

#### `METADATA_PROVIDER_AUDIOBOOK`

**Audiobook Metadata Provider**

Metadata provider for audiobook searches. Uses the book provider if not set.

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (Use book provider), `""` (No providers enabled)

#### `DEFAULT_RELEASE_SOURCE`

**Default Release Source**

The release source tab to open by default in the release modal.

- **Type:** string (choice)
- **Default:** `direct_download`
- **Options:** `direct_download` (Direct Download), `prowlarr` (Prowlarr)

</details>

## Downloads

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `BOOKS_OUTPUT_MODE` | Choose where completed book files are sent. | string (choice) | `folder` |
| `INGEST_DIR` | Directory where downloaded files are saved. | string | `/books` |
| `FILE_ORGANIZATION` | Choose how downloaded book files are named and organized. | string (choice) | `rename` |
| `TEMPLATE_RENAME` | Variables: {Author}, {Title}, {Year}. Universal adds: {Series}, {SeriesPosition}, {Subtitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders. | string | `{Author} - {Title} ({Year})` |
| `TEMPLATE_ORGANIZE` | Use / to create folders. Variables: {Author}, {Title}, {Year}. Universal adds: {Series}, {SeriesPosition}, {Subtitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. | string | `{Author}/{Title} ({Year})` |
| `HARDLINK_TORRENTS` | Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder. | boolean | `false` |
| `BOOKLORE_HOST` | Base URL of your Booklore instance | string | _none_ |
| `BOOKLORE_USERNAME` | Booklore account username | string | _none_ |
| `BOOKLORE_PASSWORD` | Booklore account password | string (secret) | _none_ |
| `BOOKLORE_LIBRARY_ID` | Booklore library to upload into. | string (choice) | _none_ |
| `BOOKLORE_PATH_ID` | Booklore library path for uploads. | string (choice) | _none_ |
| `DESTINATION_AUDIOBOOK` | Leave empty to use Books destination. | string | _none_ |
| `FILE_ORGANIZATION_AUDIOBOOK` | Choose how downloaded audiobook files are named and organized. | string (choice) | `rename` |
| `TEMPLATE_AUDIOBOOK_RENAME` | Variables: {Author}, {Title}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders. | string | `{Author} - {Title}` |
| `TEMPLATE_AUDIOBOOK_ORGANIZE` | Use / to create folders. Variables: {Author}, {Title}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. | string | `{Author}/{Title}` |
| `HARDLINK_TORRENTS_AUDIOBOOK` | Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder. | boolean | `true` |
| `AUTO_OPEN_DOWNLOADS_SIDEBAR` | Automatically open the downloads sidebar when a new download is queued. | boolean | `false` |
| `DOWNLOAD_TO_BROWSER` | Automatically download completed files to your browser. | boolean | `false` |
| `MAX_CONCURRENT_DOWNLOADS` | Maximum number of simultaneous downloads. | number | `3` |
| `STATUS_TIMEOUT` | How long to keep completed/failed downloads in the queue display. | number | `3600` |

<details>
<summary>Detailed descriptions</summary>

#### `BOOKS_OUTPUT_MODE`

**Output Mode**

Choose where completed book files are sent.

- **Type:** string (choice)
- **Default:** `folder`
- **Options:** `folder` (Folder), `booklore` (Booklore (API))

#### `INGEST_DIR`

**Destination**

Directory where downloaded files are saved.

- **Type:** string
- **Default:** `/books`
- **Required:** Yes

#### `FILE_ORGANIZATION`

**File Organization**

Choose how downloaded book files are named and organized. 

- **Type:** string (choice)
- **Default:** `rename`
- **Options:** `none` (None), `rename` (Rename Only), `organize` (Rename and Organize)

#### `TEMPLATE_RENAME`

**Naming Template**

Variables: {Author}, {Title}, {Year}. Universal adds: {Series}, {SeriesPosition}, {Subtitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders.

- **Type:** string
- **Default:** `{Author} - {Title} ({Year})`

#### `TEMPLATE_ORGANIZE`

**Path Template**

Use / to create folders. Variables: {Author}, {Title}, {Year}. Universal adds: {Series}, {SeriesPosition}, {Subtitle}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty.

- **Type:** string
- **Default:** `{Author}/{Title} ({Year})`

#### `HARDLINK_TORRENTS`

**Hardlink Book Torrents**

Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.

- **Type:** boolean
- **Default:** `false`

#### `BOOKLORE_HOST`

**Booklore URL**

Base URL of your Booklore instance

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_USERNAME`

**Username**

Booklore account username

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_PASSWORD`

**Password**

Booklore account password

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_LIBRARY_ID`

**Library**

Booklore library to upload into.

- **Type:** string (choice)
- **Default:** _none_
- **Required:** Yes

#### `BOOKLORE_PATH_ID`

**Path**

Booklore library path for uploads.

- **Type:** string (choice)
- **Default:** _none_
- **Required:** Yes

#### `DESTINATION_AUDIOBOOK`

**Destination**

Leave empty to use Books destination.

- **Type:** string
- **Default:** _none_

#### `FILE_ORGANIZATION_AUDIOBOOK`

**File Organization**

Choose how downloaded audiobook files are named and organized.

- **Type:** string (choice)
- **Default:** `rename`
- **Options:** `none` (None), `rename` (Rename Only), `organize` (Rename and Organize)

#### `TEMPLATE_AUDIOBOOK_RENAME`

**Naming Template**

Variables: {Author}, {Title}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty. Rename templates are filename-only (no '/' or '\'); use Organize for folders.

- **Type:** string
- **Default:** `{Author} - {Title}`

#### `TEMPLATE_AUDIOBOOK_ORGANIZE`

**Path Template**

Use / to create folders. Variables: {Author}, {Title}, {Year}, {Series}, {SeriesPosition}, {Subtitle}, {PartNumber}. Use arbitrary prefix/suffix: {Vol. SeriesPosition - } outputs 'Vol. 2 - ' when set, nothing when empty.

- **Type:** string
- **Default:** `{Author}/{Title}`

#### `HARDLINK_TORRENTS_AUDIOBOOK`

**Hardlink Audiobook Torrents**

Create hardlinks instead of copying. Preserves seeding but archives won't be extracted. Don't use if destination is a library ingest folder.

- **Type:** boolean
- **Default:** `true`

#### `AUTO_OPEN_DOWNLOADS_SIDEBAR`

**Auto-Open Downloads Sidebar**

Automatically open the downloads sidebar when a new download is queued.

- **Type:** boolean
- **Default:** `false`

#### `DOWNLOAD_TO_BROWSER`

**Download to Browser**

Automatically download completed files to your browser.

- **Type:** boolean
- **Default:** `false`

#### `MAX_CONCURRENT_DOWNLOADS`

**Max Concurrent Downloads**

Maximum number of simultaneous downloads.

- **Type:** number
- **Default:** `3`
- **Requires restart:** Yes
- **Constraints:** min: 1, max: 10

#### `STATUS_TIMEOUT`

**Status Timeout (seconds)**

How long to keep completed/failed downloads in the queue display.

- **Type:** number
- **Default:** `3600`
- **Constraints:** min: 60, max: 86400

</details>

## Network

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `CUSTOM_DNS` | DNS provider for domain resolution. 'Auto' rotates through providers on failure. | string (choice) | `auto` |
| `CUSTOM_DNS_MANUAL` | Comma-separated list of DNS server IP addresses (e.g., 8.8.8.8, 1.1.1.1). | string | _none_ |
| `USE_DOH` | Use encrypted DNS queries for improved reliability and privacy. | boolean | `true` |
| `USING_TOR` | Route all traffic through Tor for enhanced privacy. | boolean | `false` |
| `PROXY_MODE` | Choose proxy type. SOCKS5 handles all traffic through a single proxy. | string (choice) | `none` |
| `HTTP_PROXY` | HTTP proxy URL (e.g., http://proxy:8080) | string | _none_ |
| `HTTPS_PROXY` | HTTPS proxy URL (leave empty to use HTTP proxy for HTTPS) | string | _none_ |
| `SOCKS5_PROXY` | SOCKS5 proxy URL. Supports auth: socks5://user:pass@host:port | string | _none_ |
| `NO_PROXY` | Comma-separated hosts to bypass proxy (e.g., localhost,127.0.0.1,10.*,*.local) | string | _none_ |

<details>
<summary>Detailed descriptions</summary>

#### `CUSTOM_DNS`

**DNS Provider**

DNS provider for domain resolution. 'Auto' rotates through providers on failure.

- **Type:** string (choice)
- **Default:** `auto`
- **Options:** `auto` (Auto (Recommended)), `system` (System), `google` (Google), `cloudflare` (Cloudflare), `quad9` (Quad9), `opendns` (OpenDNS), `manual` (Manual)

#### `CUSTOM_DNS_MANUAL`

**Manual DNS Servers**

Comma-separated list of DNS server IP addresses (e.g., 8.8.8.8, 1.1.1.1).

- **Type:** string
- **Default:** _none_

#### `USE_DOH`

**Use DNS over HTTPS**

Use encrypted DNS queries for improved reliability and privacy.

- **Type:** boolean
- **Default:** `true`

#### `USING_TOR`

**Tor Routing**

Route all traffic through Tor for enhanced privacy.

- **Type:** boolean
- **Default:** `false`

#### `PROXY_MODE`

**Proxy Mode**

Choose proxy type. SOCKS5 handles all traffic through a single proxy.

- **Type:** string (choice)
- **Default:** `none`
- **Options:** `none` (None (Direct Connection)), `http` (HTTP/HTTPS Proxy), `socks5` (SOCKS5 Proxy)

#### `HTTP_PROXY`

**HTTP Proxy**

HTTP proxy URL (e.g., http://proxy:8080)

- **Type:** string
- **Default:** _none_

#### `HTTPS_PROXY`

**HTTPS Proxy**

HTTPS proxy URL (leave empty to use HTTP proxy for HTTPS)

- **Type:** string
- **Default:** _none_

#### `SOCKS5_PROXY`

**SOCKS5 Proxy**

SOCKS5 proxy URL. Supports auth: socks5://user:pass@host:port

- **Type:** string
- **Default:** _none_

#### `NO_PROXY`

**No Proxy**

Comma-separated hosts to bypass proxy (e.g., localhost,127.0.0.1,10.*,*.local)

- **Type:** string
- **Default:** _none_

</details>

## Advanced

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `URL_BASE` | Optional URL path prefix. Use a path like /shelfmark (no hostname). Leave blank for root. | string | _none_ |
| `DEBUG` | Enable verbose logging to console and file. Not recommended for normal use. | boolean | `false` |
| `MAIN_LOOP_SLEEP_TIME` | How often the download queue is checked for new items. | number | `5` |
| `DOWNLOAD_PROGRESS_UPDATE_INTERVAL` | How often download progress is broadcast to the UI. | number | `1` |
| `CUSTOM_SCRIPT` | Path to a script to run after each successful download. Must be executable. | string | _none_ |
| `CUSTOM_SCRIPT_PATH_MODE` | Pass the path to the custom script as an absolute path or relative to the destination folder. | string (choice) | `absolute` |
| `CUSTOM_SCRIPT_JSON_PAYLOAD` | Send a JSON payload to the custom script via stdin. | boolean | `false` |
| `COVERS_CACHE_ENABLED` | Cache book covers on the server for faster loading. | boolean | `true` |
| `COVERS_CACHE_TTL` | How long to keep cached covers. Set to 0 to keep forever (recommended for static artwork). | number | `0` |
| `COVERS_CACHE_MAX_SIZE_MB` | Maximum disk space for cached covers. Oldest images are removed when limit is reached. | number | `500` |
| `METADATA_CACHE_ENABLED` | When disabled, all metadata searches hit the provider API directly. | boolean | `true` |
| `METADATA_CACHE_SEARCH_TTL` | How long to cache search results. Default: 300 (5 minutes). Max: 604800 (7 days). | number | `300` |
| `METADATA_CACHE_BOOK_TTL` | How long to cache individual book details. Default: 600 (10 minutes). Max: 604800 (7 days). | number | `600` |

<details>
<summary>Detailed descriptions</summary>

#### `URL_BASE`

**Base Path**

Optional URL path prefix. Use a path like /shelfmark (no hostname). Leave blank for root.

- **Type:** string
- **Default:** _none_
- **Requires restart:** Yes

#### `DEBUG`

**Debug Mode**

Enable verbose logging to console and file. Not recommended for normal use.

- **Type:** boolean
- **Default:** `false`
- **Requires restart:** Yes

#### `MAIN_LOOP_SLEEP_TIME`

**Queue Check Interval (seconds)**

How often the download queue is checked for new items.

- **Type:** number
- **Default:** `5`
- **Requires restart:** Yes
- **Constraints:** min: 1, max: 60

#### `DOWNLOAD_PROGRESS_UPDATE_INTERVAL`

**Progress Update Interval (seconds)**

How often download progress is broadcast to the UI.

- **Type:** number
- **Default:** `1`
- **Requires restart:** Yes
- **Constraints:** min: 1, max: 10

#### `CUSTOM_SCRIPT`

**Custom Script Path**

Path to a script to run after each successful download. Must be executable.

See `docs/custom-scripts.md` for the user guide, including how the target path argument (`$1`) and optional JSON payload work.

- **Type:** string
- **Default:** _none_

#### `CUSTOM_SCRIPT_PATH_MODE`

**Custom Script Path Mode**

Pass the path to the custom script as an absolute path or relative to the destination folder.

- **Type:** string (choice)
- **Default:** `absolute`
- **Options:** `absolute` (Absolute), `relative` (Relative)

#### `CUSTOM_SCRIPT_JSON_PAYLOAD`

**Custom Script JSON Payload**

Send a JSON payload to the custom script via stdin (in addition to the target path argument).

See `docs/custom-scripts.md` for an example payload and usage patterns.

- **Type:** boolean
- **Default:** `false`

#### `COVERS_CACHE_ENABLED`

**Enable Cover Cache**

Cache book covers on the server for faster loading.

- **Type:** boolean
- **Default:** `true`

#### `COVERS_CACHE_TTL`

**Cache TTL (days)**

How long to keep cached covers. Set to 0 to keep forever (recommended for static artwork).

- **Type:** number
- **Default:** `0`
- **Constraints:** min: 0, max: 365

#### `COVERS_CACHE_MAX_SIZE_MB`

**Max Cache Size (MB)**

Maximum disk space for cached covers. Oldest images are removed when limit is reached.

- **Type:** number
- **Default:** `500`
- **Constraints:** min: 50, max: 5000

#### `METADATA_CACHE_ENABLED`

**Enable Metadata Caching**

When disabled, all metadata searches hit the provider API directly.

- **Type:** boolean
- **Default:** `true`

#### `METADATA_CACHE_SEARCH_TTL`

**Search Results Cache (seconds)**

How long to cache search results. Default: 300 (5 minutes). Max: 604800 (7 days).

- **Type:** number
- **Default:** `300`
- **Constraints:** min: 60, max: 604800

#### `METADATA_CACHE_BOOK_TTL`

**Book Details Cache (seconds)**

How long to cache individual book details. Default: 600 (10 minutes). Max: 604800 (7 days).

- **Type:** number
- **Default:** `600`
- **Constraints:** min: 60, max: 604800

</details>

## IRC

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `IRC_SERVER` | IRC server hostname | string | _none_ |
| `IRC_PORT` | IRC server port (usually 6697 for TLS, 6667 for plain) | number | `6697` |
| `IRC_USE_TLS` | Enable TLS/SSL encryption for the IRC connection. Disable for servers that don't support TLS. | boolean | `true` |
| `IRC_CHANNEL` | Channel name without the # prefix | string | _none_ |
| `IRC_NICK` | Your IRC nickname (required). Must be unique on the IRC network. | string | _none_ |
| `IRC_SEARCH_BOT` | The search bot to query for results | string | _none_ |
| `IRC_CACHE_TTL` | How long to keep cached search results before they expire. | string (choice) | `2592000` |

<details>
<summary>Detailed descriptions</summary>

#### `IRC_SERVER`

**Server**

IRC server hostname

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_PORT`

**Port**

IRC server port (usually 6697 for TLS, 6667 for plain)

- **Type:** number
- **Default:** `6697`

#### `IRC_USE_TLS`

**Use TLS**

Enable TLS/SSL encryption for the IRC connection. Disable for servers that don't support TLS.

- **Type:** boolean
- **Default:** `true`

#### `IRC_CHANNEL`

**Channel**

Channel name without the # prefix

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_NICK`

**Nickname**

Your IRC nickname (required). Must be unique on the IRC network.

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `IRC_SEARCH_BOT`

**Search bot**

The search bot to query for results

- **Type:** string
- **Default:** _none_

#### `IRC_CACHE_TTL`

**Cache Duration**

How long to keep cached search results before they expire.

- **Type:** string (choice)
- **Default:** `2592000`
- **Options:** `2592000` (30 days), `0` (Forever (until manually cleared))

</details>

## Metadata Providers

### Metadata Providers: Hardcover

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `HARDCOVER_ENABLED` | Enable Hardcover as a metadata provider for book searches | boolean | `false` |
| `HARDCOVER_API_KEY` | Get your API key from hardcover.app/account/api | string (secret) | _none_ |
| `HARDCOVER_DEFAULT_SORT` | Default sort order for Hardcover search results. | string (choice) | `relevance` |
| `HARDCOVER_EXCLUDE_COMPILATIONS` | Filter out compilations, anthologies, and omnibus editions from search results | boolean | `false` |
| `HARDCOVER_EXCLUDE_UNRELEASED` | Filter out books with a release year in the future | boolean | `false` |

<details>
<summary>Detailed descriptions</summary>

#### `HARDCOVER_ENABLED`

**Enable Hardcover**

Enable Hardcover as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

#### `HARDCOVER_API_KEY`

**API Key**

Get your API key from hardcover.app/account/api

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `HARDCOVER_DEFAULT_SORT`

**Default Sort Order**

Default sort order for Hardcover search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `popularity` (Most popular), `rating` (Highest rated), `newest` (Newest), `oldest` (Oldest)

#### `HARDCOVER_EXCLUDE_COMPILATIONS`

**Exclude Compilations**

Filter out compilations, anthologies, and omnibus editions from search results

- **Type:** boolean
- **Default:** `false`

#### `HARDCOVER_EXCLUDE_UNRELEASED`

**Exclude Unreleased Books**

Filter out books with a release year in the future

- **Type:** boolean
- **Default:** `false`

</details>

### Metadata Providers: Open Library

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `OPENLIBRARY_ENABLED` | Enable Open Library as a metadata provider for book searches | boolean | `false` |
| `OPENLIBRARY_DEFAULT_SORT` | Default sort order for Open Library search results. | string (choice) | `relevance` |

<details>
<summary>Detailed descriptions</summary>

#### `OPENLIBRARY_ENABLED`

**Enable Open Library**

Enable Open Library as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

#### `OPENLIBRARY_DEFAULT_SORT`

**Default Sort Order**

Default sort order for Open Library search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `newest` (Newest), `oldest` (Oldest)

</details>

### Metadata Providers: Google Books

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `GOOGLEBOOKS_ENABLED` | Enable Google Books as a metadata provider for book searches | boolean | `false` |
| `GOOGLEBOOKS_API_KEY` | Get your API key from Google Cloud Console (APIs & Services > Credentials) | string (secret) | _none_ |
| `GOOGLEBOOKS_DEFAULT_SORT` | Default sort order for Google Books search results. | string (choice) | `relevance` |

<details>
<summary>Detailed descriptions</summary>

#### `GOOGLEBOOKS_ENABLED`

**Enable Google Books**

Enable Google Books as a metadata provider for book searches

- **Type:** boolean
- **Default:** `false`

#### `GOOGLEBOOKS_API_KEY`

**API Key**

Get your API key from Google Cloud Console (APIs & Services > Credentials)

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `GOOGLEBOOKS_DEFAULT_SORT`

**Default Sort Order**

Default sort order for Google Books search results.

- **Type:** string (choice)
- **Default:** `relevance`
- **Options:** `relevance` (Most relevant), `newest` (Newest)

</details>

## Direct Download

### Direct Download: Download Sources

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `AA_DONATOR_KEY` | Enables fast download access on AA. Get this from your donator account page. | string (secret) | _none_ |
| `FAST_SOURCES_DISPLAY` | Always tried first, no waiting or bypass required. | JSON array | _see UI for defaults_ |
| `SOURCE_PRIORITY` | Fallback sources, may have waiting. Requires bypasser. Drag to reorder. | JSON array | _see UI for defaults_ |
| `MAX_RETRY` | Maximum retry attempts for failed downloads. | number | `10` |
| `DEFAULT_SLEEP` | Wait time between download retry attempts. | number | `5` |
| `AA_CONTENT_TYPE_ROUTING` | Override destination based on content type metadata. | boolean | `false` |
| `AA_CONTENT_TYPE_DIR_FICTION` | Fiction Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_NON_FICTION` | Non-Fiction Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_UNKNOWN` | Unknown Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_MAGAZINE` | Magazines | string | _none_ |
| `AA_CONTENT_TYPE_DIR_COMIC` | Comic Books | string | _none_ |
| `AA_CONTENT_TYPE_DIR_STANDARDS` | Standards Documents | string | _none_ |
| `AA_CONTENT_TYPE_DIR_MUSICAL_SCORE` | Musical Scores | string | _none_ |
| `AA_CONTENT_TYPE_DIR_OTHER` | Other | string | _none_ |

<details>
<summary>Detailed descriptions</summary>

#### `AA_DONATOR_KEY`

**Account Donator Key**

Enables fast download access on AA. Get this from your donator account page.

- **Type:** string (secret)
- **Default:** _none_

#### `FAST_SOURCES_DISPLAY`

**Fast downloads**

Always tried first, no waiting or bypass required.

- **Type:** JSON array
- **Default:** _see UI for defaults_

#### `SOURCE_PRIORITY`

**Slow downloads**

Fallback sources, may have waiting. Requires bypasser. Drag to reorder.

- **Type:** JSON array
- **Default:** _see UI for defaults_

#### `MAX_RETRY`

**Max Retries**

Maximum retry attempts for failed downloads.

- **Type:** number
- **Default:** `10`
- **Constraints:** min: 1, max: 50

#### `DEFAULT_SLEEP`

**Retry Delay (seconds)**

Wait time between download retry attempts.

- **Type:** number
- **Default:** `5`
- **Constraints:** min: 1, max: 60

#### `AA_CONTENT_TYPE_ROUTING`

**Enable Content-Type Routing**

Override destination based on content type metadata.

- **Type:** boolean
- **Default:** `false`

#### `AA_CONTENT_TYPE_DIR_FICTION`

**Fiction Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_NON_FICTION`

**Non-Fiction Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_UNKNOWN`

**Unknown Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_MAGAZINE`

**Magazines**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_COMIC`

**Comic Books**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_STANDARDS`

**Standards Documents**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_MUSICAL_SCORE`

**Musical Scores**

- **Type:** string
- **Default:** _none_

#### `AA_CONTENT_TYPE_DIR_OTHER`

**Other**

- **Type:** string
- **Default:** _none_

</details>

### Direct Download: Cloudflare Bypass

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `USE_CF_BYPASS` | Attempt to bypass Cloudflare protection on download sites. | boolean | `true` |
| `USING_EXTERNAL_BYPASSER` | Use FlareSolverr or similar external service instead of built-in bypasser. Caution: May have limitations with custom DNS, Tor and proxies. You may experience slower downloads and and poorer reliability compared to the internal bypasser. | boolean | `false` |
| `EXT_BYPASSER_URL` | URL of the external bypasser service (e.g., FlareSolverr). | string | `http://flaresolverr:8191` |
| `EXT_BYPASSER_PATH` | API path for the external bypasser. | string | `/v1` |
| `EXT_BYPASSER_TIMEOUT` | Timeout for external bypasser requests in milliseconds. | number | `60000` |

<details>
<summary>Detailed descriptions</summary>

#### `USE_CF_BYPASS`

**Enable Cloudflare Bypass**

Attempt to bypass Cloudflare protection on download sites.

- **Type:** boolean
- **Default:** `true`
- **Requires restart:** Yes

#### `USING_EXTERNAL_BYPASSER`

**Use External Bypasser**

Use FlareSolverr or similar external service instead of built-in bypasser. Caution: May have limitations with custom DNS, Tor and proxies. You may experience slower downloads and and poorer reliability compared to the internal bypasser.

- **Type:** boolean
- **Default:** `false`
- **Requires restart:** Yes

#### `EXT_BYPASSER_URL`

**External Bypasser URL**

URL of the external bypasser service (e.g., FlareSolverr).

- **Type:** string
- **Default:** `http://flaresolverr:8191`
- **Requires restart:** Yes

#### `EXT_BYPASSER_PATH`

**External Bypasser Path**

API path for the external bypasser.

- **Type:** string
- **Default:** `/v1`
- **Requires restart:** Yes

#### `EXT_BYPASSER_TIMEOUT`

**External Bypasser Timeout (ms)**

Timeout for external bypasser requests in milliseconds.

- **Type:** number
- **Default:** `60000`
- **Requires restart:** Yes
- **Constraints:** min: 10000, max: 300000

</details>

### Direct Download: Mirrors

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `AA_BASE_URL` | Select 'Auto' to try mirrors from your list on startup and fall back on failures. Choosing a specific mirror locks Shelfmark to that mirror (no fallback). | string (choice) | `auto` |
| `AA_MIRROR_URLS` | Editable list of AA mirrors. Used to populate the Primary Mirror dropdown and the order used when Auto is selected. Type a URL and press Enter to add. Order matters for auto-rotation | string | `https://annas-archive.gl,https://annas-archive.li` |
| `AA_ADDITIONAL_URLS` | Deprecated. Use Mirrors instead. This is kept for backwards compatibility with existing installs and environment variables. | string | _none_ |
| `LIBGEN_ADDITIONAL_URLS` | Comma-separated list of custom LibGen mirrors to add to the defaults. | string | _none_ |
| `ZLIB_PRIMARY_URL` | Z-Library mirror to use for downloads. | string (choice) | `https://z-lib.fm` |
| `ZLIB_ADDITIONAL_URLS` | Comma-separated list of custom Z-Library mirror URLs. | string | _none_ |
| `WELIB_PRIMARY_URL` | Welib mirror to use for downloads. | string (choice) | `https://welib.org` |
| `WELIB_ADDITIONAL_URLS` | Comma-separated list of custom Welib mirror URLs. | string | _none_ |

<details>
<summary>Detailed descriptions</summary>

#### `AA_BASE_URL`

**Primary Mirror**

Select 'Auto' to try mirrors from your list on startup and fall back on failures. Choosing a specific mirror locks Shelfmark to that mirror (no fallback).

- **Type:** string (choice)
- **Default:** `auto`
- **Options:** `auto` (Auto (Recommended)), `https://annas-archive.gl` (annas-archive.gl), `https://annas-archive.li` (annas-archive.li)

#### `AA_MIRROR_URLS`

**Mirrors**

Editable list of AA mirrors. Used to populate the Primary Mirror dropdown and the order used when Auto is selected. Type a URL and press Enter to add. Order matters for auto-rotation

- **Type:** string
- **Default:** `https://annas-archive.gl,https://annas-archive.li`

#### `AA_ADDITIONAL_URLS`

**Additional Mirrors (Legacy)**

Deprecated. Use Mirrors instead. This is kept for backwards compatibility with existing installs and environment variables.

- **Type:** string
- **Default:** _none_

#### `LIBGEN_ADDITIONAL_URLS`

**Additional Mirrors**

Comma-separated list of custom LibGen mirrors to add to the defaults.

- **Type:** string
- **Default:** _none_

#### `ZLIB_PRIMARY_URL`

**Primary Mirror**

Z-Library mirror to use for downloads.

- **Type:** string (choice)
- **Default:** `https://z-lib.fm`
- **Options:** `https://z-lib.fm` (z-lib.fm), `https://z-lib.gs` (z-lib.gs), `https://z-lib.id` (z-lib.id), `https://z-library.sk` (z-library.sk), `https://zlibrary-global.se` (zlibrary-global.se)

#### `ZLIB_ADDITIONAL_URLS`

**Additional Mirrors**

Comma-separated list of custom Z-Library mirror URLs.

- **Type:** string
- **Default:** _none_

#### `WELIB_PRIMARY_URL`

**Primary Mirror**

Welib mirror to use for downloads.

- **Type:** string (choice)
- **Default:** `https://welib.org`
- **Options:** `https://welib.org` (welib.org)

#### `WELIB_ADDITIONAL_URLS`

**Additional Mirrors**

Comma-separated list of custom Welib mirror URLs.

- **Type:** string
- **Default:** _none_

</details>

## Prowlarr

### Prowlarr: Configuration

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `PROWLARR_ENABLED` | Enable searching for books via Prowlarr indexers | boolean | `false` |
| `PROWLARR_URL` | Base URL of your Prowlarr instance | string | _none_ |
| `PROWLARR_API_KEY` | Found in Prowlarr: Settings > General > API Key | string (secret) | _none_ |
| `PROWLARR_INDEXERS` | Select which indexers to search. 📚 = has book categories. Leave empty to search all. | string (comma-separated) | _empty list_ |
| `PROWLARR_AUTO_EXPAND` | Automatically retry search without category filtering if no results are found | boolean | `false` |

<details>
<summary>Detailed descriptions</summary>

#### `PROWLARR_ENABLED`

**Enable Prowlarr source**

Enable searching for books via Prowlarr indexers

- **Type:** boolean
- **Default:** `false`

#### `PROWLARR_URL`

**Prowlarr URL**

Base URL of your Prowlarr instance

- **Type:** string
- **Default:** _none_
- **Required:** Yes

#### `PROWLARR_API_KEY`

**API Key**

Found in Prowlarr: Settings > General > API Key

- **Type:** string (secret)
- **Default:** _none_
- **Required:** Yes

#### `PROWLARR_INDEXERS`

**Indexers to Search**

Select which indexers to search. 📚 = has book categories. Leave empty to search all.

- **Type:** string (comma-separated)
- **Default:** _empty list_

#### `PROWLARR_AUTO_EXPAND`

**Auto-expand search on no results**

Automatically retry search without category filtering if no results are found

- **Type:** boolean
- **Default:** `false`

</details>

### Prowlarr: Download Clients

| Variable | Description | Type | Default |
|----------|-------------|------|---------|
| `PROWLARR_TORRENT_CLIENT` | Choose which torrent client to use | string (choice) | _empty string_ |
| `QBITTORRENT_URL` | Web UI URL of your qBittorrent instance | string | _none_ |
| `QBITTORRENT_USERNAME` | qBittorrent Web UI username | string | _none_ |
| `QBITTORRENT_PASSWORD` | qBittorrent Web UI password | string (secret) | _none_ |
| `QBITTORRENT_CATEGORY` | Category to assign to book downloads in qBittorrent | string | `books` |
| `QBITTORRENT_CATEGORY_AUDIOBOOK` | Category for audiobook downloads. Leave empty to use the book category. | string | _empty string_ |
| `TRANSMISSION_URL` | URL of your Transmission instance | string | _none_ |
| `TRANSMISSION_USERNAME` | Transmission RPC username (if authentication enabled) | string | _none_ |
| `TRANSMISSION_PASSWORD` | Transmission RPC password | string (secret) | _none_ |
| `TRANSMISSION_CATEGORY` | Label to assign to book downloads in Transmission | string | `books` |
| `TRANSMISSION_CATEGORY_AUDIOBOOK` | Label for audiobook downloads. Leave empty to use the book label. | string | _empty string_ |
| `DELUGE_HOST` | Hostname/IP or full URL of your Deluge Web UI (deluge-web) | string | `localhost` |
| `DELUGE_PORT` | Deluge Web UI port (default: 8112) | string | `8112` |
| `DELUGE_PASSWORD` | Deluge Web UI password (default: deluge) | string (secret) | _none_ |
| `DELUGE_CATEGORY` | Label to assign to book downloads in Deluge | string | `books` |
| `DELUGE_CATEGORY_AUDIOBOOK` | Label for audiobook downloads. Leave empty to use the book label. | string | _empty string_ |
| `RTORRENT_URL` | XML-RPC URL of your rTorrent instance | string | _none_ |
| `RTORRENT_USERNAME` | HTTP Basic auth username (if authentication enabled) | string | _none_ |
| `RTORRENT_PASSWORD` | HTTP Basic auth password | string (secret) | _none_ |
| `RTORRENT_LABEL` | Label to assign to book downloads in rTorrent | string | `cwabd` |
| `RTORRENT_DOWNLOAD_DIR` | Server-side directory where torrents are downloaded (optional, uses rTorrent default if not specified) | string | _none_ |
| `PROWLARR_USENET_CLIENT` | Choose which usenet client to use | string (choice) | _empty string_ |
| `NZBGET_URL` | URL of your NZBGet instance | string | _none_ |
| `NZBGET_USERNAME` | NZBGet control username | string | `nzbget` |
| `NZBGET_PASSWORD` | NZBGet control password | string (secret) | _none_ |
| `NZBGET_CATEGORY` | Category to assign to book downloads in NZBGet | string | `Books` |
| `NZBGET_CATEGORY_AUDIOBOOK` | Category for audiobook downloads. Leave empty to use the book category. | string | _empty string_ |
| `SABNZBD_URL` | URL of your SABnzbd instance | string | _none_ |
| `SABNZBD_API_KEY` | Found in SABnzbd: Config > General > API Key | string (secret) | _none_ |
| `SABNZBD_CATEGORY` | Category to assign to book downloads in SABnzbd | string | `books` |
| `SABNZBD_CATEGORY_AUDIOBOOK` | Category for audiobook downloads. Leave empty to use the book category. | string | _empty string_ |
| `PROWLARR_USENET_ACTION` | Move deletes the job from your usenet client after import; Copy keeps it in the client | string (choice) | `move` |

<details>
<summary>Detailed descriptions</summary>

#### `PROWLARR_TORRENT_CLIENT`

**Torrent Client**

Choose which torrent client to use

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (None), `qbittorrent` (qBittorrent), `transmission` (Transmission), `deluge` (Deluge), `rtorrent` (rTorrent)

#### `QBITTORRENT_URL`

**qBittorrent URL**

Web UI URL of your qBittorrent instance

- **Type:** string
- **Default:** _none_

#### `QBITTORRENT_USERNAME`

**Username**

qBittorrent Web UI username

- **Type:** string
- **Default:** _none_

#### `QBITTORRENT_PASSWORD`

**Password**

qBittorrent Web UI password

- **Type:** string (secret)
- **Default:** _none_

#### `QBITTORRENT_CATEGORY`

**Book Category**

Category to assign to book downloads in qBittorrent

- **Type:** string
- **Default:** `books`

#### `QBITTORRENT_CATEGORY_AUDIOBOOK`

**Audiobook Category**

Category for audiobook downloads. Leave empty to use the book category.

- **Type:** string
- **Default:** _empty string_

#### `TRANSMISSION_URL`

**Transmission URL**

URL of your Transmission instance

- **Type:** string
- **Default:** _none_

#### `TRANSMISSION_USERNAME`

**Username**

Transmission RPC username (if authentication enabled)

- **Type:** string
- **Default:** _none_

#### `TRANSMISSION_PASSWORD`

**Password**

Transmission RPC password

- **Type:** string (secret)
- **Default:** _none_

#### `TRANSMISSION_CATEGORY`

**Book Label**

Label to assign to book downloads in Transmission

- **Type:** string
- **Default:** `books`

#### `TRANSMISSION_CATEGORY_AUDIOBOOK`

**Audiobook Label**

Label for audiobook downloads. Leave empty to use the book label.

- **Type:** string
- **Default:** _empty string_

#### `DELUGE_HOST`

**Deluge Web UI Host/URL**

Hostname/IP or full URL of your Deluge Web UI (deluge-web)

- **Type:** string
- **Default:** `localhost`

#### `DELUGE_PORT`

**Deluge Web UI Port**

Deluge Web UI port (default: 8112)

- **Type:** string
- **Default:** `8112`

#### `DELUGE_PASSWORD`

**Password**

Deluge Web UI password (default: deluge)

- **Type:** string (secret)
- **Default:** _none_

#### `DELUGE_CATEGORY`

**Book Label**

Label to assign to book downloads in Deluge

- **Type:** string
- **Default:** `books`

#### `DELUGE_CATEGORY_AUDIOBOOK`

**Audiobook Label**

Label for audiobook downloads. Leave empty to use the book label.

- **Type:** string
- **Default:** _empty string_

#### `RTORRENT_URL`

**rTorrent URL**

XML-RPC URL of your rTorrent instance

- **Type:** string
- **Default:** _none_

#### `RTORRENT_USERNAME`

**Username**

HTTP Basic auth username (if authentication enabled)

- **Type:** string
- **Default:** _none_

#### `RTORRENT_PASSWORD`

**Password**

HTTP Basic auth password

- **Type:** string (secret)
- **Default:** _none_

#### `RTORRENT_LABEL`

**Book Label**

Label to assign to book downloads in rTorrent

- **Type:** string
- **Default:** `cwabd`

#### `RTORRENT_DOWNLOAD_DIR`

**Download Directory**

Server-side directory where torrents are downloaded (optional, uses rTorrent default if not specified)

- **Type:** string
- **Default:** _none_

#### `PROWLARR_USENET_CLIENT`

**Usenet Client**

Choose which usenet client to use

- **Type:** string (choice)
- **Default:** _empty string_
- **Options:** `""` (None), `nzbget` (NZBGet), `sabnzbd` (SABnzbd)

#### `NZBGET_URL`

**NZBGet URL**

URL of your NZBGet instance

- **Type:** string
- **Default:** _none_

#### `NZBGET_USERNAME`

**Username**

NZBGet control username

- **Type:** string
- **Default:** `nzbget`

#### `NZBGET_PASSWORD`

**Password**

NZBGet control password

- **Type:** string (secret)
- **Default:** _none_

#### `NZBGET_CATEGORY`

**Book Category**

Category to assign to book downloads in NZBGet

- **Type:** string
- **Default:** `Books`

#### `NZBGET_CATEGORY_AUDIOBOOK`

**Audiobook Category**

Category for audiobook downloads. Leave empty to use the book category.

- **Type:** string
- **Default:** _empty string_

#### `SABNZBD_URL`

**SABnzbd URL**

URL of your SABnzbd instance

- **Type:** string
- **Default:** _none_

#### `SABNZBD_API_KEY`

**API Key**

Found in SABnzbd: Config > General > API Key

- **Type:** string (secret)
- **Default:** _none_

#### `SABNZBD_CATEGORY`

**Book Category**

Category to assign to book downloads in SABnzbd

- **Type:** string
- **Default:** `books`

#### `SABNZBD_CATEGORY_AUDIOBOOK`

**Audiobook Category**

Category for audiobook downloads. Leave empty to use the book category.

- **Type:** string
- **Default:** _empty string_

#### `PROWLARR_USENET_ACTION`

**NZB Completion Action**

Move deletes the job from your usenet client after import; Copy keeps it in the client

- **Type:** string (choice)
- **Default:** `move`
- **Options:** `move` (Move), `copy` (Copy)

</details>

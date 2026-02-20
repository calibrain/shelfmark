# 📚 Shelfmark: Book Downloader

Formerly *Calibre Web Automated Book Downloader (CWABD)*

<img src="src/frontend/public/logo.png" alt="Shelfmark" width="200">

Shelfmark is a self-hosted web interface for searching and downloading books and audiobooks from multiple sources. Works out of the box with popular web sources, no configuration required. Add metadata providers, additional release sources, and download clients to build a single hub for your digital library. Supports multiple users with a built-in request system, so you can share your instance with others and let them browse and request books on their own.

**Fully standalone** - no external dependencies required. Works great alongside the following library tools, with support for automatic imports:
- [Calibre](https://calibre-ebook.com/)
- [Calibre-Web](https://github.com/janeczku/calibre-web)
- [Calibre-Web-Automated](https://github.com/crocodilestick/Calibre-Web-Automated)
- [Booklore](https://github.com/booklore-app/booklore)
- [Audiobookshelf](https://github.com/advplyr/audiobookshelf)

## ✨ Features

- **One-Stop Interface** - A clean, modern UI to search, browse, and download from multiple sources in one place
- **Multiple Sources** - Popular archive websites, Torrent, Usenet, and IRC download support
- **Audiobook Support** - Full audiobook search and download with dedicated processing
- **Two Search Modes**:
  - **Direct** - Search popular web sources
  - **Universal** - Search metadata providers (Hardcover, Open Library) for richer book and audiobook discovery, with multi-source downloads
- **Multi-User & Requests** - Share your instance with others, let users browse and request books, and manage approvals with configurable notifications
- **Authentication** - Built-in login, OIDC single sign-on, proxy auth, and Calibre-Web database support
- **Real-Time Progress** - Unified download queue with live status updates across all sources
- **Cloudflare Bypass** - Built-in bypasser for reliable access to protected sources

## 🖼️ Screenshots

**Home screen**
![Home screen](README_images/homescreen.png 'Home screen')

**Search results**
![Search results](README_images/search-results.png 'Search results')

**Multi-source downloads**
![Multi-source downloads](README_images/multi-source.png 'Multi-source downloads')

**Download queue**
![Download queue](README_images/downloads.png 'Download queue')

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose

### Installation

1. Download the [docker-compose file](compose/docker-compose.yml):
   ```bash
   curl -O https://raw.githubusercontent.com/calibrain/shelfmark/main/compose/docker-compose.yml
   ```

2. Start the service:
   ```bash
   docker compose up -d
   ```

3. Open `http://localhost:8084`

That's it! Configure settings through the web interface as needed.

### Volume Setup

```yaml
volumes:
  - /your/config/path:/config # Config, database, and artwork cache directory
  - /your/download/path:/books # Downloaded books
  - /client/path:/client/path # Optional: For Torrent/Usenet downloads, match your client directory exactly. 
```

> **Tip**: Point the download volume to your CWA or Booklore ingest folder for automatic import.

> **Note**: CIFS shares require `nobrl` mount option to avoid database lock errors.

## ⚙️ Configuration

### Search Modes

**Direct** (default)
- Works out of the box, no setup required
- Searches a huge library of books directly
- Returns downloadable releases immediately

**Universal**
- Cleaner search results via metadata providers (Hardcover is recommended)
- Aggregates releases from multiple configured sources
- Full Audiobook support
- Requires manual setup (API keys, additional sources)

### Environment Variables

Environment variables work for initial setup and Docker deployments. They serve as defaults that can be overridden in the web interface.

| Variable | Description | Default |
|----------|-------------|---------|
| `FLASK_PORT` | Web interface port | `8084` |
| `INGEST_DIR` | Book download directory | `/books` |
| `TZ` | Container timezone | `UTC` |
| `PUID` / `PGID` | Runtime user/group ID (also supports legacy `UID`/`GID`) | `1000` / `1000` |
| `SEARCH_MODE` | `direct` or `universal` | `direct` |
| `USING_TOR` | Enable Tor routing (requires `NET_ADMIN` capability) | `false` |

See the full [Environment Variables Reference](docs/environment-variables.md) for all available options.

Some of the additional options available in Settings:
- **Fast Download Key** - Use your paid account to skip Cloudflare challenges entirely and use faster, direct downloads
- **Prowlarr** - Configure indexers and download clients to download books and audiobooks
- **AudiobookBay** - Web scraping source for audiobook torrents (audiobooks only)
- **IRC** - Add details for IRC book sources and download directly from the UI
- **Library Link** - Add a link to your Calibre-Web or Booklore instance in the UI header
- **File processing** - Customiseable download paths, file renaming and directory creation with template-based renaming
- **Network Resilience** - Auto DNS rotation and mirror fallback when sources are unreachable. Custom proxy support (SOCK5 + HTTP/S), Tor routing.
- **Format & Language** - Filter downloads by preferred formats, languages and sorting order
- **Metadata Providers** - Configure API keys for Hardcover, Open Library, etc.

## 🐳 Docker Variants

### Standard
```bash
docker compose up -d
```

The full-featured image with built-in Cloudflare bypass.

#### Enable Tor Routing
Routes all traffic through Tor for enhanced privacy:
```bash
curl -O https://raw.githubusercontent.com/calibrain/shelfmark/main/compose/docker-compose.tor.yml
docker compose -f docker-compose.tor.yml up -d
```

**Notes:**
- Requires `NET_ADMIN` and `NET_RAW` capabilities
- Timezone is auto-detected from Tor exit node
- Custom DNS/proxy settings are ignored when Tor is active

### Lite
A smaller image without the built-in Cloudflare bypasser. Ideal for:

- **External bypassers** - Already running FlareSolverr or ByParr for other services
- **Fast downloads** - Using fast download sources
- **Alternative sources only** - Exclusively using Prowlarr, AudiobookBay, IRC, or other sources
- **Audiobooks** - Using Shelfmark exclusively for audiobooks

```bash
curl -O https://raw.githubusercontent.com/calibrain/shelfmark/main/compose/docker-compose.lite.yml
docker compose -f docker-compose.lite.yml up -d
```

If you need Cloudflare bypass with the Lite image, configure an external resolver (FlareSolverr/ByParr) in Settings under the Cloudflare tab.

## 🔐 Authentication

Authentication is optional but recommended for shared or exposed instances. Multiple authentication methods are available in Settings:

**1. Single Username/Password**

**2. Proxy (Forward) Authentication**

Proxy auth trusts headers set by your reverse proxy (e.g. `X-Auth-User`). Ensure Shelfmark is not directly exposed, and configure your proxy to strip/overwrite these headers for all inbound requests.

**3. OIDC (OpenID Connect)**

Integrate with your identity provider (Authelia, Authentik, Keycloak, etc.) for single sign-on. Supports PKCE flow, auto-discovery, group-based admin mapping, and auto-provisioning of new users.

**4. Calibre-Web Database**

If you're running Calibre-Web, you can reuse its user database by mounting it:

```yaml
volumes:
  - /path/to/calibre-web/app.db:/auth/app.db:ro
```

### Multi-User Support

With any authentication method enabled, Shelfmark supports multi-user management with admin/user roles. Users can have per-user settings for download destinations, email recipients, and notification preferences. Non-admin users only see their own downloads and can submit book requests for admin review. Admins can configure request policies per source to control whether users can download directly, must submit a request, or are blocked entirely.

## Health Monitoring

The application exposes a health endpoint at `/api/health` (no authentication required). Add a health check to your compose:

```yaml
healthcheck:
  test: ["CMD", "curl", "-sf", "http://localhost:8084/api/health"]
  interval: 30s
  timeout: 30s
  retries: 3
```

## Logging

Logs are available via:
- `docker logs <container-name>`
- `/var/log/shelfmark/` inside the container (when `ENABLE_LOGGING=true`)

Log level is configurable via Settings or `LOG_LEVEL` environment variable.

## Development

```bash
# Frontend development
make install     # Install dependencies
make dev         # Start Vite dev server (localhost:5173)
make build       # Production build
make typecheck   # TypeScript checks

# Backend (Docker)
make up          # Start backend via docker-compose.dev.yml
make down        # Stop services
make refresh     # Rebuild and restart
make restart     # Restart container
```

The frontend dev server proxies to the backend on port 8084.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Web Interface                          │
│                 (React + TypeScript + Vite)                 │
├─────────────────────────────────────────────────────────────┤
│                      Flask Backend                          │
│                   (REST API + WebSocket)                    │
├───────────────────┬─────────────────────┬───────────────────┤
│ Metadata Providers│   Download Queue    │  Cloudflare       │
│                   │   & Orchestrator    │  Bypass           │
├───────────────────┼─────────────────────┼───────────────────┤
│ • Hardcover       │ • Task scheduling   │ • Internal        │
│ • Open Library    │ • Progress tracking │ • External        │
│                   │ • Retry logic       │   (FlareSolverr)  │
├───────────────────┴─────────────────────┴───────────────────┤
│                     Release Sources                         │
├─────────────────────────────────────────────────────────────┤
│ • Direct Download (Web Sources → Mirrors → Fallbacks)       │
├─────────────────────────────────────────────────────────────┤
│                     Network Layer                           │
├─────────────────────────────────────────────────────────────┤
│ • Auto DNS rotation  • Mirror failover  • Resume support    │
└─────────────────────────────────────────────────────────────┘
```

The backend uses a plugin architecture. Metadata providers and release sources register via decorators and are automatically discovered.

## Contributing

Shelfmark's core feature set is now largely complete. Development going forward will focus on stability, bug fixes, and maintenance rather than major new features. Contributions in these areas are welcome - please file issues or submit pull requests on GitHub.

## License

MIT License - see [LICENSE](LICENSE) for details.

## ⚠️ Disclaimers

### Copyright Notice

This tool can access various sources including those that might contain copyrighted material. Users are responsible for:
- Ensuring they have the right to download requested materials
- Respecting copyright laws and intellectual property rights
- Using the tool in compliance with their local regulations

### Library Integration

Downloads are written atomically (via intermediate `.crdownload` files) to prevent partial files from being ingested. However, if your library tool (CWA, Booklore, Calibre) is actively scanning or importing, there's a small chance of race conditions. If you experience database errors or import failures, try pausing your library's auto-import during bulk downloads.

## Support

For issues or questions, please [file an issue](https://github.com/calibrain/shelfmark/issues) on GitHub.

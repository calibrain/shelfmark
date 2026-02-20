## New Features

### OIDC Authentication (#606, #612)
- **OIDC login** with PKCE flow, auto-discovery, and group-based admin mapping
- **Auto-provisioning** of OIDC users (configurable) and email-based account linking
- **Password fallback** when OIDC is enabled to prevent admin lockout
- Backwards compatible with all existing auth modes (no-auth, builtin, proxy, CWA)

### Multi-User Support (#606, #612, #613)
- **User management** -create, edit, and delete users with admin/user roles
- **Per-user settings** -custom download destinations, BookLore library/path, email recipients, and `{User}` template variable
- **Per-user download visibility** -non-admins only see their own downloads

### Multi-User Request System (#615, #617, #620)
- **Book request workflow** -users can request books with notes; admins review, approve, and fulfil requests
- **Policy-based configuration** -set download/request/block policies per content type or per source (e.g. allow direct downloads, set Prowlarr to request-only)
- **Per-user policy overrides** for tailored access control
- **New Activity Sidebar** -replaces downloads sidebar, combining active downloads with requests; sidebar can now be pinned
- Request retry support and admin-level request management

### Notification Support (#618)
- **Apprise-based notifications** for request events and download completions
- Configurable globally or per user, with full customization of events and notification services
- Expanded activity cards with detailed request info and file management

### AudiobookBay Release Source (#619, #621, #623)
- **New release source** -search AudiobookBay for audiobook torrents directly from the UI
- Results include title, language, format, and size
- Downloads via configured torrent client with audiobook-specific category support
- Configurable hostname, max search pages, and rate limit delay

### Email Output Mode (#603, #604)
- **Email delivery** as an alternative output mode for downloaded books
- Per-user email recipient configuration

## Improvements
- Admin-configurable visibility for self-settings options (delivery preferences, notifications) (#625)
- BookLore Bookdrop API destination support as an alternative to specific library selection (#625)
- Download path options for all torrent clients (#625)
- Add tag support to qBittorrent downloads (#610 by @dawescc)
- Add threading to file system operations for improved performance (#602)
- Enhanced custom scripting -JSON download info, more consistent activation, decoupled from staging (#591)
- Hardlink-before-move optimization for file transfers (#591)
- New BookLore API file formats (#591)
- Improved login cookie naming for reverse proxy compatibility (#591)
- Fix Transmission URL parsing (#591)
- Fix healthcheck starvation during large file processing (#591)

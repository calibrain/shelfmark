# Design: Directory & Volume Setup Documentation

**Date:** 2026-02-05
**Status:** Ready for writing
**Target file:** `docs/configuration.md` or `docs/directory-setup.md`

## Purpose

User guide for configuring directories and Docker volumes in Shelfmark. Addresses common confusion around:
- Destination vs download client paths
- Docker volume mapping for torrent/usenet setups
- When to use file processing options (hardlink, copy, organize)

## Target Audience

Docker-savvy users who understand volumes and compose files but need Shelfmark-specific guidance.

---

## Document Structure

### 1. Conceptual Overview

**Opening:** Shelfmark uses different directories depending on download method:
- Direct downloads: simple two-folder setup
- Torrent/Usenet: requires path matching between Shelfmark and download client

**Diagram:**

```
┌─────────────────────────────────────────────────────────────────┐
│                     DIRECT DOWNLOADS                            │
│                                                                 │
│         Shelfmark downloads directly → destination              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                    TORRENT / USENET                             │
│                                                                 │
│  Prowlarr → Download client saves to <client path>              │
│           → Shelfmark reads from <client path>                  │
│           → Shelfmark processes to destination                  │
└─────────────────────────────────────────────────────────────────┘
```

**Key point:** For torrent/usenet, Shelfmark needs to see the same files your download client sees - this is why path matching matters.

---

### 2. Direct Download Setup

Simplest case - users who only use direct downloads.

**Required volumes:**

| Volume | Purpose |
|--------|---------|
| `/config` | Settings, database, cover cache |
| `/books` | Where finished downloads are saved |

**Example docker-compose:**

```yaml
services:
  shelfmark:
    image: ghcr.io/calibrain/shelfmark:latest
    volumes:
      - /path/to/config:/config
      - /path/to/books:/books
```

**Notes to include:**
- Library integration: point `/books` at CWA/Booklore/Calibre ingest folder for automatic pickup
- Permissions: PUID/PGID must match owner of host directories

---

### 3. Torrent / Usenet Setup

**Key concept:** Download client and Shelfmark must see completed downloads at the same path inside their containers.

**Why:** When qBittorrent reports "file is at `/data/torrents/books/MyBook.epub`", Shelfmark needs to access that exact path.

**Required volumes:**

| Volume | Purpose |
|--------|---------|
| `/config` | Settings, database, cover cache |
| `/books` | Destination for processed files |
| `<client path>` | Must match download client's volume exactly |

**Side-by-side example:**

```yaml
services:
  shelfmark:
    volumes:
      - /path/to/config:/config
      - /path/to/books:/books
      - /path/to/downloads:/data/torrents  # ← Must match client

  qbittorrent:
    volumes:
      - /path/to/downloads:/data/torrents  # ← Same mapping
```

**Callout:** Host path (`/path/to/downloads`) can be anything, but container path (`/data/torrents`) must be identical in both containers.

**What happens if paths don't match:** Shelfmark can't find the file → download fails with error.

**Advanced: Remote Path Mappings**

If paths can't match (download client on different machine, existing setup you can't change), Shelfmark supports remote path mappings. Translates paths from what client reports to what Shelfmark can access. Configure in Settings under download client section.

---

### 4. File Processing Options

**Transfer methods (Torrent/Usenet only):**

| Method | When to use |
|--------|-------------|
| **Copy** (default) | Safe default, works everywhere |
| **Hardlink** | Keep seeding without doubling disk space |

**Hardlink requirements:**
- Source and destination must be on same filesystem
- Falls back to copy automatically if not possible

**File organization:**

Three modes: **None**, **Rename**, **Organize**. Configure templates in Settings → Downloads.

**Quick guidance:**
- Sending to CWA/Booklore ingest → **Rename** or **None** (they handle organization)
- Managing files yourself → **Organize** for folder structure
- Hardlinking torrents → Archive extraction disabled, files stay as-is

*(Detailed template syntax belongs in separate document)*

---

### 5. Common Mistakes

**"Download failed - file not found"**
- Path mismatch between Shelfmark and download client
- Check container paths match in both volume mappings

**"Permission denied"**
- PUID/PGID doesn't match owner of host directories
- Ensure read access to client path, write access to destination

**"Hardlinks not working" / "Files being copied instead"**
- Source and destination on different filesystems
- Move destination to same filesystem, or accept copy fallback

**"Downloads work but library doesn't see them"**
- Destination not pointing to library's ingest folder
- Check Settings → Downloads → Destination

**CIFS/SMB network shares**
- Add `nobrl` mount option to avoid database lock errors
- Example: `//server/share /mnt/share cifs nobrl,... 0 0`

---

## Related Documentation

- **Environment Variables Reference** (`docs/environment-variables.md`) - auto-generated, lists all config options
- **File Naming Templates** - separate doc covering template syntax in detail (to be written)
- **Prowlarr Setup** - separate doc for full Prowlarr/client configuration (to be written)

## Open Questions

None - ready for writing.

# Test Suite

This directory contains the test suite for Shelfmark. Tests are organized by scope and component.

## Quick Start

```bash
# Sync the local Python environment once
uv sync --locked --extra browser

# Run all unit tests locally (fast, no external dependencies)
uv run pytest tests/ -v -m "not integration and not e2e"

# Run E2E API tests against a running app stack
uv run pytest tests/e2e/ -v -m e2e

# Run everything except integration tests locally
uv run pytest tests/ -v -m "not integration"
```

## Test Structure

```
tests/
├── config/                 # Settings & configuration tests
│   ├── test_docker_volumes.py          # Docker volume mapping
│   ├── test_environment.py             # Environment variable handling
│   ├── test_mirror_settings_live_apply.py  # Mirror settings live reload
│   ├── test_mirror_settings_options.py # Mirror settings options
│   ├── test_security.py               # Security settings
│   └── test_oidc_settings.py          # OIDC settings fields & show_when conditions
│
├── core/                   # Core application logic tests
│   ├── test_admin_users_api.py         # Admin user CRUD API endpoints
│   ├── test_booklore_multiuser.py      # BookLore per-user override merging
│   ├── test_builtin_multiuser.py       # Builtin auth multi-user migration
│   ├── test_download_processing.py     # Download file processing
│   ├── test_hardlink.py               # Hardlink/copy operations
│   ├── test_library_processing.py      # Library file processing
│   ├── test_manual_query.py           # Manual search query handling
│   ├── test_mirrors_config.py         # Mirror configuration
│   ├── test_naming.py                 # File naming templates
│   ├── test_oidc_auth.py             # OIDC auth helpers (group claims, user provisioning)
│   ├── test_oidc_integration.py       # OIDC integration into auth system (logic mirror)
│   ├── test_oidc_routes.py           # OIDC Flask route handlers
│   ├── test_part_number_extraction.py  # Part number extraction
│   ├── test_per_user_downloads.py     # Per-user download queue filtering
│   ├── test_permission_handling.py    # File permission handling
│   ├── test_processing_integration.py  # Processing integration
│   ├── test_search_plan.py           # Search plan logic
│   ├── test_user_db.py               # UserDB CRUD operations
│   └── test_user_template_variable.py  # {User} template variable in naming
│
├── e2e/                    # End-to-end API tests
│   ├── conftest.py         # Fixtures (APIClient, DownloadTracker)
│   ├── test_api.py         # Core API endpoint tests
│   ├── test_download_flow.py   # Full download journey tests
│   └── test_prowlarr_flow.py   # Prowlarr-specific tests
│
├── prowlarr/               # Prowlarr plugin tests
│   ├── conftest.py         # Shared fixtures
│   ├── test_clients.py     # DownloadClient base, registry, DownloadStatus
│   ├── test_qbittorrent_client.py  # qBittorrent client unit tests
│   ├── test_transmission_client.py # Transmission client unit tests
│   ├── test_nzbget_client.py       # NZBGet client unit tests
│   ├── test_sabnzbd_client.py      # SABnzbd client unit tests
│   ├── test_handler.py     # ProwlarrHandler unit tests
│   ├── test_torrent_utils.py   # Bencode, hash extraction, URL parsing
│   ├── test_bencode.py     # Bencode encoding/decoding
│   ├── test_source.py      # Release source (size parsing, format detection)
│   ├── test_cache.py       # Release cache
│   ├── test_integration_clients.py  # Integration tests (require Docker stack)
│   └── test_integration_handler.py  # Handler integration tests
│
└── README.md               # This file
```

## Test Types

### Unit Tests
Fast tests that mock external dependencies. Run these frequently during development.

```bash
uv run pytest tests/prowlarr/ -v -m "not integration"
```

**What they test:**
- Download client logic (status mapping, URL handling, error cases)
- Bencode encoding/decoding for torrent files
- Hash extraction from magnet links and .torrent files
- Protocol detection (torrent vs usenet)
- Release cache operations
- Handler download flow logic
- User database (CRUD, settings, OIDC subject linking)
- OIDC authentication (group claims, user provisioning, route handlers)
- Admin user management API (create, update, delete, password, per-user settings)
- Multi-user download queue filtering and per-user overrides
- Settings configuration (OIDC fields, show_when conditions)

### E2E Tests
Test the full application through its HTTP API. Require the app to be running.

```bash
uv run pytest tests/e2e/ -v -m e2e
```

**What they test:**
- Health check endpoint
- Configuration endpoint
- Metadata provider search (Hardcover, etc.)
- Release source listing
- Download queue operations (add, cancel, reorder, clear)
- Settings API
- Prowlarr integration

### Integration Tests
Test against real services (qBittorrent, Transmission, etc.). Require the full Docker test stack.

```bash
# Start the test stack first
docker compose -f docker-compose.test-clients.yml up -d

# Run integration tests
docker compose -f docker-compose.test-clients.yml exec shelfmark uv run pytest tests/prowlarr/ -v -m integration
```

**What they test:**
- Real connections to download clients
- Adding/removing actual torrents
- Status polling from real clients

## Test Markers

| Marker | Description | When to Skip |
|--------|-------------|--------------|
| `integration` | Requires running services (qBittorrent, etc.) | Default skip with `-m "not integration"` |
| `e2e` | End-to-end API tests | When app isn't running |
| `slow` | Tests that take longer (network calls, polling) | Quick feedback with `-m "not slow"` |

## Common Commands

```bash
# Run specific test file
uv run pytest tests/prowlarr/test_clients.py -v

# Run specific test class
uv run pytest tests/e2e/test_api.py::TestHealthEndpoint -v

# Run specific test
uv run pytest tests/e2e/test_api.py::TestHealthEndpoint::test_health_returns_ok -v

# Run with short traceback (cleaner output)
uv run pytest tests/ -v --tb=short -m "not integration"

# Run and stop on first failure
uv run pytest tests/ -v -x -m "not integration"

# Run with coverage (if pytest-cov installed)
uv run pytest tests/ --cov=shelfmark -m "not integration"
```

## Writing New Tests

### Unit Test Example

```python
from unittest.mock import MagicMock, patch

class TestMyFeature:
    def test_something(self, monkeypatch):
        # Mock config values
        monkeypatch.setattr(
            "shelfmark.module.config.get",
            lambda key, default="": {"KEY": "value"}.get(key, default),
        )

        # Test your code
        result = my_function()
        assert result == expected
```

### E2E Test Example

```python
import pytest
from .conftest import APIClient, DownloadTracker

@pytest.mark.e2e
class TestMyEndpoint:
    def test_endpoint_works(self, protected_api_client: APIClient):
        resp = protected_api_client.get("/api/my-endpoint")
        assert resp.status_code == 200

    def test_with_cleanup(
        self,
        protected_api_client: APIClient,
        download_tracker: DownloadTracker,
    ):
        # Track IDs for automatic cleanup after test
        download_tracker.track("some-id")
        # ... test code ...
```

## Test Fixtures

### E2E Fixtures (`tests/e2e/conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `api_client` | function | Fresh HTTP client for general E2E calls |
| `protected_api_client` | function | Authenticated client for protected-route E2Es |
| `download_tracker` | function | Tracks downloads for cleanup |
| `server_config` | session | Cached server configuration |

### Prowlarr Fixtures (`tests/prowlarr/conftest.py`)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `transmission_client` | module | Real Transmission client (integration) |
| `qbittorrent_client` | module | Real qBittorrent client (integration) |
| `deluge_client` | module | Real Deluge client (integration) |
| `nzbget_client` | module | Real NZBGet client (integration) |
| `sabnzbd_client` | module | Real SABnzbd client (integration) |

## Expected Skips

Some tests skip when external services aren't available. This is normal:

- **"No metadata providers available"** - Metadata provider not responding
- **"Prowlarr not configured"** - Prowlarr settings not set up
- **"No releases found"** - No indexers configured in Prowlarr
- **"Legacy search source unavailable"** - Direct download source offline
- **"Transmission/qBittorrent not available"** - Docker test stack not running

## Troubleshooting

### Tests can't connect to app
```bash
# Check the app/container is running
docker ps

# Check app logs
docker logs <your-shelfmark-container>
```

### Import errors
```bash
# Sync the local Python environment first
uv sync --locked --extra browser

# Then run tests from the repo root
uv run pytest ...
```

### Integration tests failing
```bash
# Make sure test stack is running
docker compose -f docker-compose.test-clients.yml up -d

# Check client containers
docker ps | grep -E "qbittorrent|transmission|deluge|nzbget|sabnzbd"
```

### Stale test data

Restart the container to reset the in-memory queue between test runs:
```bash
docker restart <your-shelfmark-container>
```

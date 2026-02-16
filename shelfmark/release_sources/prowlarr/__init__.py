"""
Prowlarr release source plugin.

This plugin integrates with Prowlarr to search for book releases
across multiple indexers (torrent and usenet).

Includes:
- ProwlarrSource: Search integration with Prowlarr
- ProwlarrHandler: Download handling via external clients
"""

# Import submodules to trigger decorator registration
from shelfmark.release_sources.prowlarr import source  # noqa: F401
from shelfmark.release_sources.prowlarr import handler  # noqa: F401
from shelfmark.release_sources.prowlarr import settings  # noqa: F401

# Import shared download clients/settings to trigger registration.
# This is in a try/except to handle optional dependencies gracefully.
try:
    from shelfmark.download import clients  # noqa: F401
    from shelfmark.download.clients import settings as client_settings  # noqa: F401
except ImportError as e:
    import logging

    logging.getLogger(__name__).debug(f"Download clients not loaded: {e}")

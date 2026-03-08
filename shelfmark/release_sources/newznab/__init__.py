"""
Newznab release source plugin.

Integrates with any Newznab-compatible indexer or aggregator (e.g. NZBHydra2,
NZBGeek, Drunkenslug) to search for book releases via the standard Newznab API.

Includes:
- NewznabSource: Search integration
- NewznabHandler: Download handling via configured usenet/torrent client
"""

# Import submodules to trigger decorator registration
from shelfmark.release_sources.newznab import source  # noqa: F401
from shelfmark.release_sources.newznab import handler  # noqa: F401
from shelfmark.release_sources.newznab import settings  # noqa: F401

# Import shared download clients/settings to trigger registration.
try:
    from shelfmark.download import clients  # noqa: F401
    from shelfmark.download.clients import settings as client_settings  # noqa: F401
except ImportError as e:
    import logging

    logging.getLogger(__name__).debug(f"Download clients not loaded: {e}")

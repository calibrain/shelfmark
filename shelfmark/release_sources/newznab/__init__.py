"""
Newznab release source plugin.

Integrates with any Newznab-compatible indexer or aggregator (e.g. NZBHydra2,
NZBGeek, Drunkenslug) to search for book releases via the standard Newznab API.

Includes:
- NewznabSource: Search integration
- NewznabHandler: Download handling via configured usenet/torrent client
"""

# Import submodules to trigger decorator registration
from shelfmark.release_sources.newznab import (
    handler,
    settings,
    source,
)

# Import shared download clients/settings to trigger registration.
try:
    from shelfmark.download import clients
    from shelfmark.download.clients import settings as client_settings 
except ImportError as e:
    import logging

    logging.getLogger(__name__).debug("Download clients not loaded: %s", e)

"""IRC Highway release source plugin.

Searches and downloads ebooks from IRC Highway #ebooks via DCC protocol.
Requires ENABLE_IRC_SEARCH=true env var to activate.

Based on OpenBooks (https://github.com/evan-buss/openbooks).
"""

import os

if os.environ.get("ENABLE_IRC_SEARCH", "").lower() in ("true", "1", "yes"):
    from shelfmark.release_sources.irc import source  # noqa: F401
    from shelfmark.release_sources.irc import handler  # noqa: F401
    from shelfmark.release_sources.irc import settings  # noqa: F401

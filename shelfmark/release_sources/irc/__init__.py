"""IRC release source plugin.

Searches and downloads ebooks from IRC channels via DCC protocol.
Available when IRC server, channel, and nickname are configured in settings.

Based on OpenBooks (https://github.com/evan-buss/openbooks).
"""

from shelfmark.release_sources.irc import source  # noqa: F401
from shelfmark.release_sources.irc import handler  # noqa: F401
from shelfmark.release_sources.irc import settings  # noqa: F401

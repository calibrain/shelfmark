"""IRC release source plugin.

Searches and downloads ebook and audiobook releases from IRC channels via DCC protocol.
Available when IRC server, channel, and nickname are configured in settings.

Based on OpenBooks (https://github.com/evan-buss/openbooks).
"""

from shelfmark.release_sources.irc import handler as handler
from shelfmark.release_sources.irc import settings as settings
from shelfmark.release_sources.irc import source as source

"""The audiobook format list must stay in agreement across every layer that gates on it.

These lists were maintained by hand in four places and drifted: the settings UI offered
only m4b/mp3/m4a, so FLAC/OPUS/OGG could never be enabled even though the parsers
recognized them, the sorter ranked them, and archive extraction knew them. The result was
a FLAC audiobook that was invisible in search and rejected after download. They now all
derive from `shelfmark.core.utils.AUDIOBOOK_FORMATS`; this test fails if one drifts again.
"""

from shelfmark.config.settings import _AUDIOBOOK_FORMAT_OPTIONS
from shelfmark.core.utils import ARCHIVE_FORMATS, AUDIOBOOK_FORMATS
from shelfmark.download.archive import ALL_AUDIO_EXTENSIONS
from shelfmark.release_sources.irc import parser
from shelfmark.release_sources.prowlarr.source import AUDIOBOOK_FORMATS as PROWLARR_FORMATS


def test_archive_extraction_knows_every_audiobook_format():
    assert ALL_AUDIO_EXTENSIONS == {f".{fmt}" for fmt in AUDIOBOOK_FORMATS}


def test_prowlarr_knows_every_audiobook_format():
    assert PROWLARR_FORMATS == list(AUDIOBOOK_FORMATS)


def test_irc_parser_knows_every_audiobook_format():
    assert set(AUDIOBOOK_FORMATS) <= set(parser.ALL_RECOGNIZED_FORMATS)


def test_every_audiobook_format_is_selectable_in_settings():
    """The settings list is the only one a user's config can be built from."""
    selectable = {option["value"] for option in _AUDIOBOOK_FORMAT_OPTIONS}

    assert selectable == {*AUDIOBOOK_FORMATS, *ARCHIVE_FORMATS}


def test_audiobook_and_ebook_formats_do_not_overlap():
    """Overlap would make content-type classification by extension ambiguous."""
    assert not set(AUDIOBOOK_FORMATS) & set(parser.EBOOK_FORMATS)

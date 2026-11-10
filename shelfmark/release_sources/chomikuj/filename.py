"""Chomikuj filename, format, and language parsing.

Chomikuj exposes the file extension in ``FileType`` and usually omits it from
``FileName``, but some rows embed it in the name. This module is the single
source of truth for which formats may be imported, which extensions are
archives that must never be offered, and how a ``FileName``/``FileType`` pair
maps to a title, an optional author, and a format.

The parsing shapes are ported from a validated Chomikuj implementation's
``filename.mjs``. The language heuristic is a Shelfmark-specific addition
modelled on :mod:`shelfmark.release_sources.webshare.source`: Chomikuj has no
server-side language filter, so a language tag is read from the name for display
only and ambiguous or unknown names stay ``None`` instead of dropping a row.
"""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# Formats Shelfmark can postprocess. The validated importable-format list minus
# ``kepub``, which Shelfmark's postprocess step cannot track
# (shelfmark/download/postprocess/scan.py).
IMPORTABLE_EBOOK_FORMATS = ("epub", "mobi", "azw3", "azw", "pdf", "fb2", "djvu")

# Archives are never offered or imported by the Chomikuj source, even though
# Shelfmark's default ebook formats include cbz and cbr for other sources.
ARCHIVE_EXTENSIONS = ("zip", "rar", "7z", "tar", "gz", "bz2", "xz", "cbz", "cbr", "cb7", "cbt")

_KNOWN_EXTENSIONS = frozenset((*IMPORTABLE_EBOOK_FORMATS, *ARCHIVE_EXTENSIONS))

# Webshare-style language tags mapped to ISO 639-1. An unknown or ambiguous tag
# leaves the language as None; it never filters a result out.
_LANGUAGE_TAGS = {
    "pl": "pl",
    "pol": "pl",
    "polish": "pl",
    "polski": "pl",
    "en": "en",
    "eng": "en",
    "english": "en",
    "angielski": "en",
    "cz": "cs",
    "cze": "cs",
    "ces": "cs",
    "czech": "cs",
    "cesky": "cs",
    "sk": "sk",
    "slo": "sk",
    "slk": "sk",
    "slovak": "sk",
    "slovensky": "sk",
    "de": "de",
    "ger": "de",
    "deu": "de",
}

# Broader tag set used only to strip bracket noise (from the validated
# implementation), so tags such as ``[FR]`` disappear from titles even though
# they are not guessed.
_LANGUAGE_CODES = frozenset(
    {
        *_LANGUAGE_TAGS,
        "fr",
        "fra",
        "es",
        "spa",
        "it",
        "ita",
        "ru",
        "rus",
        "uk",
        "ua",
        "nl",
        "sv",
        "swe",
        "pt",
        "por",
        "hu",
        "ro",
        "bg",
        "hr",
        "sr",
        "sl",
        "lt",
        "lv",
        "et",
        "fi",
        "no",
        "da",
        "el",
        "tr",
        "ar",
        "he",
        "ja",
        "zh",
        "ko",
    }
)

_NOISE_WORDS = frozenset(
    {
        *_KNOWN_EXTENSIONS,
        "ebook",
        "ebooks",
        "e-book",
        "e-books",
        "book",
        "books",
        "ksiazka",
        "książka",
        "dokument",
    }
)

_EXTENSION_RE = re.compile(r"\.([A-Za-z0-9]{1,10})$")
_LEADING_BRACKET_RE = re.compile(r"^\s*[\[(]([^\]\)]*)[\])]\s*")
_TRAILING_BRACKET_RE = re.compile(r"\s*[\[(]([^\]\)]*)[\])]\s*$")
_AUTHOR_TITLE_SEPARATOR_RE = re.compile(r"\s+[-\u2013\u2014]\s+")
_TRAILING_PARENTHETICAL_RE = re.compile(r"^(.*\S)\s*\(([^()]+)\)\s*$")
_TOKEN_RE = re.compile(r"[a-z]+")
_WORD_TOKEN_RE = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class ParsedFile:
    """Title metadata parsed from a Chomikuj ``FileName``/``FileType`` pair."""

    title: str
    author: str | None
    format: str


def normalize_format(value: object) -> str:
    """Return a trimmed, lower-case format with at most one leading dot removed."""
    if not isinstance(value, str):
        return ""
    trimmed = value.strip().lower()
    if not trimmed:
        return ""
    return trimmed[1:].strip() if trimmed.startswith(".") else trimmed


def is_archive_extension(value: object) -> bool:
    """Return True when ``value`` is an archive extension that must never be offered."""
    return normalize_format(value) in ARCHIVE_EXTENSIONS


def _is_noise_tag(content: str) -> bool:
    """Return True when every token in a bracket tag is a format, language, or noise word."""
    normalized = content.strip().lower()
    if not normalized:
        return True
    if normalized in _NOISE_WORDS or normalized in _LANGUAGE_CODES:
        return True
    tokens = _WORD_TOKEN_RE.findall(normalized)
    if not tokens:
        return True
    return all(token in _NOISE_WORDS or token in _LANGUAGE_CODES for token in tokens)


def _strip_leading_noise(name: str) -> str:
    """Remove leading bracket tags that only name a format, language, or noise."""
    current = name.strip()
    while match := _LEADING_BRACKET_RE.match(current):
        if not _is_noise_tag(match.group(1)):
            break
        current = current[match.end() :].strip()
    return current


def _strip_trailing_noise(name: str) -> str:
    """Remove trailing bracket tags that only name a format, language, or noise."""
    current = name.strip()
    while match := _TRAILING_BRACKET_RE.search(current):
        if not _is_noise_tag(match.group(1)):
            break
        current = current[: match.start()].strip()
    return current


def _strip_repeated_known_extensions(name: str) -> str:
    """Remove trailing known extensions until a single one remains."""
    current = name.strip()
    while match := _EXTENSION_RE.search(current):
        if match.group(1).lower() not in _KNOWN_EXTENSIONS:
            break
        current = current[: match.start()].strip()
    return current


def _looks_like_author(value: str) -> bool:
    """Require a letter so years and bare edition numbers are not authors."""
    candidate = value.strip()
    return bool(candidate) and any(character.isalpha() for character in candidate)


def _split_author_title(name: str) -> tuple[str, str] | None:
    """Split an author-title pair into its title and author, or return None."""
    match = _AUTHOR_TITLE_SEPARATOR_RE.search(name)
    if match is None:
        return None
    author = name[: match.start()].strip()
    title = name[match.end() :].strip()
    if not _looks_like_author(author) or not title:
        return None
    return title, author


def _split_title_author(name: str) -> tuple[str, str] | None:
    """Split a title with a trailing parenthetical author, or return None."""
    match = _TRAILING_PARENTHETICAL_RE.match(name)
    if match is None:
        return None
    title = match.group(1).strip()
    author = match.group(2).strip()
    if not title or not _looks_like_author(author) or _is_noise_tag(author):
        return None
    return title, author


def parse_file_name(file_name: object, file_type: object) -> ParsedFile:
    """Parse a Chomikuj ``FileName``/``FileType`` pair into title metadata.

    Supported shapes::

        "Author - Title.epub"   -> author "Author", title "Title"
        "Title (Author).pdf"    -> author "Author", title "Title"
        "[PL] Title.epub"       -> author None, title "Title"
        "NoSeparator.mobi"      -> author None, title "NoSeparator"

    ``FileType`` wins over the filename extension only when it is non-empty.
    """
    raw = file_name.strip() if isinstance(file_name, str) else ""
    type_format = normalize_format(file_type)

    extension_match = _EXTENSION_RE.search(raw)
    extension = extension_match.group(1).lower() if extension_match else ""
    without_extension = raw[: extension_match.start()].strip() if extension_match else raw

    parsed_format = type_format or normalize_format(extension)

    stem = _strip_repeated_known_extensions(without_extension)
    stem = _strip_leading_noise(stem)
    stem = _strip_trailing_noise(stem).strip()

    author_title = _split_author_title(stem)
    if author_title is not None:
        title, author = author_title
        return ParsedFile(title=title, author=author, format=parsed_format)

    title_author = _split_title_author(stem)
    if title_author is not None:
        title, author = title_author
        return ParsedFile(title=title, author=author, format=parsed_format)

    return ParsedFile(title=stem, author=None, format=parsed_format)


def guess_language(file_name: object) -> str | None:
    """Return the single language tagged in ``file_name``, or ``None``.

    Tags are read from the filename stem, so ``"Ksiazka (PL).epub"`` yields
    ``"pl"``. Multiple distinct languages (``"Ksiazka PL + EN.epub"``), an
    unknown tag, or no tag at all yield ``None``; callers must never drop the
    row because of an unknown language.
    """
    if not isinstance(file_name, str):
        return None
    stem = PurePosixPath(file_name.strip()).stem.lower()
    found = {_LANGUAGE_TAGS[token] for token in _TOKEN_RE.findall(stem) if token in _LANGUAGE_TAGS}
    return found.pop() if len(found) == 1 else None

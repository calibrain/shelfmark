"""Tests for the Chomikuj filename/format parser and language heuristic.

Ported from a validated Chomikuj implementation's ``filename.test.mjs``, with
the Shelfmark adaptations from the work plan: ``kepub`` is not importable, a year
or bare number is never an author, and language tags follow the Webshare-style
heuristic instead of that implementation's hardcoded ``None``.
"""

import pytest

from shelfmark.release_sources.chomikuj.filename import (
    ARCHIVE_EXTENSIONS,
    IMPORTABLE_EBOOK_FORMATS,
    ParsedFile,
    guess_language,
    is_archive_extension,
    normalize_format,
    parse_file_name,
)

EXPECTED_IMPORTABLE = ("epub", "mobi", "azw3", "azw", "pdf", "fb2", "djvu")
EXPECTED_ARCHIVES = (
    "zip",
    "rar",
    "7z",
    "tar",
    "gz",
    "bz2",
    "xz",
    "cbz",
    "cbr",
    "cb7",
    "cbt",
)

PARSER_CASES = [
    pytest.param(
        "Author - Title.epub",
        None,
        ParsedFile(title="Title", author="Author", format="epub"),
        id="author-dash-title",
    ),
    pytest.param(
        "Synthetic Author - Testowa Książka",
        "epub",
        ParsedFile(title="Testowa Książka", author="Synthetic Author", format="epub"),
        id="extensionless-name-uses-file-type",
    ),
    pytest.param(
        "Title (Author).pdf",
        None,
        ParsedFile(title="Title", author="Author", format="pdf"),
        id="title-author-parenthetical",
    ),
    pytest.param(
        "[PL] Author - Tytuł.epub",
        None,
        ParsedFile(title="Tytuł", author="Author", format="epub"),
        id="leading-pl-tag",
    ),
    pytest.param(
        "NoSeparator.mobi",
        None,
        ParsedFile(title="NoSeparator", author=None, format="mobi"),
        id="no-separator",
    ),
    pytest.param(
        "Author - Title.epub.epub",
        None,
        ParsedFile(title="Title", author="Author", format="epub"),
        id="duplicated-extension",
    ),
    pytest.param(
        "Author - Comic Book.cbz",
        None,
        ParsedFile(title="Comic Book", author="Author", format="cbz"),
        id="archive-filename-still-parsed",
    ),
    pytest.param(
        "Author - Title",
        "PDF",
        ParsedFile(title="Title", author="Author", format="pdf"),
        id="uppercase-file-type",
    ),
    pytest.param(
        "Author - Title.epub",
        "MOBI",
        ParsedFile(title="Title", author="Author", format="mobi"),
        id="file-type-wins",
    ),
    pytest.param(
        "Author - Title.epub",
        "",
        ParsedFile(title="Title", author="Author", format="epub"),
        id="empty-file-type-falls-back-to-extension",
    ),
    pytest.param(
        "Title (Author) (ebook).epub",
        None,
        ParsedFile(title="Title", author="Author", format="epub"),
        id="trailing-ebook-noise",
    ),
    pytest.param(
        "[ENG][EBOOK] Author - Title.pdf",
        None,
        ParsedFile(title="Title", author="Author", format="pdf"),
        id="multiple-leading-tags",
    ),
    pytest.param(
        "Title (Author) [EPUB]",
        "epub",
        ParsedFile(title="Title", author="Author", format="epub"),
        id="trailing-format-tag",
    ),
    pytest.param(
        "[PL] Jan Kowalski - Żółć gęślą jaźń.fb2",
        None,
        ParsedFile(title="Żółć gęślą jaźń", author="Jan Kowalski", format="fb2"),
        id="polish-diacritics",
    ),
    pytest.param(
        "Zbrodnia i kara (Fiodor Dostojewski)",
        ".epub",
        ParsedFile(title="Zbrodnia i kara", author="Fiodor Dostojewski", format="epub"),
        id="dot-prefixed-file-type",
    ),
    pytest.param(
        "  Author - Title.azw3  ",
        None,
        ParsedFile(title="Title", author="Author", format="azw3"),
        id="surrounding-whitespace",
    ),
    pytest.param(
        "NoSeparator",
        None,
        ParsedFile(title="NoSeparator", author=None, format=""),
        id="no-format",
    ),
    pytest.param(
        "book.zip",
        None,
        ParsedFile(title="book", author=None, format="zip"),
        id="archive-no-author",
    ),
    pytest.param(
        "Title (2020).djvu",
        None,
        ParsedFile(title="Title (2020)", author=None, format="djvu"),
        id="year-not-author",
    ),
    pytest.param(
        "Author - Podtytuł - Tom 2.kepub",
        None,
        ParsedFile(title="Podtytuł - Tom 2", author="Author", format="kepub"),
        id="split-on-first-separator",
    ),
    pytest.param(
        "Title.epub.epub",
        None,
        ParsedFile(title="Title", author=None, format="epub"),
        id="repeated-extension-no-author",
    ),
    pytest.param(
        "Author - Title.EPUB",
        None,
        ParsedFile(title="Title", author="Author", format="epub"),
        id="uppercase-extension",
    ),
    pytest.param(
        "Author - Title.epub",
        ".mobi",
        ParsedFile(title="Title", author="Author", format="mobi"),
        id="dot-prefixed-file-type-wins",
    ),
]


def test_importable_formats_are_exact():
    assert IMPORTABLE_EBOOK_FORMATS == EXPECTED_IMPORTABLE
    assert "kepub" not in IMPORTABLE_EBOOK_FORMATS


def test_archive_extensions_are_exact():
    assert ARCHIVE_EXTENSIONS == EXPECTED_ARCHIVES


def test_importable_and_archive_sets_are_disjoint():
    assert not set(IMPORTABLE_EBOOK_FORMATS) & set(ARCHIVE_EXTENSIONS)
    assert "kepub" not in ARCHIVE_EXTENSIONS


def test_txt_doc_docx_are_rejected_by_omission():
    assert {"txt", "doc", "docx"}.isdisjoint(IMPORTABLE_EBOOK_FORMATS)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (".EPUB", "epub"),
        ("  MoBi ", "mobi"),
        ("azw3", "azw3"),
        ("", ""),
        ("   ", ""),
        (None, ""),
        (42, ""),
        (["epub"], ""),
    ],
)
def test_normalize_format(value, expected):
    assert normalize_format(value) == expected


def test_normalize_format_strips_only_one_leading_dot():
    assert normalize_format("..epub") == ".epub"


def test_is_archive_extension_every_member():
    assert all(is_archive_extension(extension) for extension in ARCHIVE_EXTENSIONS)


def test_is_archive_extension_normalizes_case_and_dot():
    assert is_archive_extension(".CBZ")
    assert is_archive_extension(" RAR ")
    assert is_archive_extension("7Z")


def test_is_archive_extension_false_for_other_formats():
    assert not is_archive_extension("epub")
    assert not is_archive_extension("pdf")
    assert not is_archive_extension("txt")
    assert not is_archive_extension(None)
    assert not is_archive_extension(42)


@pytest.mark.parametrize(("file_name", "file_type", "expected"), PARSER_CASES)
def test_parse_file_name(file_name, file_type, expected):
    assert parse_file_name(file_name, file_type) == expected


@pytest.mark.parametrize(
    ("file_name", "separator"),
    [
        ("Author - Title.epub", "-"),
        ("Author \u2013 Title.epub", "\u2013"),
        ("Author \u2014 Title.epub", "\u2014"),
    ],
)
def test_parse_file_name_dash_variants(file_name, separator):
    parsed = parse_file_name(file_name, None)
    assert parsed == ParsedFile(title="Title", author="Author", format="epub"), separator


@pytest.mark.parametrize(
    "file_name",
    ["2020 - Title.epub", "12345 - Title.pdf", "0000 - Title.mobi"],
)
def test_year_not_author(file_name):
    parsed = parse_file_name(file_name, None)
    assert parsed.author is None
    assert parsed.title.split(" - ", 1)[1].startswith("Title")


def test_year_is_allowed_as_a_title():
    assert parse_file_name("Author - 2020.epub", None) == ParsedFile(
        title="2020", author="Author", format="epub"
    )


def test_noise_only_name_strips_to_empty_title():
    assert parse_file_name("[PL] (ebook).epub", None) == ParsedFile(
        title="", author=None, format="epub"
    )


def test_parse_file_name_handles_non_string_input():
    assert parse_file_name(None, None) == ParsedFile(title="", author=None, format="")
    assert parse_file_name(42, "epub") == ParsedFile(title="", author=None, format="epub")


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("Ksiazka (PL).epub", "pl"),
        ("Book [ENG].pdf", "en"),
        ("Kniha (CZ).epub", "cs"),
        ("Kniha (SK).epub", "sk"),
        ("Buch (DE).epub", "de"),
        ("Ksiazka (polski).epub", "pl"),
        ("Book (angielski).pdf", "en"),
        ("Kniha (CESKY).epub", "cs"),
        ("Ksiazka PL + EN.epub", None),
        ("Ksiazka (PL) (EN).epub", None),
        ("Ksiazka.epub", None),
        ("Ksiazka (XYZ).epub", None),
        (None, None),
        (42, None),
    ],
)
def test_guess_language(file_name, expected):
    assert guess_language(file_name) == expected

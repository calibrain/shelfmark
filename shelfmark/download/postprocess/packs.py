"""Multi-book ("pack") release planning.

A pack is one release that contains several books: a whole-series torrent with one
subfolder per book, or a flat folder of `Series 1.0 - Title.m4b` files. The same
planning rules serve pre-download inspection (the file list comes from the release
source) and post-processing (the file list comes from disk), so what the user
approved in the modal is what gets filed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_YEAR_SUFFIX_RE = re.compile(r"\s*\(\s*(?P<year>\d{4})\s*\)\s*$")
_SERIES_MARKER_RE = re.compile(
    r"""
    ^\s*
    (?:
        \[\s*\#?(?P<bracket>\d+(?:\.\d+)?)\s*\]     # [03] / [#3]
      | \#(?P<hash>\d+(?:\.\d+)?)                    # #3
      | book\.?\s*(?P<book>\d+(?:\.\d+)?)            # Book 3 / Book. 03
      | (?P<plain>\d+(?:\.\d+)?)(?=[\s\-:.])         # 03 - / 1.0 - / 3.
    )
    \s*(?:[-:.]\s*)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SEPARATOR_CHARS = " \t-_:."
# "Gods of Risk 2.5 - Gods of Risk": the title repeated on both sides of the position.
_REPEATED_TITLE_RE = re.compile(
    r"^(?P<left>.+?)\s+(?P<position>\d+(?:\.\d+)?)\s*[-:\u2013]\s*(?P<right>.+)$"
)
_SERIES_LABEL_WORDS = r"(?:novella|novellas|short\s+story|short|story|novel)"


@dataclass(frozen=True)
class PackFile:
    """One file inside a release, path relative to the release root."""

    path: str
    size: int | None = None


@dataclass(frozen=True)
class PackBook:
    """One book split out of a pack, files as release-relative paths."""

    title: str
    series_position: float | None
    year: int | None
    files: list[str]


@dataclass(frozen=True)
class PackPlan:
    books: list[PackBook]
    ignored: list[str]

    @property
    def is_pack(self) -> bool:
        return len(self.books) > 1


@dataclass(frozen=True)
class BookGroup:
    """One book's on-disk files, ready for transfer."""

    title: str
    series_position: float | None
    year: int | None
    files: list[Path]


def _strip_series_name(name: str, series_name: str | None) -> str:
    if not series_name:
        return name
    prefix = series_name.strip()
    if not prefix or not name.lower().startswith(prefix.lower()):
        return name
    remainder = name[len(prefix) :]
    if remainder and remainder[0].isalnum():
        return name
    return remainder.lstrip(_SEPARATOR_CHARS)


def _strip_series_label(work: str, series_name: str | None) -> str:
    """Drop a leading "An <Series> Novella - " style label that some packs prepend."""
    if not series_name:
        return work
    # "The Expanse" is labelled "An Expanse Novella", so match without the article.
    core = re.sub(r"^(?:the|an?)\s+", "", series_name.strip(), flags=re.IGNORECASE)
    if not core:
        return work
    pattern = re.compile(
        rf"^(?:an?\s+|the\s+)?{re.escape(core)}\s+{_SERIES_LABEL_WORDS}\s*[-:\u2013]\s*",
        re.IGNORECASE,
    )
    return pattern.sub("", work, count=1)


def parse_pack_book_name(
    name: str, *, series_name: str | None
) -> tuple[str, float | None, int | None]:
    """Split a book folder/file-stem name into (title, series position, year).

    Strips a leading series name, a leading position marker (`Book 3 - `, `03 - `,
    `1.0 - `, `3. `, `[03] `, `#3 `) and a trailing `(YYYY)`. Returns the name
    unchanged with no position/year when nothing would be left of the title.
    """
    work = _strip_series_name(name.strip(), series_name)

    year: int | None = None
    year_match = _YEAR_SUFFIX_RE.search(work)
    if year_match:
        year = int(year_match.group("year"))
        work = work[: year_match.start()]

    position: float | None = None
    repeated = _REPEATED_TITLE_RE.match(work.strip())
    if (
        repeated
        and repeated.group("left").strip().lower() == repeated.group("right").strip().lower()
    ):
        return repeated.group("right").strip(), float(repeated.group("position")), year

    marker = _SERIES_MARKER_RE.match(work)
    if marker:
        raw = (
            marker.group("bracket")
            or marker.group("hash")
            or marker.group("book")
            or marker.group("plain")
        )
        position = float(raw)
        work = work[marker.end() :]

    work = _strip_series_label(work, series_name)
    title = work.strip().strip(_SEPARATOR_CHARS).strip()
    if not title:
        return name, None, None
    return title, position, year


def _book_from_name(name: str, files: list[str], series_name: str | None) -> PackBook:
    title, position, year = parse_pack_book_name(name, series_name=series_name)
    return PackBook(title=title, series_position=position, year=year, files=files)


def _common_root_parts(paths: list[PurePosixPath]) -> tuple[str, ...]:
    parents = [p.parent.parts for p in paths]
    common: list[str] = []
    for parts in zip(*parents, strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return tuple(common)


def plan_pack(
    files: list[PackFile],
    *,
    supported_extensions: set[str],
    series_name: str | None,
    root_depth: int | None = None,
) -> PackPlan:
    """Group a release's file list into books.

    Files in a subfolder (relative to the common root) group by that subfolder. Files
    directly in the root split one-book-per-file only when at least two of them carry
    a series position in their names; otherwise they are one book (a chaptered
    audiobook, e.g. `01.mp3`, `02.mp3`). `root_depth` fixes how many leading path
    components form the root instead of deriving it from the files' common parent.
    """
    supported = {ext.lower().lstrip(".") for ext in supported_extensions}
    book_files: list[PurePosixPath] = []
    ignored: list[str] = []
    for pack_file in files:
        rel = PurePosixPath(pack_file.path.replace("\\", "/").lstrip("./"))
        if rel.suffix.lower().lstrip(".") in supported:
            book_files.append(rel)
        else:
            ignored.append(pack_file.path)

    if not book_files:
        return PackPlan(books=[], ignored=ignored)

    root_parts = (
        _common_root_parts(book_files) if root_depth is None else book_files[0].parts[:root_depth]
    )
    depth = len(root_parts)

    root_files: list[PurePosixPath] = []
    folders: dict[str, list[str]] = {}
    for rel in book_files:
        remainder = rel.parts[depth:]
        if len(remainder) > 1:
            folders.setdefault(remainder[0], []).append(str(rel))
        else:
            root_files.append(rel)

    books: list[PackBook] = []
    if root_files:
        parsed = [parse_pack_book_name(f.stem, series_name=series_name) for f in root_files]
        positions = {p[1] for p in parsed if p[1] is not None}
        if len(positions) >= 2:
            books.extend(
                PackBook(title=title, series_position=position, year=year, files=[str(f)])
                for f, (title, position, year) in zip(root_files, parsed, strict=True)
            )
        elif len(root_files) == 1:
            books.append(_book_from_name(root_files[0].stem, [str(root_files[0])], series_name))
        else:
            group_name = root_parts[-1] if root_parts else ""
            books.append(_book_from_name(group_name, [str(f) for f in root_files], series_name))

    books.extend(_book_from_name(folder, paths, series_name) for folder, paths in folders.items())
    return PackPlan(books=books, ignored=ignored)


def _relative_paths(
    book_files: list[Path], root: Path | None = None
) -> tuple[Path, dict[Path, str]]:
    if root is None:
        root = Path(os.path.commonpath([str(f.parent) for f in book_files]))
    return root, {f: f.relative_to(root).as_posix() for f in book_files}


def group_files_into_books(
    book_files: list[Path],
    *,
    series_name: str | None,
    root: Path | None = None,
) -> list[BookGroup]:
    """Heuristically split on-disk files into books (see `plan_pack`).

    `root` pins the release root when grouping a subset of a larger file set.
    """
    if not book_files:
        return []
    _root, rel_by_path = _relative_paths(book_files, root)
    path_by_rel = {rel: path for path, rel in rel_by_path.items()}
    extensions = {f.suffix.lower().lstrip(".") for f in book_files}
    plan = plan_pack(
        [PackFile(rel) for rel in rel_by_path.values()],
        supported_extensions=extensions,
        series_name=series_name,
        root_depth=None if root is None else 0,
    )
    return [
        BookGroup(
            title=book.title,
            series_position=book.series_position,
            year=book.year,
            files=[path_by_rel[rel] for rel in book.files],
        )
        for book in plan.books
    ]


def match_plan_to_files(
    plan: list[PackBook],
    book_files: list[Path],
    *,
    series_name: str | None = None,
) -> list[BookGroup]:
    """Apply an approved plan to on-disk files.

    Files match by release-relative path first, then by basename (archive extraction
    and client save paths can shift the root). Book files the plan does not mention
    fall back to heuristic grouping so nothing is silently dropped.
    """
    if not book_files:
        return []
    root, rel_by_path = _relative_paths(book_files)
    by_rel = {rel: path for path, rel in rel_by_path.items()}
    by_name: dict[str, list[Path]] = {}
    for path in book_files:
        by_name.setdefault(path.name, []).append(path)

    claimed: set[Path] = set()
    groups: list[BookGroup] = []
    for book in plan:
        matched: list[Path] = []
        for wanted in book.files:
            wanted_rel = wanted.replace("\\", "/").lstrip("./")
            candidate = by_rel.get(wanted_rel)
            if candidate is None:
                candidates = [
                    p for p in by_name.get(PurePosixPath(wanted_rel).name, []) if p not in claimed
                ]
                candidate = candidates[0] if candidates else None
            if candidate is not None and candidate not in claimed:
                claimed.add(candidate)
                matched.append(candidate)
        if matched:
            groups.append(
                BookGroup(
                    title=book.title,
                    series_position=book.series_position,
                    year=book.year,
                    files=matched,
                )
            )

    unmatched = [p for p in book_files if p not in claimed]
    if unmatched:
        groups.extend(group_files_into_books(unmatched, series_name=series_name, root=root))
    return groups

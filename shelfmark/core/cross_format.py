"""Cross-format markers: name each work held as both an ebook and an audiobook.

A reader browsing Audiobookshelf cannot see that the ebook of the same work sits in
Calibre, nor the other way round. This module finds the pairs and marks the copies.

Pairs come from the library check's matcher (``library_index.matching_entries``): a
Calibre book and an Audiobookshelf item are a pair when either one, asked for by its
own title, author and identifiers, is found in the other library as the same work held
on its own. That carries every rule the ownership check applies: a series label or a
numbered parenthetical in front of a title is the series, not another work; a
different edition ("Full Cast Edition", "Dramatized Adaptation") is a different
recording and not a pair; an omnibus contains the work but is not its copy.

Only the Audiobookshelf side is written, as one tag on each paired audiobook. The
Calibre side is reported through the admin endpoint for a separate script to act on:
Shelfmark reads ``metadata.db`` and never writes it. Both libraries must be readable
before anything is written, since an empty index would look like "no pairs" and strip
every tag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core import library_index
from shelfmark.core.config import config as app_config
from shelfmark.core.library_providers import all_providers, setting
from shelfmark.core.library_providers.audiobookshelf import (
    AudiobookshelfForbiddenError,
    AudiobookshelfTagClient,
)
from shelfmark.core.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from shelfmark.core.library_providers import LibraryEntry

logger = setup_logger(__name__)

DEFAULT_TAG = "ebook-in-library"


class CrossFormatNotConfiguredError(ValueError):
    """Both library checks must be enabled before pairs can be computed."""


class CrossFormatDisabledError(ValueError):
    """Writing Audiobookshelf tags is off; only a dry run is allowed."""


@dataclass(frozen=True)
class Pair:
    ebook: LibraryEntry
    audiobook: LibraryEntry


@dataclass(frozen=True)
class TagChange:
    """One Audiobookshelf item whose tag list the index says is out of date."""

    item_id: str
    title: str
    action: str  # "add" or "remove"


def compute_pairs(ebooks: list[LibraryEntry], audiobooks: list[LibraryEntry]) -> list[Pair]:
    """Every (ebook, audiobook) that is the same work, in library order.

    Each library asks for its books in the other, and the union is kept. One direction
    alone misses pairs, because the text rules read the query's words as the ground
    truth: "Kiss the Girls" finds "Alex Cross 02: Kiss the Girls" (the shelf's extra
    words are its series label), while the reverse query carries the label's words
    and fails the overlap test.
    """
    pairs: dict[tuple[str, str], Pair] = {}
    for ebook in ebooks:
        for audiobook in library_index.matching_entries(ebook, audiobooks):
            pairs.setdefault((ebook.item_id, audiobook.item_id), Pair(ebook, audiobook))
    for audiobook in audiobooks:
        for ebook in library_index.matching_entries(audiobook, ebooks):
            pairs.setdefault((ebook.item_id, audiobook.item_id), Pair(ebook, audiobook))
    return list(pairs.values())


def tag_name(overrides: Mapping[str, Any] | None = None) -> str:
    name = str(setting(app_config, "CROSS_FORMAT_ABS_TAG", DEFAULT_TAG, overrides) or "").strip()
    return name or DEFAULT_TAG


def tagging_enabled() -> bool:
    return bool(app_config.get("CROSS_FORMAT_ABS_TAGGING_ENABLED", False))


def _libraries(
    overrides: Mapping[str, Any] | None = None,
) -> tuple[list[LibraryEntry], list[LibraryEntry]]:
    """Both indexes, strictly: an unreadable library raises rather than reading as empty."""
    providers = {p.name: p for p in all_providers(overrides) if p.is_enabled()}
    calibre = providers.get("calibre")
    audiobookshelf = providers.get("audiobookshelf")
    if calibre is None or audiobookshelf is None:
        raise CrossFormatNotConfiguredError(
            "Cross-format markers need both library checks enabled: Calibre and Audiobookshelf."
        )
    return (
        library_index.entries_for_provider(calibre, strict=True),
        library_index.entries_for_provider(audiobookshelf, strict=True),
    )


def _describe(entry: LibraryEntry) -> dict[str, str]:
    return {"id": entry.item_id, "title": entry.title, "author": entry.author or ""}


def pairs_report(overrides: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The pairs as the admin endpoint reports them: flat, and indexed from each side.

    ``by_calibre_id`` answers "which audiobook(s) match Calibre book N" for a script
    marking the Calibre side; ``by_audiobookshelf_id`` is the reverse.
    """
    ebooks, audiobooks = _libraries(overrides)
    pairs = compute_pairs(ebooks, audiobooks)
    by_calibre: dict[str, list[dict[str, str]]] = {}
    by_audiobookshelf: dict[str, list[dict[str, str]]] = {}
    for pair in pairs:
        by_calibre.setdefault(pair.ebook.item_id, []).append(_describe(pair.audiobook))
        by_audiobookshelf.setdefault(pair.audiobook.item_id, []).append(_describe(pair.ebook))
    return {
        "ebooks_indexed": len(ebooks),
        "audiobooks_indexed": len(audiobooks),
        "pair_count": len(pairs),
        "pairs": [
            {"ebook": _describe(pair.ebook), "audiobook": _describe(pair.audiobook)}
            for pair in pairs
        ],
        "by_calibre_id": by_calibre,
        "by_audiobookshelf_id": by_audiobookshelf,
    }


def plan_tag_changes(
    audiobooks: list[LibraryEntry], pairs: list[Pair], tag: str
) -> list[TagChange]:
    """Which audiobooks need ``tag`` added or removed, going by the cached index.

    Only this one tag is ever touched: a paired item without it gets it, an unpaired
    item carrying it loses it, and everything else on the item is left alone.
    """
    paired = {pair.audiobook.item_id for pair in pairs}
    changes: list[TagChange] = []
    for audiobook in audiobooks:
        tagged = tag in audiobook.tags
        if audiobook.item_id in paired and not tagged:
            changes.append(TagChange(audiobook.item_id, audiobook.title, "add"))
        elif audiobook.item_id not in paired and tagged:
            changes.append(TagChange(audiobook.item_id, audiobook.title, "remove"))
    return changes


def apply_tag(current: list[str], tag: str, action: str) -> list[str]:
    """``current`` with ``tag`` added at the end or removed; other tags keep their order."""
    if action == "add":
        return current if tag in current else [*current, tag]
    return [existing for existing in current if existing != tag]


def _summary(dry_run: bool, tag: str, pairs: int, ebooks: int, audiobooks: int) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "tag": tag,
        "pairs": pairs,
        "ebooks_indexed": ebooks,
        "audiobooks_indexed": audiobooks,
        "added": [],
        "removed": [],
        "already_current": 0,
        "errors": 0,
        "forbidden": False,
    }


def sync_audiobookshelf_tags(
    *,
    dry_run: bool,
    overrides: Mapping[str, Any] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Bring the tag on every Audiobookshelf item in line with the pairs.

    A dry run only reports what would change. A real run needs the setting on, reads
    each item's current tags from Audiobookshelf just before writing (the index may be
    minutes old) and writes only when that fresh list still needs the change. A 403
    ends the run at once, since the token cannot update anything; any other failure
    on one item is counted and the rest continue. ``should_stop`` is polled between
    items so a scheduler cycle out of time can hand back early.
    """
    if not dry_run and not tagging_enabled():
        raise CrossFormatDisabledError(
            "Audiobookshelf tagging is off. Turn on 'Tag audiobooks whose ebook is in "
            "Calibre' and save, or run a dry run."
        )
    tag = tag_name(overrides)
    ebooks, audiobooks = _libraries(overrides)
    pairs = compute_pairs(ebooks, audiobooks)
    changes = plan_tag_changes(audiobooks, pairs, tag)
    summary = _summary(dry_run, tag, len(pairs), len(ebooks), len(audiobooks))

    if dry_run:
        for change in changes:
            summary["added" if change.action == "add" else "removed"].append(
                {"id": change.item_id, "title": change.title}
            )
        logger.info(
            "cross-format: dry run; %d pair(s); '%s' would be added to %d and removed from "
            "%d of %d audiobook(s)",
            len(pairs),
            tag,
            len(summary["added"]),
            len(summary["removed"]),
            len(audiobooks),
        )
        return summary

    client = AudiobookshelfTagClient(overrides)
    for change in changes:
        if should_stop is not None and should_stop():
            logger.warning("cross-format: out of time; stopping with changes left to make")
            summary["errors"] += 1
            break
        try:
            current = client.tags(change.item_id)
            wanted = apply_tag(current, tag, change.action)
            if wanted == current:
                summary["already_current"] += 1
                continue
            client.set_tags(change.item_id, wanted)
        except AudiobookshelfForbiddenError as exc:
            logger.warning("cross-format: %s", exc)
            summary["forbidden"] = True
            summary["errors"] += 1
            summary["error"] = str(exc)
            break
        except Exception as exc:  # noqa: BLE001 - one item's failure must not stop the rest
            logger.warning(
                "cross-format: could not update '%s' (%s): %s", change.title, change.item_id, exc
            )
            summary["errors"] += 1
            continue
        verb = "added to" if change.action == "add" else "removed from"
        logger.info("cross-format: '%s' %s '%s' (%s)", tag, verb, change.title, change.item_id)
        summary["added" if change.action == "add" else "removed"].append(
            {"id": change.item_id, "title": change.title}
        )

    logger.info(
        "cross-format: %d pair(s); '%s' added to %d, removed from %d, %d already current, "
        "%d error(s)",
        len(pairs),
        tag,
        len(summary["added"]),
        len(summary["removed"]),
        summary["already_current"],
        summary["errors"],
    )
    return summary


def describe_summary(summary: dict[str, Any]) -> str:
    """One sentence for the settings button and the cycle log."""
    verb = "would be added to" if summary["dry_run"] else "added to"
    text = (
        f"{summary['pairs']} pair(s) across {summary['ebooks_indexed']} ebook(s) and "
        f"{summary['audiobooks_indexed']} audiobook(s); '{summary['tag']}' {verb} "
        f"{len(summary['added'])} and removed from {len(summary['removed'])}."
    )
    if summary.get("forbidden"):
        text += f" Stopped: {summary.get('error', 'Audiobookshelf refused the update.')}"
    elif summary["errors"]:
        text += f" {summary['errors']} item(s) could not be updated; see the log."
    return text

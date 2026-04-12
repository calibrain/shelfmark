"""Request auto-selection settings and release choice helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_policy import normalize_content_type, normalize_source
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release, SourceUnavailableError

logger = setup_logger(__name__)

REQUEST_AUTO_SELECTION_KEYS = frozenset(
    {
        "REQUEST_AUTO_SELECT_ENABLED",
        "REQUEST_AUTO_APPROVE_ENABLED",
        "REQUEST_AUTO_PREFERRED_SOURCE",
        "REQUEST_AUTO_PREFERRED_INDEXER",
        "REQUEST_AUTO_CONTENT_TYPES",
        "REQUEST_AUTO_FORMATS",
        "REQUEST_AUTO_SELECTION_POLICY",
        "REQUEST_AUTO_FALLBACK_STRATEGY",
    }
)

REQUEST_AUTO_SUPPORTED_CONTENT_TYPES = ("ebook", "audiobook")
REQUEST_AUTO_KNOWN_FORMATS = (
    "epub",
    "mobi",
    "azw3",
    "azw",
    "pdf",
    "cbz",
    "cbr",
    "fb2",
    "djvu",
    "lit",
    "pdb",
    "txt",
    "doc",
    "docx",
    "rtf",
    "html",
    "htm",
    "zip",
    "rar",
    "m4b",
    "mp3",
    "m4a",
    "flac",
    "ogg",
    "wma",
    "aac",
    "wav",
    "opus",
)
_KNOWN_FORMAT_SET = set(REQUEST_AUTO_KNOWN_FORMATS)
_AUDIOBOOK_FORMAT_HINTS = frozenset(
    {"m4b", "mp3", "m4a", "flac", "ogg", "wma", "aac", "wav", "opus"}
)
_AUDIOBOOK_CATEGORY_RANGE = (3030, 3049)
_SOURCE_SEARCH_ERRORS = (AttributeError, OSError, RuntimeError, TypeError, ValueError)


class RequestAutoSelectionPolicy(StrEnum):
    """Explicit release ranking strategies."""

    BEST_MATCH = "best_match"
    MOST_SEEDERS = "most_seeders"
    BEST_AVAILABILITY = "best_availability"
    NEWEST = "newest"


class RequestAutoFallbackStrategy(StrEnum):
    """How far automatic source fallback is allowed to expand."""

    SAME_SOURCE = "same_source"
    SAME_SOURCE_THEN_ANY_SOURCE = "same_source_then_any_source"


@dataclass(frozen=True)
class RequestAutoSelectionSettings:
    """Effective auto-selection settings for one user/request context."""

    enabled: bool = False
    auto_approve_enabled: bool = False
    preferred_source: str | None = None
    preferred_indexer: str | None = None
    content_types: tuple[str, ...] = REQUEST_AUTO_SUPPORTED_CONTENT_TYPES
    preferred_formats: tuple[str, ...] = ()
    selection_policy: RequestAutoSelectionPolicy = RequestAutoSelectionPolicy.BEST_MATCH
    fallback_strategy: RequestAutoFallbackStrategy = RequestAutoFallbackStrategy.SAME_SOURCE

    def supports_content_type(self, content_type: str) -> bool:
        return content_type in self.content_types


@dataclass(frozen=True)
class RequestAutoSelectionMatch:
    """A selected release plus the audit context used to choose it."""

    release: Release
    release_data: dict[str, Any]
    reason: str
    stage: str


@dataclass(frozen=True)
class RequestAutoSelectionResult:
    """Result of attempting to auto-select a release for a book-level request."""

    settings: RequestAutoSelectionSettings
    attempted: bool
    selected: RequestAutoSelectionMatch | None = None
    searched_sources: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    fallback_reason: str | None = None


@dataclass(frozen=True)
class _SearchStage:
    key: str
    sources: tuple[str, ...]
    indexers: tuple[str, ...] | None = None


def _normalize_bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _parse_content_type_override(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"ebook", "book", "books", "book (fiction)"}:
        return "ebook"
    if normalized in {"audiobook", "audiobooks", "audio", "book (audiobook)"}:
        return "audiobook"
    return None


def parse_request_auto_selection_policy(
    value: object,
) -> RequestAutoSelectionPolicy | None:
    if isinstance(value, RequestAutoSelectionPolicy):
        return value
    if not isinstance(value, str):
        return None
    try:
        return RequestAutoSelectionPolicy(value.strip().lower())
    except ValueError:
        return None


def parse_request_auto_fallback_strategy(
    value: object,
) -> RequestAutoFallbackStrategy | None:
    if isinstance(value, RequestAutoFallbackStrategy):
        return value
    if not isinstance(value, str):
        return None
    try:
        return RequestAutoFallbackStrategy(value.strip().lower())
    except ValueError:
        return None


def _normalize_content_type_list(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None

    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_values = [str(part).strip() for part in value if str(part).strip()]
    else:
        return None

    normalized: list[str] = []
    for raw_value in raw_values:
        content_type = _parse_content_type_override(raw_value)
        if content_type is None:
            return None
        if content_type not in normalized:
            normalized.append(content_type)
    return tuple(normalized)


def _normalize_format_list(value: object) -> tuple[str, ...] | None:
    if value is None:
        return None

    if isinstance(value, str):
        raw_values = [part.strip() for part in value.split(",") if part.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_values = [str(part).strip() for part in value if str(part).strip()]
    else:
        return None

    normalized: list[str] = []
    for raw_value in raw_values:
        fmt = raw_value.lower()
        if fmt in _KNOWN_FORMAT_SET and fmt not in normalized:
            normalized.append(fmt)
    return tuple(normalized)


def _parse_publish_year(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
    return None


def _split_authors(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def get_available_request_auto_sources() -> set[str]:
    """Return registered release source identifiers."""
    from shelfmark.release_sources import list_available_sources

    return {
        str(source.get("name")).strip()
        for source in list_available_sources()
        if str(source.get("name") or "").strip()
    }


def get_available_request_auto_indexers() -> list[str]:
    """Return enabled Prowlarr indexer names when they can be loaded."""
    from shelfmark.release_sources import get_source

    try:
        source = get_source("prowlarr")
        column_config = source.get_column_config()
    except (SourceUnavailableError, ValueError, *_SOURCE_SEARCH_ERRORS):
        return []

    raw_indexers = getattr(column_config, "available_indexers", None) or []
    normalized: list[str] = []
    for value in raw_indexers:
        indexer = _normalize_optional_text(value)
        if indexer and indexer not in normalized:
            normalized.append(indexer)
    return sorted(normalized)


def validate_request_auto_selection_settings(
    values: Mapping[str, object],
) -> tuple[dict[str, object], list[str]]:
    """Validate and normalize request auto-selection settings."""
    normalized: dict[str, object] = {}
    errors: list[str] = []

    if "REQUEST_AUTO_SELECT_ENABLED" in values:
        normalized["REQUEST_AUTO_SELECT_ENABLED"] = _normalize_bool(
            values.get("REQUEST_AUTO_SELECT_ENABLED"),
            default=False,
        )

    if "REQUEST_AUTO_APPROVE_ENABLED" in values:
        normalized["REQUEST_AUTO_APPROVE_ENABLED"] = _normalize_bool(
            values.get("REQUEST_AUTO_APPROVE_ENABLED"),
            default=False,
        )

    source_options = get_available_request_auto_sources()
    preferred_source = None
    if "REQUEST_AUTO_PREFERRED_SOURCE" in values:
        preferred_source = normalize_source(values.get("REQUEST_AUTO_PREFERRED_SOURCE"))
        if preferred_source == "*":
            preferred_source = ""
        if preferred_source and preferred_source not in source_options:
            errors.append("REQUEST_AUTO_PREFERRED_SOURCE must be a valid release source name or empty")
        normalized["REQUEST_AUTO_PREFERRED_SOURCE"] = preferred_source

    preferred_indexer = None
    if "REQUEST_AUTO_PREFERRED_INDEXER" in values:
        preferred_indexer = _normalize_optional_text(values.get("REQUEST_AUTO_PREFERRED_INDEXER")) or ""
        normalized["REQUEST_AUTO_PREFERRED_INDEXER"] = preferred_indexer

    if "REQUEST_AUTO_CONTENT_TYPES" in values:
        content_types = _normalize_content_type_list(values.get("REQUEST_AUTO_CONTENT_TYPES"))
        if content_types is None:
            errors.append("REQUEST_AUTO_CONTENT_TYPES must be a list of content types")
        else:
            normalized["REQUEST_AUTO_CONTENT_TYPES"] = list(content_types)

    if "REQUEST_AUTO_FORMATS" in values:
        formats = _normalize_format_list(values.get("REQUEST_AUTO_FORMATS"))
        if formats is None:
            errors.append("REQUEST_AUTO_FORMATS must be a list of file formats")
        else:
            raw_values = values.get("REQUEST_AUTO_FORMATS")
            invalid_formats: list[str] = []
            if isinstance(raw_values, str):
                raw_iterable = [part.strip() for part in raw_values.split(",") if part.strip()]
            elif isinstance(raw_values, (list, tuple, set)):
                raw_iterable = [str(part).strip() for part in raw_values if str(part).strip()]
            else:
                raw_iterable = []
            for raw_value in raw_iterable:
                if raw_value.lower() not in _KNOWN_FORMAT_SET:
                    invalid_formats.append(raw_value)
            if invalid_formats:
                errors.append(
                    "REQUEST_AUTO_FORMATS contains unsupported format(s): "
                    + ", ".join(sorted(invalid_formats))
                )
            normalized["REQUEST_AUTO_FORMATS"] = list(formats)

    if "REQUEST_AUTO_SELECTION_POLICY" in values:
        policy = parse_request_auto_selection_policy(values.get("REQUEST_AUTO_SELECTION_POLICY"))
        if policy is None:
            errors.append(
                "REQUEST_AUTO_SELECTION_POLICY must be one of: "
                + ", ".join(policy.value for policy in RequestAutoSelectionPolicy)
            )
        else:
            normalized["REQUEST_AUTO_SELECTION_POLICY"] = policy.value

    if "REQUEST_AUTO_FALLBACK_STRATEGY" in values:
        fallback = parse_request_auto_fallback_strategy(values.get("REQUEST_AUTO_FALLBACK_STRATEGY"))
        if fallback is None:
            errors.append(
                "REQUEST_AUTO_FALLBACK_STRATEGY must be one of: "
                + ", ".join(value.value for value in RequestAutoFallbackStrategy)
            )
        else:
            normalized["REQUEST_AUTO_FALLBACK_STRATEGY"] = fallback.value

    effective_source = preferred_source
    if effective_source is None:
        raw_source = values.get("REQUEST_AUTO_PREFERRED_SOURCE")
        if raw_source is not None:
            normalized_source = normalize_source(raw_source)
            effective_source = "" if normalized_source == "*" else normalized_source

    if preferred_indexer and effective_source not in {"prowlarr", None}:
        errors.append("REQUEST_AUTO_PREFERRED_INDEXER is only supported when REQUEST_AUTO_PREFERRED_SOURCE is Prowlarr")
    elif preferred_indexer and effective_source == "prowlarr":
        available_indexers = get_available_request_auto_indexers()
        if available_indexers and preferred_indexer not in available_indexers:
            errors.append("REQUEST_AUTO_PREFERRED_INDEXER must be an enabled Prowlarr indexer name")

    return normalized, errors


def _resolve_default_release_source_for_content_type(
    content_type: str,
    *,
    user_id: int | None,
) -> str | None:
    if content_type == "audiobook":
        audiobook_source = normalize_source(
            app_config.get("DEFAULT_RELEASE_SOURCE_AUDIOBOOK", "", user_id=user_id)
        )
        if audiobook_source not in {"", "*"}:
            return audiobook_source

    source = normalize_source(app_config.get("DEFAULT_RELEASE_SOURCE", "", user_id=user_id))
    if source in {"", "*"}:
        return None
    return source


def resolve_request_auto_selection_settings(
    *,
    user_id: int | None,
) -> RequestAutoSelectionSettings:
    """Resolve effective request auto-selection settings for one user."""
    content_types = _normalize_content_type_list(
        app_config.get("REQUEST_AUTO_CONTENT_TYPES", list(REQUEST_AUTO_SUPPORTED_CONTENT_TYPES), user_id=user_id)
    ) or REQUEST_AUTO_SUPPORTED_CONTENT_TYPES
    formats = _normalize_format_list(
        app_config.get("REQUEST_AUTO_FORMATS", [], user_id=user_id)
    ) or ()

    preferred_source_raw = normalize_source(
        app_config.get("REQUEST_AUTO_PREFERRED_SOURCE", "", user_id=user_id)
    )
    preferred_source = None if preferred_source_raw in {"", "*"} else preferred_source_raw
    if preferred_source and preferred_source not in get_available_request_auto_sources():
        preferred_source = None

    preferred_indexer = _normalize_optional_text(
        app_config.get("REQUEST_AUTO_PREFERRED_INDEXER", "", user_id=user_id)
    )
    if preferred_source != "prowlarr":
        preferred_indexer = None

    selection_policy = parse_request_auto_selection_policy(
        app_config.get("REQUEST_AUTO_SELECTION_POLICY", RequestAutoSelectionPolicy.BEST_MATCH.value, user_id=user_id)
    ) or RequestAutoSelectionPolicy.BEST_MATCH
    fallback_strategy = parse_request_auto_fallback_strategy(
        app_config.get(
            "REQUEST_AUTO_FALLBACK_STRATEGY",
            RequestAutoFallbackStrategy.SAME_SOURCE.value,
            user_id=user_id,
        )
    ) or RequestAutoFallbackStrategy.SAME_SOURCE

    return RequestAutoSelectionSettings(
        enabled=_normalize_bool(
            app_config.get("REQUEST_AUTO_SELECT_ENABLED", False, user_id=user_id),
            default=False,
        ),
        auto_approve_enabled=_normalize_bool(
            app_config.get("REQUEST_AUTO_APPROVE_ENABLED", False, user_id=user_id),
            default=False,
        ),
        preferred_source=preferred_source,
        preferred_indexer=preferred_indexer,
        content_types=tuple(content_types),
        preferred_formats=tuple(formats),
        selection_policy=selection_policy,
        fallback_strategy=fallback_strategy,
    )


def _build_book_metadata(book_data: Mapping[str, Any], *, content_type: str) -> BookMetadata:
    title = _normalize_optional_text(book_data.get("title")) or "Unknown title"
    author = _normalize_optional_text(book_data.get("author"))
    authors = _split_authors(book_data.get("authors") or author)
    return BookMetadata(
        provider=_normalize_optional_text(book_data.get("provider")) or "request",
        provider_id=_normalize_optional_text(book_data.get("provider_id")) or title,
        provider_display_name=_normalize_optional_text(book_data.get("provider_display_name")),
        title=title,
        search_title=_normalize_optional_text(book_data.get("search_title")) or title,
        search_author=_normalize_optional_text(book_data.get("search_author")) or author,
        authors=authors,
        isbn_10=_normalize_optional_text(book_data.get("isbn_10")),
        isbn_13=_normalize_optional_text(book_data.get("isbn_13")),
        cover_url=_normalize_optional_text(book_data.get("preview")),
        description=_normalize_optional_text(book_data.get("description")),
        publisher=_normalize_optional_text(book_data.get("publisher")),
        publish_year=_parse_publish_year(book_data.get("year")),
        language=_normalize_optional_text(book_data.get("language")),
        source_url=_normalize_optional_text(book_data.get("source_url")),
        subtitle=_normalize_optional_text(book_data.get("subtitle")),
        series_name=_normalize_optional_text(book_data.get("series_name")),
        series_position=(
            float(book_data["series_position"])
            if isinstance(book_data.get("series_position"), (int, float))
            else None
        ),
        series_count=(
            int(book_data["series_count"])
            if isinstance(book_data.get("series_count"), int)
            else None
        ),
        titles_by_language=(
            dict(book_data["titles_by_language"])
            if isinstance(book_data.get("titles_by_language"), Mapping)
            else {}
        ),
    )


def _list_enabled_sources_for_content_type(content_type: str) -> list[str]:
    from shelfmark.release_sources import list_available_sources

    return [
        str(source["name"])
        for source in list_available_sources()
        if source.get("enabled")
        and content_type in source.get("supported_content_types", list(REQUEST_AUTO_SUPPORTED_CONTENT_TYPES))
    ]


def _build_search_stages(
    *,
    source_hint: str,
    content_type: str,
    settings: RequestAutoSelectionSettings,
    user_id: int | None,
    allowed_sources: tuple[str, ...] | None = None,
) -> list[_SearchStage]:
    enabled_sources = _list_enabled_sources_for_content_type(content_type)
    if allowed_sources is not None:
        allowed_source_set = set(allowed_sources)
        enabled_sources = [source for source in enabled_sources if source in allowed_source_set]
    if not enabled_sources:
        return []

    explicit_source = normalize_source(source_hint)
    explicit_source = None if explicit_source in {"", "*"} else explicit_source
    if explicit_source and explicit_source not in enabled_sources:
        explicit_source = None

    base_source = explicit_source
    if base_source is None and settings.preferred_source in enabled_sources:
        base_source = settings.preferred_source
    if base_source is None:
        default_source = _resolve_default_release_source_for_content_type(
            content_type,
            user_id=user_id,
        )
        if default_source in enabled_sources:
            base_source = default_source

    stages: list[_SearchStage] = []
    if base_source:
        if settings.preferred_indexer and base_source == "prowlarr":
            stages.append(
                _SearchStage(
                    key="preferred_indexer",
                    sources=(base_source,),
                    indexers=(settings.preferred_indexer,),
                )
            )

        stages.append(_SearchStage(key="preferred_source", sources=(base_source,)))

        if (
            explicit_source is None
            and settings.fallback_strategy == RequestAutoFallbackStrategy.SAME_SOURCE_THEN_ANY_SOURCE
        ):
            remaining_sources = tuple(source for source in enabled_sources if source != base_source)
            if remaining_sources:
                stages.append(_SearchStage(key="any_source", sources=remaining_sources))

        return stages

    stages.append(_SearchStage(key="all_sources", sources=tuple(enabled_sources)))
    return stages


def _search_stage(
    *,
    stage: _SearchStage,
    book: BookMetadata,
    content_type: str,
) -> tuple[list[Release], list[str]]:
    from shelfmark.release_sources import get_source

    results: list[Release] = []
    errors: list[str] = []

    for source_name in stage.sources:
        try:
            source = get_source(source_name)
            plan = build_release_search_plan(
                book,
                indexers=list(stage.indexers) if stage.indexers and source_name == "prowlarr" else None,
            )
            releases = source.search(
                book,
                plan,
                expand_search=False,
                content_type=content_type,
            )
        except ValueError:
            errors.append(f"{source_name}: unknown source")
            continue
        except (SourceUnavailableError, *_SOURCE_SEARCH_ERRORS) as exc:
            logger.warning(
                "Request auto-selection search failed for source %s: %s",
                source_name,
                exc,
            )
            errors.append(f"{source_name}: {exc}")
            continue
        except Exception as exc:  # pragma: no cover - defensive guard around source plugins
            logger.exception(
                "Unexpected request auto-selection error for source %s",
                source_name,
            )
            errors.append(f"{source_name}: {exc}")
            continue

        results.extend(releases)

    return results, errors


def _collect_release_formats(release: Release) -> tuple[str, ...]:
    seen: list[str] = []

    def add_format(value: object) -> None:
        if not isinstance(value, str):
            return
        fmt = value.strip().lower()
        if not fmt or fmt in seen:
            return
        seen.append(fmt)

    add_format(release.format)
    extra = release.extra if isinstance(release.extra, Mapping) else {}
    extra_formats = extra.get("formats")
    if isinstance(extra_formats, list):
        for value in extra_formats:
            add_format(value)
    else:
        add_format(extra_formats)
    return tuple(seen)


def _contains_audiobook_hint(value: object) -> bool:
    if not isinstance(value, str):
        return False
    tokens = [token for token in value.strip().lower().replace("/", " ").split() if token]
    return any(token in _AUDIOBOOK_FORMAT_HINTS for token in tokens)


def _infer_release_content_type(release: Release) -> str:
    if release.content_type is not None:
        return normalize_content_type(release.content_type)

    extra = release.extra if isinstance(release.extra, Mapping) else {}
    raw_categories = extra.get("categories")
    if isinstance(raw_categories, list):
        min_category, max_category = _AUDIOBOOK_CATEGORY_RANGE
        for raw_category in raw_categories:
            try:
                category_id = int(raw_category)
            except (TypeError, ValueError):
                continue
            if min_category <= category_id <= max_category:
                return "audiobook"

    candidates: list[object] = [release.format, release.title]
    candidates.extend(_collect_release_formats(release))
    if any(_contains_audiobook_hint(candidate) for candidate in candidates):
        return "audiobook"

    return "ebook"


def _release_matches_content_type(release: Release, content_type: str) -> bool:
    return _infer_release_content_type(release) == content_type


def _format_match_score(release: Release, preferred_formats: tuple[str, ...]) -> int:
    if not preferred_formats:
        return 0
    release_formats = _collect_release_formats(release)
    if not release_formats:
        return -1
    for index, preferred_format in enumerate(preferred_formats):
        if preferred_format in release_formats:
            return len(preferred_formats) - index
    return 0


def _availability_score(release: Release) -> int:
    score = 0
    if isinstance(release.seeders, int) and release.seeders > 0:
        score += release.seeders * 10
    if release.download_url:
        score += 50
    if release.protocol is not None:
        score += 25
    if release.size_bytes is not None or release.size:
        score += 10
    return score


def _parse_release_newest_value(release: Release) -> int:
    extra = release.extra if isinstance(release.extra, Mapping) else {}
    for key in ("publish_date", "posted_date"):
        raw_value = extra.get(key)
        if not isinstance(raw_value, str):
            continue
        normalized = raw_value.strip()
        if not normalized:
            continue
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            continue
        return int(parsed.timestamp())

    year = _parse_publish_year(extra.get("year"))
    if year is None:
        year = _parse_publish_year(extra.get("publish_year"))
    if year is None:
        year = _parse_publish_year(release.extra.get("year") if isinstance(release.extra, Mapping) else None)
    if year is None:
        return 0
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp())


def _rank_release(
    release: Release,
    *,
    settings: RequestAutoSelectionSettings,
) -> tuple[int, int, int, int, str, str]:
    format_score = _format_match_score(release, settings.preferred_formats)
    availability_score = _availability_score(release)
    seeders = release.seeders if isinstance(release.seeders, int) and release.seeders > 0 else -1
    newest_value = _parse_release_newest_value(release)

    if settings.selection_policy == RequestAutoSelectionPolicy.MOST_SEEDERS:
        primary = (seeders, format_score, availability_score, newest_value)
    elif settings.selection_policy == RequestAutoSelectionPolicy.NEWEST:
        primary = (newest_value, format_score, seeders, availability_score)
    elif settings.selection_policy == RequestAutoSelectionPolicy.BEST_AVAILABILITY:
        primary = (availability_score, format_score, seeders, newest_value)
    else:
        primary = (format_score, seeders, availability_score, newest_value)

    return (*primary, release.title.lower(), release.source_id)


def _build_selection_reason(
    release: Release,
    *,
    settings: RequestAutoSelectionSettings,
    stage: str,
) -> str:
    detail_bits = [release.source]
    if release.indexer:
        detail_bits.append(release.indexer)
    if release.format:
        detail_bits.append(release.format.upper())
    if isinstance(release.seeders, int):
        detail_bits.append(f"{release.seeders} seeders")
    detail = " / ".join(detail_bits)
    return (
        f"Auto-selected by request policy ({settings.selection_policy.value}, {stage}): {detail}"
    )


def _build_release_data(
    *,
    book_data: Mapping[str, Any],
    release: Release,
    content_type: str,
    settings: RequestAutoSelectionSettings,
    stage: str,
) -> dict[str, Any]:
    extra = dict(release.extra or {})
    extra["auto_selection"] = {
        "stage": stage,
        "policy": settings.selection_policy.value,
        "source": release.source,
        "indexer": release.indexer,
    }

    payload: dict[str, Any] = {
        "source": release.source,
        "source_id": release.source_id,
        "title": release.title or _normalize_optional_text(book_data.get("title")) or "Unknown title",
        "author": _normalize_optional_text(book_data.get("author")),
        "year": book_data.get("year"),
        "format": release.format,
        "size": release.size,
        "size_bytes": release.size_bytes,
        "download_url": release.download_url,
        "info_url": release.info_url,
        "protocol": release.protocol.value if release.protocol is not None else None,
        "indexer": release.indexer,
        "seeders": release.seeders,
        "peers": release.peers,
        "content_type": content_type,
        "search_mode": "universal",
        "preview": book_data.get("preview"),
        "series_name": book_data.get("series_name"),
        "series_position": book_data.get("series_position"),
        "series_count": book_data.get("series_count"),
        "subtitle": book_data.get("subtitle"),
        "source_url": book_data.get("source_url"),
        "extra": extra,
    }
    return {key: value for key, value in payload.items() if value is not None}


def select_release_for_request(
    *,
    book_data: Mapping[str, Any],
    source_hint: object,
    content_type: object,
    user_id: int | None,
    allowed_sources: tuple[str, ...] | None = None,
) -> RequestAutoSelectionResult:
    """Auto-select a release for a book-level request when settings allow it."""
    settings = resolve_request_auto_selection_settings(user_id=user_id)
    normalized_content_type = normalize_content_type(content_type or book_data.get("content_type"))

    if not settings.enabled:
        return RequestAutoSelectionResult(
            settings=settings,
            attempted=False,
            fallback_reason="automatic selection disabled",
        )
    if not settings.supports_content_type(normalized_content_type):
        return RequestAutoSelectionResult(
            settings=settings,
            attempted=False,
            fallback_reason=f"{normalized_content_type} auto-selection disabled",
        )

    book = _build_book_metadata(book_data, content_type=normalized_content_type)
    stages = _build_search_stages(
        source_hint=normalize_source(source_hint),
        content_type=normalized_content_type,
        settings=settings,
        user_id=user_id,
        allowed_sources=allowed_sources,
    )
    if not stages:
        return RequestAutoSelectionResult(
            settings=settings,
            attempted=True,
            fallback_reason="no eligible release sources are enabled",
        )

    searched_sources: list[str] = []
    errors: list[str] = []
    for stage in stages:
        searched_sources.extend(stage.sources)
        stage_results, stage_errors = _search_stage(
            stage=stage,
            book=book,
            content_type=normalized_content_type,
        )
        errors.extend(stage_errors)

        matching_results = [
            release for release in stage_results if _release_matches_content_type(release, normalized_content_type)
        ]
        if not matching_results:
            continue

        selected_release = max(
            matching_results,
            key=lambda release: _rank_release(release, settings=settings),
        )
        release_data = _build_release_data(
            book_data=book_data,
            release=selected_release,
            content_type=normalized_content_type,
            settings=settings,
            stage=stage.key,
        )
        reason = _build_selection_reason(
            selected_release,
            settings=settings,
            stage=stage.key,
        )
        return RequestAutoSelectionResult(
            settings=settings,
            attempted=True,
            selected=RequestAutoSelectionMatch(
                release=selected_release,
                release_data=release_data,
                reason=reason,
                stage=stage.key,
            ),
            searched_sources=tuple(dict.fromkeys(searched_sources)),
            errors=tuple(errors),
        )

    return RequestAutoSelectionResult(
        settings=settings,
        attempted=True,
        searched_sources=tuple(dict.fromkeys(searched_sources)),
        errors=tuple(errors),
        fallback_reason="no matching release candidates found",
    )

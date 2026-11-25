"""Release routes: listing, download, release sources and source records."""

from __future__ import annotations

import re
import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from flask import jsonify, request, session

from shelfmark.core import search_deadline
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.models import SearchFilters
from shelfmark.core.release_inspect_routes import register_release_inspect_routes
from shelfmark.core.request_helpers import (
    coerce_bool,
    get_session_db_user_id,
    normalize_positive_int,
)
from shelfmark.core.request_policy import (
    DEFAULT_SUPPORTED_CONTENT_TYPES,
    PolicyMode,
    get_source_content_type_capabilities,
    merge_request_policy_settings,
    normalize_content_type,
    normalize_source,
    resolve_policy_mode,
)
from shelfmark.core.requests_service import RequestServiceError, retry_request_release
from shelfmark.core.utils import AUDIOBOOK_FORMATS
from shelfmark.download import orchestrator as backend
from shelfmark.download import warmup
from shelfmark.release_sources import BrowseRecord, Release, SourceUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from werkzeug.wrappers import Response

    from shelfmark.core.user_db import UserDB
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_IMPORT_OPERATIONAL_ERRORS = (ImportError, *_OPERATIONAL_ERRORS)

_AUDIOBOOK_CATEGORY_RANGE = (3030, 3049)
_AUDIOBOOK_FORMAT_HINTS = frozenset(AUDIOBOOK_FORMATS)


def _contains_audiobook_format_hint(value: Any) -> bool:
    """Whether a string names an audiobook format as one of its words (m4b, mp3 and the like)."""
    if not isinstance(value, str):
        return False

    normalized = value.strip().lower()
    if not normalized:
        return False

    tokens = [token for token in re.split(r"[^a-z0-9]+", normalized) if token]
    return any(token in _AUDIOBOOK_FORMAT_HINTS for token in tokens)


def _resolve_release_content_type(data: dict[str, Any], source: Any) -> tuple[str, bool]:
    """Resolve release content type for policy checks and queue payload normalization."""
    extra = data.get("extra")
    if not isinstance(extra, dict):
        extra = {}

    explicit_content_type = data.get("content_type")
    if explicit_content_type is None:
        explicit_content_type = extra.get("content_type")
    if explicit_content_type is not None:
        return normalize_content_type(explicit_content_type), False

    categories = extra.get("categories")
    if isinstance(categories, list):
        min_cat, max_cat = _AUDIOBOOK_CATEGORY_RANGE
        for raw_category in categories:
            try:
                category_id = int(raw_category)
            except TypeError, ValueError:
                continue
            if min_cat <= category_id <= max_cat:
                return "audiobook", True

    candidates: list[Any] = [
        data.get("format"),
        extra.get("format"),
        extra.get("formats_display"),
        data.get("title"),
    ]
    formats = extra.get("formats")
    if isinstance(formats, list):
        candidates.extend(formats)
    else:
        candidates.append(formats)

    if any(_contains_audiobook_format_hint(candidate) for candidate in candidates):
        return "audiobook", True

    capabilities = get_source_content_type_capabilities()
    supported = capabilities.get(normalize_source(source))
    if supported and len(supported) == 1:
        return normalize_content_type(next(iter(supported))), True

    return "ebook", False


def _policy_block_response(
    mode: PolicyMode, *, error: str = "Download not allowed by policy"
) -> tuple[Response, int]:
    """Build the 403 for a request the user's policy mode refuses, with the mode they need."""
    logger.debug(
        "download policy guard user=%s db_user_id=%s mode=%s",
        session.get("user_id"),
        session.get("db_user_id"),
        mode.value,
    )
    if mode == PolicyMode.BLOCKED:
        return (
            jsonify(
                {
                    "error": error,
                    "code": "policy_blocked",
                    "required_mode": PolicyMode.BLOCKED.value,
                }
            ),
            403,
        )
    return (
        jsonify(
            {
                "error": error,
                "code": "policy_requires_request",
                "required_mode": mode.value,
            }
        ),
        403,
    )


# Modes under which the user never sees a release list: request_book asks for the
# book as a whole and blocked gets nothing. request_release still lists releases,
# since that user picks the one they ask for. Listing goes through the shared
# source credentials (Prowlarr, torrent clients, scrapers), so the routes that
# list have to refuse these two modes just as the download route does.
_RELEASE_LISTING_REFUSED_MODES = frozenset({PolicyMode.REQUEST_BOOK, PolicyMode.BLOCKED})


def _release_listing_block_response(mode: PolicyMode) -> tuple[Response, int]:
    """Build the 403 for a release listing the user's policy mode refuses."""
    return _policy_block_response(mode, error="Release listing not allowed by policy")


# Keys of a download call that say how to queue it, not what the release is.
_ROUTING_KEYS = frozenset({"priority", "on_behalf_of_user_id", "request_id"})

# An approved pack's file plan. It can run past the size a request may store, so a retry
# queues it with the download and keeps it out of the request's own release data.
_PLAN_KEYS = frozenset({"book_plan", "ignored_files"})


def _retry_refusal(status_code: int, code: str | None) -> tuple[Response, int]:
    """A fixed answer for a refused Retry, chosen by status and code, never the error text."""
    if code == "stale_transition":
        message = "This request is no longer pending"
    elif code == "queue_failed":
        message = "Could not queue the release"
    elif status_code == HTTPStatus.FORBIDDEN:
        message = "You can't retry this request"
    elif status_code == HTTPStatus.NOT_FOUND:
        message = "Request not found"
    else:
        message = "Could not retry the request"
    payload: dict[str, Any] = {"error": message}
    if code is not None:
        payload["code"] = code
    return jsonify(payload), status_code


class _ReleasePolicy:
    """Request-policy checks for the current session, shared by the release routes."""

    def __init__(
        self,
        *,
        resolve_auth_mode: Callable[[], str],
        get_user_db: Callable[[], UserDB | None],
        load_policy_settings: Callable[[], dict[str, Any]],
    ) -> None:
        """Take the lookups as callables, so each check reads the current settings."""
        self._resolve_auth_mode = resolve_auth_mode
        self._get_user_db = get_user_db
        self._load_policy_settings = load_policy_settings

    def mode_for_current_user(self, *, source: Any, content_type: Any) -> PolicyMode | None:
        """Resolve policy mode for current session, or None when policy guard is bypassed."""
        user_db = self._get_user_db()
        auth_mode = self._resolve_auth_mode()
        if auth_mode == "none":
            return None
        if session.get("is_admin", False):
            return None
        if user_db is None:
            return None

        global_settings = self._load_policy_settings()
        db_user_id = session.get("db_user_id")
        user_settings: dict[str, Any] | None = None
        if db_user_id is not None:
            try:
                user_settings = user_db.get_user_settings(int(db_user_id))
            except TypeError, ValueError:
                user_settings = None

        effective = merge_request_policy_settings(global_settings, user_settings)
        if not coerce_bool(effective.get("REQUESTS_ENABLED"), default=False):
            return None

        resolved_mode = resolve_policy_mode(
            source=source,
            content_type=content_type,
            global_settings=global_settings,
            user_settings=user_settings,
        )
        logger.debug(
            "download policy resolve user=%s db_user_id=%s is_admin=%s source=%s content_type=%s mode=%s",
            session.get("user_id"),
            db_user_id,
            bool(session.get("is_admin", False)),
            source,
            content_type,
            resolved_mode.value,
        )
        return resolved_mode

    def listing_refusal(self, *, source: Any, content_type: Any) -> PolicyMode | None:
        """Return the policy mode when the current user may not list releases from `source`."""
        mode = self.mode_for_current_user(source=source, content_type=content_type)
        if mode is not None and mode in _RELEASE_LISTING_REFUSED_MODES:
            return mode
        return None

    def sources_allowed_for_listing(
        self, sources: list[str], content_type: Any
    ) -> tuple[list[str], PolicyMode | None]:
        """Drop the sources the current user may not list releases from.

        Returns the sources left and, when any was dropped, the mode of the first one
        dropped, which is what the response reports when nothing is left.
        """
        allowed: list[str] = []
        refused_mode: PolicyMode | None = None
        for source_name in sources:
            refusal = self.listing_refusal(source=source_name, content_type=content_type)
            if refusal is None:
                allowed.append(source_name)
            elif refused_mode is None:
                refused_mode = refusal
        return allowed, refused_mode

    def source_record_refusal(self, source_name: str) -> PolicyMode | None:
        """Policy refusal for resolving one browse record of a release source.

        The record's content type is not known before it is fetched, so the user is
        refused only when every content type the source supports is refused.
        """
        capabilities = get_source_content_type_capabilities()
        content_types = capabilities.get(normalize_source(source_name)) or set(
            DEFAULT_SUPPORTED_CONTENT_TYPES
        )
        refused_mode: PolicyMode | None = None
        for content_type in sorted(content_types):
            refusal = self.listing_refusal(source=source_name, content_type=content_type)
            if refusal is None:
                return None
            if refused_mode is None:
                refused_mode = refusal
        return refused_mode

    def payload_listing_gate(self, data: dict[str, Any]) -> tuple[Response, int] | None:
        """Policy gate for routes that take a release payload, shared with the inspect route."""
        source = data["source"]
        resolved_content_type, _inferred = _resolve_release_content_type(data, source)
        refusal = self.listing_refusal(source=source, content_type=resolved_content_type)
        if refusal is None:
            return None
        return _release_listing_block_response(refusal)

    def resolve_download_user_context(
        self,
        db_user_id: Any,
        username: Any,
        on_behalf_of_user_id: Any,
    ) -> tuple[Any, Any, tuple[Response, int] | None]:
        """Resolve download queue user context, including optional admin on-behalf overrides."""
        user_db = self._get_user_db()
        if on_behalf_of_user_id in (None, ""):
            return db_user_id, username, None

        if not session.get("is_admin", False):
            return db_user_id, username, (jsonify({"error": "Admin required"}), 403)

        if user_db is None:
            return db_user_id, username, (jsonify({"error": "User database unavailable"}), 503)

        try:
            target_user_id = int(on_behalf_of_user_id)
        except TypeError, ValueError:
            return db_user_id, username, (jsonify({"error": "Invalid on_behalf_of_user_id"}), 400)

        if target_user_id <= 0:
            return db_user_id, username, (jsonify({"error": "Invalid on_behalf_of_user_id"}), 400)

        target_user = user_db.get_user(user_id=target_user_id)
        if not target_user:
            return db_user_id, username, (jsonify({"error": "User not found"}), 404)

        return target_user["id"], target_user["username"], None


def _parse_search_filters_from_request() -> SearchFilters:
    """Parse direct/source browse filters from query parameters."""
    return SearchFilters(
        isbn=request.args.getlist("isbn"),
        author=request.args.getlist("author"),
        title=request.args.getlist("title"),
        lang=request.args.getlist("lang"),
        sort=request.args.get("sort"),
        content=request.args.getlist("content"),
        format=request.args.getlist("format"),
    )


def _build_source_query_book(query_text: str, filters: SearchFilters) -> BookMetadata:
    """Build a synthetic book context for source-native browse searches."""
    from shelfmark.metadata_providers import BookMetadata

    author_values = [value.strip() for value in (filters.author or []) if str(value).strip()]
    title_values = [value.strip() for value in (filters.title or []) if str(value).strip()]
    isbn_values = [value.strip() for value in (filters.isbn or []) if str(value).strip()]
    title = (
        title_values[0]
        if title_values
        else query_text
        or (isbn_values[0] if isbn_values else "")
        or (author_values[0] if author_values else "Direct Search")
    )
    author = author_values[0] if author_values else ""

    return BookMetadata(
        provider="manual",
        provider_id=query_text or title,
        provider_display_name="Manual Search",
        title=title,
        search_title=title,
        search_author=author or None,
        authors=author_values,
    )


def _serialize_browse_record(record: BrowseRecord) -> dict:
    """Serialize a source-native browse record for the frontend."""
    result = {key: value for key, value in record.__dict__.items() if value is not None}

    preview = result.get("preview")
    if isinstance(preview, str) and preview:
        from shelfmark.core.utils import transform_cover_url

        result["preview"] = transform_cover_url(preview, record.id)

    return result


def _serialize_release(release: Release) -> dict:
    """Serialize a release for the frontend, normalizing preview URLs."""
    from dataclasses import asdict

    from shelfmark.core.utils import transform_cover_url

    result = asdict(release)
    extra = result.get("extra")
    if isinstance(extra, dict):
        preview = extra.get("preview")
        if isinstance(preview, str) and preview:
            extra = dict(extra)
            extra["preview"] = transform_cover_url(preview, release.source_id)
            result["extra"] = extra

    return result


def register_release_routes(
    app: Flask,
    login_required: Callable[..., Any],
    *,
    resolve_auth_mode: Callable[[], str],
    get_user_db: Callable[[], UserDB | None],
    load_policy_settings: Callable[[], dict[str, Any]],
    resolve_status_scope: Callable[..., tuple[bool, int | None, bool]],
    attach_library_ownership: Callable[[Any, dict[str, Any]], None],
    emit_request_updates: Callable[[list[dict[str, Any]]], None] | None = None,
) -> None:
    """Register the release listing, download, source list and source record routes."""
    policy = _ReleasePolicy(
        resolve_auth_mode=resolve_auth_mode,
        get_user_db=get_user_db,
        load_policy_settings=load_policy_settings,
    )

    register_release_inspect_routes(app, login_required, policy_gate=policy.payload_listing_gate)

    def _queue_for_request(
        raw_request_id: object, release_payload: dict[str, Any], data: dict[str, Any]
    ) -> Response | tuple[Response, int]:
        """Queue a release through the reopened request it failed for (#1681).

        The policy check has already passed, so the caller may download this release
        directly; the request service checks they are an admin or the request's owner and
        that the request is still pending. Without login there is no session user, so the
        admin that owns synced requests and approves auto-downloads is used, as auto-download
        does.
        """
        request_id = normalize_positive_int(raw_request_id)
        if request_id is None:
            return jsonify({"error": "Invalid request_id"}), 400
        if data.get("on_behalf_of_user_id") not in (None, ""):
            return jsonify({"error": "request_id already names who the download is for"}), 400
        user_db = get_user_db()
        if user_db is None:
            return jsonify({"error": "User database unavailable"}), 503

        if resolve_auth_mode() == "none":
            from shelfmark.core.hardcover_sync import resolve_request_owner

            actor_user_id, is_admin = resolve_request_owner(user_db), True
        else:
            actor_user_id = get_session_db_user_id(session)
            is_admin = bool(session.get("is_admin", False))

        release_data = {
            key: value
            for key, value in release_payload.items()
            if key not in _ROUTING_KEYS | _PLAN_KEYS
        }
        plan = {key: release_payload[key] for key in _PLAN_KEYS if key in release_payload}

        def queue_with_plan(
            queued_release: dict[str, Any], priority: int, **kwargs: Any
        ) -> tuple[bool, str | None]:
            """Queue the release with its pack plan, which the request does not store."""
            return backend.queue_release({**queued_release, **plan}, priority, **kwargs)

        try:
            updated = retry_request_release(
                user_db,
                request_id=request_id,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
                queue_release=queue_with_plan,
                release_data=release_data,
            )
        except RequestServiceError as exc:
            logger.warning("Retry of request #%s refused: %s", request_id, exc)
            return _retry_refusal(exc.status_code, exc.code)
        logger.info("Request #%s queued again from Retry", updated["id"])
        if emit_request_updates is not None:
            emit_request_updates([updated])
        return jsonify({"status": "queued", "priority": 0, "request_id": updated["id"]})

    @app.route("/api/releases/download", methods=["POST"])
    @login_required
    def api_download_release() -> Response | tuple[Response, int]:
        """Queue a release for download.

        This endpoint is used when downloading from the ReleaseModal where the
        frontend already has all the release data from the search results.

        Request Body (JSON):
            source (str): Release source (e.g., "direct_download")
            source_id (str): ID within the source (e.g., AA MD5 hash)
            title (str): Book title
            format (str, optional): File format
            size (str, optional): Human-readable size
            extra (dict, optional): Additional metadata

        Returns:
            flask.Response: JSON status object indicating success or failure.

        """
        try:
            data = request.get_json(silent=True)
            if not data:
                return jsonify({"error": "No data provided"}), 400

            if "source_id" not in data:
                return jsonify({"error": "source_id is required"}), 400
            if "source" not in data:
                return jsonify({"error": "source is required"}), 400

            source = data["source"]
            resolved_content_type, inferred_content_type = _resolve_release_content_type(
                data, source
            )
            policy_mode = policy.mode_for_current_user(
                source=source,
                content_type=resolved_content_type,
            )
            if policy_mode is not None and policy_mode != PolicyMode.DOWNLOAD:
                return _policy_block_response(policy_mode)

            release_payload = data
            if inferred_content_type and data.get("content_type") is None:
                release_payload = dict(data)
                release_payload["content_type"] = resolved_content_type

            # Retry on a failed release of a reopened request goes through that request.
            if data.get("request_id") not in (None, ""):
                return _queue_for_request(data["request_id"], release_payload, data)

            logger.info(
                "Download request received. keys=%s downloads=%s extra.downloads=%s",
                list(data.keys()),
                data.get("downloads"),
                data.get("extra", {}).get("downloads")
                if isinstance(data.get("extra"), dict)
                else None,
            )

            priority = data.get("priority", 0)
            # Per-user download overrides
            db_user_id = session.get("db_user_id")
            _username = session.get("user_id")
            db_user_id, _username, on_behalf_error = policy.resolve_download_user_context(
                db_user_id,
                _username,
                data.get("on_behalf_of_user_id"),
            )
            if on_behalf_error:
                return on_behalf_error
            success, error_msg = backend.queue_release(
                release_payload,
                priority,
                user_id=db_user_id,
                username=_username,
            )

            if success:
                return jsonify({"status": "queued", "priority": priority})
            return jsonify({"error": error_msg or "Failed to queue release"}), 500
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Release download error: {e}")
            return jsonify({"error": "Could not queue the release"}), 500

    @app.route("/api/releases", methods=["GET"])
    @login_required
    def api_releases() -> Response | tuple[Response, int]:
        """Search for downloadable releases of a book.

        This endpoint takes book metadata and searches available release sources
        (e.g., Anna's Archive, Libgen) for downloadable files.

        Query Parameters:
            provider (str): Metadata provider name (required)
            book_id (str): Book ID from metadata provider (required)
            source (str): Release source to search (optional, default: all)

        Returns:
            flask.Response: JSON with list of available releases.

        """
        try:
            from dataclasses import asdict

            from shelfmark.core.release_search import search_source_releases
            from shelfmark.metadata_providers import (
                BookMetadata,
                get_provider,
                get_provider_kwargs,
                is_provider_registered,
            )
            from shelfmark.release_sources import (
                apply_column_visibility,
                browse_record_to_book_metadata,
                get_source,
                list_available_sources,
                serialize_column_config,
                source_results_are_releases,
            )

            def _search_source_releases(
                source_name: str, search_book: BookMetadata
            ) -> tuple[Any | None, list[Any], str | None]:
                """Search one source and return any error message instead of raising."""
                return search_source_releases(
                    source_name,
                    search_book,
                    languages=(
                        browse_filters.lang if source_query_filters is not None else languages
                    ),
                    manual_query=(query_text if source_query_filters is not None else manual_query),
                    indexers=indexers,
                    expand_search=expand_search,
                    content_type=content_type,
                    source_filters=source_query_filters,
                    user_id=db_user_id,
                    include_error_detail=resolve_status_scope(require_authenticated=False)[0],
                )

            provider = request.args.get("provider", "").strip()
            book_id = request.args.get("book_id", "").strip()
            source_filter = request.args.get("source", "").strip()
            query_text = request.args.get("query", "").strip()
            # Accept title/author from frontend to avoid re-fetching metadata
            title_param = request.args.get("title", "").strip()
            author_param = request.args.get("author", "").strip()
            expand_search = request.args.get("expand_search", "").lower() == "true"
            # Accept language codes for filtering (comma-separated)
            languages_param = request.args.get("languages", "").strip()
            languages = (
                [lang.strip() for lang in languages_param.split(",") if lang.strip()]
                if languages_param
                else None
            )
            # Without an explicit filter the plan falls back to this user's default languages.
            db_user_id = get_session_db_user_id(session)
            # Content type for audiobook vs ebook search
            content_type = request.args.get("content_type", "ebook").strip()

            manual_query = request.args.get("manual_query", "").strip()

            # Accept indexer names for Prowlarr filtering (comma-separated)
            indexers_param = request.args.get("indexers", "").strip()
            indexers = (
                [idx.strip() for idx in indexers_param.split(",") if idx.strip()]
                if indexers_param
                else None
            )
            browse_filters = _parse_search_filters_from_request()
            has_browse_filters = bool(query_text or any(vars(browse_filters).values()))

            source_query_filters = None
            is_source_provider = bool(provider) and source_results_are_releases(provider)

            if not provider or not book_id:
                if not source_filter or not has_browse_filters:
                    return jsonify(
                        {"error": "Parameters 'provider' and 'book_id' are required"}
                    ), 400
                if not source_results_are_releases(source_filter):
                    return jsonify(
                        {"error": f"Source does not support browse release search: {source_filter}"}
                    ), 400
                source_query_filters = browse_filters

            # Determine which release sources to search
            if source_filter:
                sources_to_search = [source_filter]
            elif is_source_provider:
                # Source-backed browse flows stay within the source that produced the record.
                sources_to_search = [provider]
            else:
                # Search only enabled sources
                sources_to_search = [
                    src["name"] for src in list_available_sources() if src["enabled"]
                ]

            # The policy gate comes before anything is asked of a source: listing goes
            # through the shared source credentials, so a user whose policy never shows
            # a release list (request_book, blocked) must not reach them.
            sources_to_search, refused_mode = policy.sources_allowed_for_listing(
                sources_to_search, content_type
            )
            if not sources_to_search and refused_mode is not None:
                return _release_listing_block_response(refused_mode)

            book: BookMetadata

            if source_query_filters is not None:
                book = _build_source_query_book(query_text, browse_filters)
            elif is_source_provider:
                # Source-backed browse flows can reopen the release modal with provider=<source name>.
                # In that flow, treat the source-native record as release-search context instead of
                # requiring a metadata provider registration.
                # A source named in `source` was gated above. When provider names another one,
                # its record lookup needs the refusal the record route applies.
                if normalize_source(provider) not in {
                    normalize_source(s) for s in sources_to_search
                }:
                    refused_record_mode = policy.source_record_refusal(provider)
                    if refused_record_mode is not None:
                        return _release_listing_block_response(refused_record_mode)
                source = get_source(provider)
                direct_record = source.get_record(book_id)
                if direct_record is None:
                    return jsonify({"error": "Book not found in release source"}), 404

                book = browse_record_to_book_metadata(
                    direct_record,
                    title_override=title_param or None,
                    author_override=author_param or None,
                )
            elif provider == "manual":
                resolved_title = title_param or manual_query or "Manual Search"
                resolved_author = author_param or ""
                # The release modal sends `authors.join(', ')` as `author`, so the commas here
                # are joins between contributors, not part of one name. This split is the only
                # place that knows that, so `search_author` comes from it rather than from the
                # joined text - see issue #1252.
                authors = [a.strip() for a in resolved_author.split(",") if a.strip()]

                book = BookMetadata(
                    provider="manual",
                    provider_id=book_id,
                    provider_display_name="Manual Search",
                    title=resolved_title,
                    search_title=resolved_title,
                    search_author=authors[0] if authors else None,
                    authors=authors,
                )
            else:
                if not is_provider_registered(provider):
                    return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

                # Get book metadata from provider
                kwargs = get_provider_kwargs(provider)
                prov = get_provider(provider, **kwargs)
                resolved_book = prov.get_book(book_id)

                if not resolved_book:
                    return jsonify({"error": "Book not found in metadata provider"}), 404
                book = resolved_book

                # Override title from frontend if available (search results may have better data)
                # Note: We intentionally DON'T override authors here - get_book() now returns
                # filtered authors (primary authors only, excluding translators/narrators),
                # which gives better release search results than the unfiltered search data
                if title_param:
                    if title_param != book.title:
                        # A different title is one the search result carried and the book
                        # record does not, typically a translation's ("Kinder des Nebels" for
                        # "Mistborn: The Final Empire"). The provider's search_title derives
                        # from the original and would otherwise win the release search.
                        book.search_title = title_param
                    book.title = title_param

            # Search each source for releases.
            #
            # Under a wall-clock budget: this endpoint is synchronous, and the bypass path it
            # can reach used to be allowed minutes per URL with nothing bounding the request
            # as a whole. A search that ran into an unsolvable protection challenge therefore
            # outlived every reverse proxy in front of it and surfaced to the user as
            # "Server unavailable (504)" - a gateway timeout that blames their proxy for a
            # challenge failure. The budget is shared across sources, so a stuck first source
            # cannot spend the whole request on its own. See issue #1276.
            all_releases = []
            errors = []
            source_instances = {}  # Keep source instances for column config

            # A real search is under way, so a warm-up still sitting on its start-up delay
            # should stand down rather than queue its throwaway solve in front of this one.
            warmup.note_user_search()

            with search_deadline.search_deadline():
                for index, source_name in enumerate(sources_to_search):
                    if search_deadline.expired():
                        logger.warning("Release search budget spent; %s not searched", source_name)
                        errors.append(f"{source_name}: {search_deadline.deadline_message()}")
                        continue

                    with search_deadline.unit_deadline(waiting=len(sources_to_search) - index - 1):
                        source, releases, error = _search_source_releases(source_name, book)
                    if source is not None:
                        source_instances[source_name] = source
                        all_releases.extend(releases)
                    if error is not None:
                        errors.append(error)

            # Convert Release objects to dicts
            releases_data = [_serialize_release(release) for release in all_releases]

            # Get column config from the first source searched
            # Reuse the same instance to get any dynamic data (e.g., online_servers for IRC)
            column_config = None
            if sources_to_search and sources_to_search[0] in source_instances:
                try:
                    first_source = source_instances[sources_to_search[0]]
                    column_config = serialize_column_config(
                        apply_column_visibility(
                            first_source.get_column_config(),
                            content_type=normalize_content_type(content_type),
                            is_setting_enabled=lambda key: bool(
                                app_config.get(key, True, user_id=db_user_id)
                            ),
                        )
                    )
                except _OPERATIONAL_ERRORS as e:
                    logger.warning("Failed to get column config: %s", e)

            # Convert book to dict and transform cover_url
            book_dict = asdict(book)
            attach_library_ownership(book, book_dict)
            from shelfmark.core.utils import transform_cover_url

            if book_dict.get("cover_url"):
                cache_id = f"{provider}_{book_id}"
                book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

            search_info = {}
            for source_name, source_instance in source_instances.items():
                info: dict[str, str | int | None] = {}
                if (
                    hasattr(source_instance, "last_search_type")
                    and source_instance.last_search_type
                ):
                    info["search_type"] = source_instance.last_search_type
                if hasattr(source_instance, "total_results"):
                    info["total_results"] = source_instance.total_results
                if info:
                    search_info[source_name] = info

            response = {
                "releases": releases_data,
                "book": book_dict,
                "sources_searched": sources_to_search,
                "column_config": column_config,
                "search_info": search_info,
            }

            if errors:
                response["errors"] = errors

            # If no releases found and there were errors, return 503 with the first
            # source failure message so direct-mode source query searches surface the
            # same unavailable-state messaging as release modal searches.
            if not releases_data and errors:
                # Use the first error message (typically the most relevant)
                error_message = errors[0]
                # Strip the source prefix if present (e.g., "direct_download: message" -> "message")
                if ": " in error_message:
                    error_message = error_message.split(": ", 1)[1]
                return jsonify({"error": error_message}), 503

            return jsonify(response)
        except SourceUnavailableError as e:
            logger.warning("Release search unavailable: %s", e)
            return jsonify({"error": str(e)}), 503
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Releases search error: {e}")
            return jsonify({"error": "Could not search for releases"}), 500

    @app.route("/api/release-sources", methods=["GET"])
    @login_required
    def api_release_sources() -> Response | tuple[Response, int]:
        """Get available release sources from the plugin registry.

        Returns:
            flask.Response: JSON list of available release sources.

        """
        try:
            from shelfmark.release_sources import list_available_sources

            sources = list_available_sources()
            return jsonify(sources)
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Release sources error: {e}")
            return jsonify({"error": "Could not list the release sources"}), 500

    @app.route("/api/release-sources/<source_name>/records/<path:record_id>", methods=["GET"])
    @login_required
    def api_release_source_record(
        source_name: str, record_id: str
    ) -> Response | tuple[Response, int]:
        """Resolve a source-native browse record for a release source."""
        try:
            from shelfmark.release_sources import get_source

            source = get_source(source_name)
            refused_mode = policy.source_record_refusal(source_name)
            if refused_mode is not None:
                return _release_listing_block_response(refused_mode)
            record = source.get_record(record_id)
            if record is None:
                return jsonify({"error": "Record not found"}), 404
            return jsonify(_serialize_browse_record(record))
        except ValueError:
            return jsonify({"error": f"Unknown release source: {source_name}"}), 400
        except SourceUnavailableError as e:
            logger.warning("Release source record unavailable: %s", e)
            return jsonify({"error": str(e)}), 503
        except _OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Release source record error: {e}")
            return jsonify({"error": "Could not resolve the record"}), 500

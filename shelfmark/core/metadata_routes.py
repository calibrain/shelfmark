"""Metadata providers: the ``/api/metadata/...`` search, book and target routes."""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from functools import wraps
from typing import TYPE_CHECKING, Any

from flask import jsonify, request, session

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import get_session_db_user_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask
    from werkzeug.wrappers import Response

    from shelfmark.metadata_providers import BookMetadata, MetadataProvider

logger = setup_logger(__name__)

_OPERATIONAL_ERRORS = (OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)
_IMPORT_OPERATIONAL_ERRORS = (ImportError, *_OPERATIONAL_ERRORS)


def _resolve_metadata_provider(provider_name: str) -> MetadataProvider:
    """Validate, instantiate and return a ready metadata provider.

    Raises appropriate HTTP-friendly exceptions on failure.
    """
    from shelfmark.metadata_providers import (
        get_provider,
        get_provider_kwargs,
        is_provider_registered,
    )

    if not is_provider_registered(provider_name):
        msg = f"Unknown metadata provider: {provider_name}"
        raise ValueError(msg)

    kwargs = get_provider_kwargs(provider_name)
    prov = get_provider(provider_name, **kwargs)

    if not prov.is_available():
        msg = f"Provider '{provider_name}' is not available"
        raise RuntimeError(msg)

    return prov


def _handle_target_errors(
    fallback_message: str,
) -> Callable[
    [Callable[..., Response | tuple[Response, int]]], Callable[..., Response | tuple[Response, int]]
]:
    """Wrap a metadata-target route with standard error handling."""

    def decorator(
        fn: Callable[..., Response | tuple[Response, int]],
    ) -> Callable[..., Response | tuple[Response, int]]:
        @wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> Response | tuple[Response, int]:
            try:
                return fn(*args, **kwargs)
            except (NotImplementedError, ValueError) as e:
                return jsonify({"error": str(e)}), 400
            except RuntimeError as e:
                return jsonify({"error": str(e)}), 502
            except (OSError, TypeError, sqlite3.Error) as e:
                logger.error_trace(f"{fallback_message}: {e}")
                return jsonify({"error": fallback_message}), 500

        return wrapper

    return decorator


def register_metadata_routes(
    app: Flask,
    *,
    login_required: Callable[..., Callable[..., Response | tuple[Response, int]]],
    resolve_auth_mode: Callable[[], str],
    attach_library_ownership: Callable[[BookMetadata, dict[str, Any]], None],
) -> None:
    """Register the ``/api/metadata/...`` routes.

    ``login_required`` is the app's decorator. ``resolve_auth_mode`` is called per request so a
    runtime patch of the auth mode still takes effect. ``attach_library_ownership`` is shared
    with the releases routes, which is why it stays in ``main.py``.
    """

    def _session_may_use_provider_account() -> bool:
        """Whether this session may read or change the metadata provider's own account.

        A provider such as Hardcover is signed in as one account, the instance owner's, and its lists
        and shelves belong to that person. With no authentication configured the one user is the owner.
        Otherwise only an admin may use them.
        """
        return resolve_auth_mode() == "none" or bool(session.get("is_admin", False))

    @app.route("/api/metadata/providers", methods=["GET"])
    @login_required
    def api_metadata_providers() -> Response | tuple[Response, int]:
        """Get list of available metadata providers.

        Returns:
            flask.Response: JSON with list of providers and their status.

        """
        try:
            from shelfmark.metadata_providers import (
                get_configured_provider_name,
                get_provider,
                get_provider_kwargs,
                list_providers,
            )

            app_config.refresh()
            db_user_id = get_session_db_user_id(session)

            configured_metadata_provider = get_configured_provider_name(
                content_type="ebook",
                user_id=db_user_id,
                fallback_to_main=True,
            )
            configured_audiobook_metadata_provider = get_configured_provider_name(
                content_type="audiobook",
                user_id=db_user_id,
                fallback_to_main=False,
            )
            configured_combined_metadata_provider = get_configured_provider_name(
                content_type="combined",
                user_id=db_user_id,
                fallback_to_main=False,
            )
            providers = []
            for info in list_providers():
                enabled_key = f"{info['name'].upper()}_ENABLED"
                provider_info = {
                    "name": info["name"],
                    "display_name": info["display_name"],
                    "requires_auth": info["requires_auth"],
                    "enabled": app_config.get(enabled_key, False) is True,
                    "available": False,
                }

                try:
                    kwargs = get_provider_kwargs(info["name"])
                    provider = get_provider(info["name"], **kwargs)
                    provider_info["available"] = provider.is_available()
                except _OPERATIONAL_ERRORS as exc:
                    logger.debug(
                        "Metadata provider %s availability check failed: %s",
                        info["name"],
                        exc,
                    )

                providers.append(provider_info)

            return jsonify(
                {
                    "providers": providers,
                    "configured_provider": configured_metadata_provider or None,
                    "configured_provider_audiobook": configured_audiobook_metadata_provider or None,
                    "configured_provider_combined": configured_combined_metadata_provider or None,
                }
            )
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Metadata providers error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/metadata/config", methods=["GET"])
    @login_required
    def api_metadata_config() -> Response | tuple[Response, int]:
        """Return provider-specific metadata search config for the active session."""
        try:
            from shelfmark.metadata_providers import (
                get_configured_provider_name,
                get_provider,
                get_provider_capabilities,
                get_provider_default_sort,
                get_provider_kwargs,
                get_provider_search_fields,
                get_provider_sort_options,
                is_provider_registered,
            )

            app_config.refresh()
            content_type = request.args.get("content_type", "ebook").strip()
            provider_name = request.args.get("provider", "").strip()

            db_user_id = get_session_db_user_id(session)

            if not provider_name:
                provider_name = get_configured_provider_name(
                    content_type=content_type,
                    user_id=db_user_id,
                    fallback_to_main=True,
                )

            if not provider_name:
                return jsonify(
                    {
                        "provider": None,
                        "display_name": None,
                        "enabled": False,
                        "available": False,
                        "search_fields": [],
                        "capabilities": [],
                        "sort_options": [{"value": "relevance", "label": "Most relevant"}],
                        "default_sort": "relevance",
                    }
                )

            if not is_provider_registered(provider_name):
                return jsonify({"error": f"Unknown metadata provider: {provider_name}"}), 400

            kwargs = get_provider_kwargs(provider_name)
            provider = get_provider(provider_name, **kwargs)
            enabled_key = f"{provider_name.upper()}_ENABLED"
            provider_enabled = app_config.get(enabled_key, False) is True
            provider_available = provider.is_available()

            return jsonify(
                {
                    "provider": provider_name,
                    "display_name": provider.display_name,
                    "enabled": provider_enabled,
                    "available": provider_available,
                    "search_fields": get_provider_search_fields(provider_name),
                    "capabilities": get_provider_capabilities(provider_name),
                    "sort_options": get_provider_sort_options(provider_name),
                    "default_sort": get_provider_default_sort(provider_name, user_id=db_user_id),
                }
            )
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Metadata config error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/metadata/search", methods=["GET"])
    @login_required
    def api_metadata_search() -> Response | tuple[Response, int]:
        """Search for books using the configured metadata provider.

        Query Parameters:
            query (str): Search query (required)
            limit (int): Maximum number of results (default: 40, max: 100)
            sort (str): Sort order - relevance, popularity, rating, newest, oldest (default: relevance)
            [dynamic fields]: Provider-specific search fields passed as query params

        Returns:
            flask.Response: JSON with list of books from metadata provider.

        """
        try:
            from dataclasses import asdict

            from shelfmark.metadata_providers import (
                CheckboxSearchField,
                MetadataSearchOptions,
                NumberSearchField,
                SortOrder,
                get_configured_provider,
                get_provider,
                get_provider_kwargs,
                is_provider_enabled,
                is_provider_registered,
            )

            query = request.args.get("query", "").strip()
            content_type = request.args.get("content_type", "ebook").strip()
            provider_name = request.args.get("provider", "").strip()

            try:
                limit = min(int(request.args.get("limit", 40)), 100)
            except ValueError:
                limit = 40

            try:
                page = max(1, int(request.args.get("page", 1)))
            except ValueError:
                page = 1

            # Parse sort parameter
            sort_value = request.args.get("sort", "relevance").lower()
            try:
                sort_order = SortOrder(sort_value)
            except ValueError:
                sort_order = SortOrder.RELEVANCE

            db_user_id = get_session_db_user_id(session)

            if provider_name:
                if not is_provider_registered(provider_name):
                    return jsonify(
                        {
                            "error": f"Unknown metadata provider: {provider_name}",
                            "message": f"Unknown metadata provider: {provider_name}",
                        }
                    ), 400
                if not is_provider_enabled(provider_name):
                    return jsonify(
                        {
                            "error": f"Metadata provider '{provider_name}' is not enabled",
                            "message": f"{provider_name} is not enabled. Enable it in Settings first.",
                        }
                    ), 503

                kwargs = get_provider_kwargs(provider_name)
                provider = get_provider(provider_name, **kwargs)
            else:
                provider = get_configured_provider(content_type=content_type, user_id=db_user_id)

            if not provider:
                return jsonify(
                    {
                        "error": "No metadata provider configured",
                        "message": "No metadata provider configured. Enable one in Settings.",
                    }
                ), 503

            if not provider.is_available():
                return jsonify(
                    {
                        "error": f"Metadata provider '{provider.name}' is not available",
                        "message": f"{provider.display_name} is not available. Check configuration in Settings.",
                    }
                ), 503

            # Extract custom search field values from query params
            fields: dict[str, Any] = {}
            may_use_account = _session_may_use_provider_account()
            for search_field in provider.search_fields:
                if search_field.key in provider.account_search_fields and not may_use_account:
                    continue
                value = request.args.get(search_field.key)
                if value is not None:
                    # Strip string values to handle whitespace-only input
                    value = value.strip()
                    if value != "":
                        # Parse value based on field type
                        if isinstance(search_field, CheckboxSearchField):
                            fields[search_field.key] = value.lower() in ("true", "1", "yes", "on")
                        elif isinstance(search_field, NumberSearchField):
                            with suppress(ValueError):
                                fields[search_field.key] = int(value)
                        else:
                            fields[search_field.key] = value

            # Require either a query or at least one field value
            if not query and not fields:
                return jsonify({"error": "Either 'query' or search field values are required"}), 400

            options = MetadataSearchOptions(
                query=query,
                limit=limit,
                page=page,
                sort=sort_order,
                fields=fields,
                user_id=db_user_id,
            )
            search_result = provider.search_paginated(options)

            # Convert BookMetadata objects to dicts
            books_data = [asdict(book) for book in search_result.books]

            # Transform cover_url to local proxy URLs when caching is enabled
            from shelfmark.core.utils import transform_cover_url

            for book_dict in books_data:
                if book_dict.get("cover_url"):
                    cache_id = f"{book_dict['provider']}_{book_dict['provider_id']}"
                    book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)
            for book, book_dict in zip(search_result.books, books_data, strict=True):
                attach_library_ownership(book, book_dict)

            response_data = {
                "books": books_data,
                "provider": provider.name,
                "query": query,
                "page": search_result.page,
                "total_found": search_result.total_found,
                "has_more": search_result.has_more,
            }
            if search_result.source_url:
                response_data["source_url"] = search_result.source_url
            if search_result.source_title:
                response_data["source_title"] = search_result.source_title
            return jsonify(response_data)
        except _IMPORT_OPERATIONAL_ERRORS as e:
            logger.error_trace(f"Metadata search error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/metadata/field-options", methods=["GET"])
    @login_required
    def api_metadata_field_options() -> Response:
        """Return dynamic search-field options for a metadata provider."""
        try:
            from shelfmark.metadata_providers import (
                get_configured_provider,
                get_provider,
                get_provider_kwargs,
                is_provider_registered,
            )

            field_key = request.args.get("field", "").strip()
            provider_name = request.args.get("provider", "").strip()
            content_type = request.args.get("content_type", "ebook").strip()
            query_text = request.args.get("query", "").strip()

            if not field_key:
                return jsonify({"options": []})

            db_user_id = get_session_db_user_id(session)

            provider = None
            if provider_name:
                if not is_provider_registered(provider_name):
                    return jsonify({"options": []})
                kwargs = get_provider_kwargs(provider_name)
                provider = get_provider(provider_name, **kwargs)
            else:
                provider = get_configured_provider(content_type=content_type, user_id=db_user_id)

            if not provider or not provider.is_available():
                return jsonify({"options": []})

            if (
                field_key in provider.account_search_fields
                and not _session_may_use_provider_account()
            ):
                return jsonify({"options": []})

            options = provider.get_search_field_options(field_key, query=query_text or None)
            return jsonify({"options": options})
        except _OPERATIONAL_ERRORS as e:
            logger.warning("Metadata field options endpoint error: %s", e)
            return jsonify({"options": []})

    @app.route("/api/metadata/book/<provider>/<book_id>", methods=["GET"])
    @login_required
    def api_metadata_book(provider: str, book_id: str) -> Response | tuple[Response, int]:
        """Get detailed book information from a metadata provider.

        Path Parameters:
            provider (str): Provider name (e.g., "hardcover", "openlibrary")
            book_id (str): Book ID in the provider's system

        Returns:
            flask.Response: JSON with book details.

        """
        try:
            from dataclasses import asdict

            prov = _resolve_metadata_provider(provider)

            book = prov.get_book(book_id)
            if not book:
                return jsonify({"error": "Book not found"}), 404

            book_dict = asdict(book)
            attach_library_ownership(book, book_dict)

            # Transform cover_url to local proxy URL when caching is enabled
            from shelfmark.core.utils import transform_cover_url

            if book_dict.get("cover_url"):
                cache_id = f"{provider}_{book_id}"
                book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

            return jsonify(book_dict)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        except (OSError, TypeError, sqlite3.Error) as e:
            logger.error_trace(f"Metadata book error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/metadata/book/<provider>/<book_id>/targets", methods=["GET"])
    @login_required
    @_handle_target_errors("Failed to load book targets")
    def api_metadata_book_targets(provider: str, book_id: str) -> Response | tuple[Response, int]:
        """Get provider-managed list/status targets for a specific book."""
        prov = _resolve_metadata_provider(provider)
        if not _session_may_use_provider_account():
            return jsonify({"options": []})
        return jsonify({"options": prov.get_book_targets(book_id)})

    @app.route("/api/metadata/book/<provider>/targets/batch", methods=["POST"])
    @login_required
    @_handle_target_errors("Failed to load book targets")
    def api_metadata_book_targets_batch(provider: str) -> Response | tuple[Response, int]:
        """Get provider-managed list/status targets for multiple books."""
        prov = _resolve_metadata_provider(provider)

        payload = request.get_json(silent=True) or {}
        raw_ids = payload.get("book_ids", []) if isinstance(payload, dict) else []
        if not isinstance(raw_ids, list) or not raw_ids:
            return jsonify({"error": "book_ids must be a non-empty array"}), 400

        book_ids = [str(bid) for bid in raw_ids[:50]]

        if not _session_may_use_provider_account():
            return jsonify({"results": {book_id: [] for book_id in book_ids}})
        return jsonify({"results": prov.get_book_targets_batch(book_ids)})

    @app.route("/api/metadata/book/<provider>/<book_id>/targets", methods=["PUT"])
    @login_required
    @_handle_target_errors("Failed to update book targets")
    def api_metadata_book_targets_update(
        provider: str, book_id: str
    ) -> Response | tuple[Response, int]:
        """Set whether a book belongs to a provider-managed list or shelf."""
        if not _session_may_use_provider_account():
            return jsonify({"error": "Admin access required"}), 403
        prov = _resolve_metadata_provider(provider)

        payload = request.get_json(silent=True) or {}
        target = str(payload.get("target", "")).strip() if isinstance(payload, dict) else ""
        selected = payload.get("selected") if isinstance(payload, dict) else None
        if not target:
            return jsonify({"error": "target is required"}), 400
        if not isinstance(selected, bool):
            return jsonify({"error": "selected must be a boolean"}), 400

        result = prov.set_book_target_state(book_id, target, selected=selected)
        response: dict = {
            "success": True,
            "changed": bool(result.get("changed", True)),
            "selected": selected,
        }
        deselected = result.get("deselected_target")
        if isinstance(deselected, str) and deselected:
            response["deselected_target"] = deselected
        return jsonify(response)

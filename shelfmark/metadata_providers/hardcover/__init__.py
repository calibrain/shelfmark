"""Hardcover metadata provider package."""

from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.config import config as app_config

from .auth import _get_connected_user_id, _get_connected_username, _save_connected_user
from .constants import (
    HARDCOVER_LIST_ID_PREFIX,
    HARDCOVER_STATUS_GROUP,
    HARDCOVER_STATUS_PREFIX,
    HARDCOVER_WRITABLE_TARGET_GROUPS,
)
from .models import HardcoverBookTargetState, HardcoverGraphQLError, HardcoverTargetPayloadError
from .parsing import _compute_search_title, _simplify_author_for_search
from .provider import HardcoverProvider
from .settings import hardcover_settings

__all__ = [
    "HARDCOVER_LIST_ID_PREFIX",
    "HARDCOVER_STATUS_GROUP",
    "HARDCOVER_STATUS_PREFIX",
    "HARDCOVER_WRITABLE_TARGET_GROUPS",
    "HardcoverBookTargetState",
    "HardcoverGraphQLError",
    "HardcoverProvider",
    "HardcoverTargetPayloadError",
    "_compute_search_title",
    "_get_connected_user_id",
    "_get_connected_username",
    "_save_connected_user",
    "_simplify_author_for_search",
    "app_config",
    "get_metadata_cache",
    "hardcover_settings",
]

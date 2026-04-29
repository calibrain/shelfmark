"""Hardcover list/status target read and mutation workflows."""

from typing import TYPE_CHECKING, Any

from shelfmark.core.cache import cache_key
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_int

from .constants import (
    HARDCOVER_LIST_ID_PREFIX,
    HARDCOVER_STATUS_PREFIX,
    HARDCOVER_WRITABLE_TARGET_GROUPS,
)
from .models import HardcoverBookTargetState, HardcoverTargetPayloadError
from .queries import (
    BOOK_TARGET_MEMBERSHIP_BATCH_QUERY,
    BOOK_TARGET_MEMBERSHIP_QUERY,
    DELETE_LIST_BOOK_MUTATION,
    DELETE_USER_BOOK_MUTATION,
    INSERT_LIST_BOOK_MUTATION,
    INSERT_USER_BOOK_MUTATION,
    UPDATE_USER_BOOK_MUTATION,
)

logger = setup_logger(__name__)


def _metadata_cache() -> Any:
    from shelfmark.metadata_providers import hardcover

    return hardcover.get_metadata_cache()


class HardcoverTargetsMixin:
    if TYPE_CHECKING:
        api_key: str

        def _execute_query(
            self,
            query: str,
            variables: dict[str, Any],
            *,
            raise_on_error: bool = False,
        ) -> dict[str, Any] | None: ...

        def _resolve_current_user_id(self) -> str | None: ...

        def get_user_lists(self) -> list[dict[str, str]]: ...

    def get_book_targets(self, book_id: str) -> list[dict[str, Any]]:
        """Get writable Hardcover list/status targets for a specific book."""
        if not self.api_key:
            return []

        book_id_int = coerce_int(book_id, 0)
        if book_id_int < 1:
            msg = "book_id must be a valid Hardcover book id"
            raise ValueError(msg)

        state = self._fetch_book_target_state(book_id_int)
        options: list[dict[str, Any]] = [
            dict(option)
            for option in self.get_user_lists()
            if option.get("group") in HARDCOVER_WRITABLE_TARGET_GROUPS
        ]

        for option in options:
            value = str(option.get("value") or "").strip()
            option["checked"] = self._is_target_checked(value, state)
            option["writable"] = True

        return options

    def set_book_target_state(
        self,
        book_id: str,
        target: str,
        *,
        selected: bool,
    ) -> dict[str, Any]:
        """Set whether a Hardcover book belongs to a status shelf or user list."""
        if not self.api_key:
            msg = "Hardcover is not configured"
            raise ValueError(msg)

        book_id_int = coerce_int(book_id, 0)
        if book_id_int < 1:
            msg = "book_id must be a valid Hardcover book id"
            raise ValueError(msg)

        selected_target = str(target or "").strip()
        if not selected_target:
            msg = "target is required"
            raise ValueError(msg)

        if selected_target not in self._get_writable_targets():
            msg = "Unsupported Hardcover target"
            raise ValueError(msg)

        state = self._fetch_book_target_state(book_id_int)
        status_ids_to_invalidate: set[int] = set()
        list_ids_to_invalidate: set[int] = set()
        deselected_target: str | None = None

        if selected_target.startswith(HARDCOVER_STATUS_PREFIX):
            status_id = self._parse_prefixed_int(selected_target, "status target")
            previous_status_id = state.status_id
            changed = self._set_status_target_state(
                book_id_int,
                status_id,
                selected=selected,
                state=state,
            )
            if changed:
                if previous_status_id is not None:
                    status_ids_to_invalidate.add(previous_status_id)
                    if selected and previous_status_id != status_id:
                        deselected_target = f"{HARDCOVER_STATUS_PREFIX}{previous_status_id}"
                status_ids_to_invalidate.add(status_id)
        elif selected_target.startswith(HARDCOVER_LIST_ID_PREFIX):
            list_id = self._parse_prefixed_int(selected_target, "list target")
            changed = self._set_list_target_state(
                book_id_int,
                list_id,
                selected=selected,
                state=state,
            )
            if changed:
                list_ids_to_invalidate.add(list_id)
        else:
            msg = "Unsupported Hardcover target"
            raise ValueError(msg)

        if changed:
            self._invalidate_book_target_caches(
                connected_user_id=self._resolve_current_user_id(),
                status_ids=status_ids_to_invalidate,
                list_ids=list_ids_to_invalidate,
            )

        result_data: dict[str, Any] = {"changed": changed}
        if deselected_target:
            result_data["deselected_target"] = deselected_target
        return result_data

    @staticmethod
    def _unwrap_me_data(result: dict | None) -> dict:
        """Extract and validate the ``me`` payload from a GraphQL result."""
        if not isinstance(result, dict):
            msg = "Hardcover could not load book targets"
            raise HardcoverTargetPayloadError(msg)

        me_data = result.get("me", {})
        if isinstance(me_data, list) and me_data:
            me_data = me_data[0]
        if not isinstance(me_data, dict):
            msg = "Hardcover returned an invalid target payload"
            raise HardcoverTargetPayloadError(msg)
        return me_data

    def _fetch_book_target_state(self, book_id: int) -> HardcoverBookTargetState:
        """Load current Hardcover membership state for a specific book."""
        result = self._execute_query(
            BOOK_TARGET_MEMBERSHIP_QUERY,
            {"bookId": book_id},
            raise_on_error=True,
        )
        me_data = self._unwrap_me_data(result)

        user_book_id: int | None = None
        status_id: int | None = None
        user_books = me_data.get("user_books", [])
        if isinstance(user_books, list) and user_books:
            latest_user_book = user_books[0] if isinstance(user_books[0], dict) else {}
            user_book_id = coerce_int(latest_user_book.get("id"), 0) or None
            status_id = coerce_int(latest_user_book.get("status_id"), 0) or None

        list_book_ids: dict[int, int] = {}
        for user_list in me_data.get("lists", []):
            if not isinstance(user_list, dict):
                continue
            list_id = coerce_int(user_list.get("id"), 0)
            if list_id < 1:
                continue

            list_books = user_list.get("list_books", [])
            if not isinstance(list_books, list) or not list_books:
                continue

            list_book = list_books[0] if isinstance(list_books[0], dict) else {}
            list_book_id = coerce_int(list_book.get("id"), 0)
            if list_book_id > 0:
                list_book_ids[list_id] = list_book_id

        return HardcoverBookTargetState(
            user_book_id=user_book_id,
            status_id=status_id,
            list_book_ids=list_book_ids,
        )

    def _fetch_book_target_states_batch(
        self,
        book_ids: list[int],
    ) -> dict[int, HardcoverBookTargetState]:
        """Load Hardcover membership state for multiple books in one query."""
        result = self._execute_query(
            BOOK_TARGET_MEMBERSHIP_BATCH_QUERY,
            {"bookIds": book_ids},
            raise_on_error=True,
        )
        me_data = self._unwrap_me_data(result)

        # Group user_books by book_id (keep only the latest per book)
        user_book_by_book: dict[int, dict] = {}
        for ub in me_data.get("user_books", []):
            if not isinstance(ub, dict):
                continue
            bid = coerce_int(ub.get("book_id"), 0)
            if bid > 0 and bid not in user_book_by_book:
                user_book_by_book[bid] = ub

        # Group list_book memberships by book_id
        list_book_ids_by_book: dict[int, dict[int, int]] = {}
        for user_list in me_data.get("lists", []):
            if not isinstance(user_list, dict):
                continue
            list_id = coerce_int(user_list.get("id"), 0)
            if list_id < 1:
                continue
            for lb in user_list.get("list_books", []):
                if not isinstance(lb, dict):
                    continue
                bid = coerce_int(lb.get("book_id"), 0)
                lb_id = coerce_int(lb.get("id"), 0)
                if bid > 0 and lb_id > 0:
                    list_book_ids_by_book.setdefault(bid, {})[list_id] = lb_id

        states: dict[int, HardcoverBookTargetState] = {}
        for bid in book_ids:
            ub = user_book_by_book.get(bid)
            states[bid] = HardcoverBookTargetState(
                user_book_id=coerce_int(ub.get("id"), 0) or None if ub else None,
                status_id=coerce_int(ub.get("status_id"), 0) or None if ub else None,
                list_book_ids=list_book_ids_by_book.get(bid, {}),
            )
        return states

    def get_book_targets_batch(self, book_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """Get writable Hardcover list/status targets for multiple books."""
        if not self.api_key or not book_ids:
            return {bid: [] for bid in book_ids}

        int_ids = []
        id_map: dict[int, str] = {}
        for bid in book_ids:
            int_id = coerce_int(bid, 0)
            if int_id > 0:
                int_ids.append(int_id)
                id_map[int_id] = bid

        if not int_ids:
            return {bid: [] for bid in book_ids}

        states = self._fetch_book_target_states_batch(int_ids)
        writable_options: list[dict[str, Any]] = [
            dict(option)
            for option in self.get_user_lists()
            if option.get("group") in HARDCOVER_WRITABLE_TARGET_GROUPS
        ]

        results: dict[str, list[dict[str, Any]]] = {}
        for int_id, str_id in id_map.items():
            state = states.get(
                int_id,
                HardcoverBookTargetState(
                    user_book_id=None,
                    status_id=None,
                    list_book_ids={},
                ),
            )
            options = [dict(opt) for opt in writable_options]
            for option in options:
                value = str(option.get("value") or "").strip()
                option["checked"] = self._is_target_checked(value, state)
                option["writable"] = True
            results[str_id] = options

        # Fill in any book_ids that didn't parse as valid ints
        for bid in book_ids:
            if bid not in results:
                results[bid] = []

        return results

    def _get_writable_targets(self) -> set[str]:
        """Return the set of writable Hardcover targets for the current user."""
        writable_targets: set[str] = set()
        for option in self.get_user_lists():
            value = str(option.get("value") or "").strip()
            if (
                option.get("group") in HARDCOVER_WRITABLE_TARGET_GROUPS
                and value
                and value.startswith((HARDCOVER_STATUS_PREFIX, HARDCOVER_LIST_ID_PREFIX))
            ):
                writable_targets.add(value)
        return writable_targets

    def _is_target_checked(self, target: str, state: HardcoverBookTargetState) -> bool:
        """Return whether a target is currently selected for the book."""
        if target.startswith(HARDCOVER_STATUS_PREFIX):
            return state.status_id == self._parse_prefixed_int(target)
        if target.startswith(HARDCOVER_LIST_ID_PREFIX):
            return self._parse_prefixed_int(target) in state.list_book_ids
        return False

    def _set_status_target_state(
        self,
        book_id: int,
        status_id: int,
        *,
        selected: bool,
        state: HardcoverBookTargetState,
    ) -> bool:
        """Set whether the book belongs to a Hardcover status shelf."""
        if selected:
            if state.user_book_id is None:
                result = self._execute_query(
                    INSERT_USER_BOOK_MUTATION,
                    {"bookId": book_id, "statusId": status_id},
                    raise_on_error=True,
                )
                self._check_mutation_result(result, "insert_user_book")
                return True

            if state.status_id == status_id:
                return False

            result = self._execute_query(
                UPDATE_USER_BOOK_MUTATION,
                {"userBookId": state.user_book_id, "statusId": status_id},
                raise_on_error=True,
            )
            self._check_mutation_result(result, "update_user_book")
            return True

        if state.user_book_id is None or state.status_id != status_id:
            return False

        result = self._execute_query(
            DELETE_USER_BOOK_MUTATION,
            {"userBookId": state.user_book_id},
            raise_on_error=True,
        )
        self._check_mutation_result(result, "delete_user_book", check_error=False)
        return True

    def _set_list_target_state(
        self,
        book_id: int,
        list_id: int,
        *,
        selected: bool,
        state: HardcoverBookTargetState,
    ) -> bool:
        """Set whether the book belongs to a Hardcover list."""
        list_book_id = state.list_book_ids.get(list_id)

        if selected:
            if list_book_id is not None:
                return False

            result = self._execute_query(
                INSERT_LIST_BOOK_MUTATION,
                {"bookId": book_id, "listId": list_id},
                raise_on_error=True,
            )
            self._check_mutation_result(result, "insert_list_book")
            return True

        if list_book_id is None:
            return False

        result = self._execute_query(
            DELETE_LIST_BOOK_MUTATION,
            {"listBookId": list_book_id},
            raise_on_error=True,
        )
        self._check_mutation_result(result, "delete_list_book", check_error=False)
        return True

    def _invalidate_book_target_caches(
        self,
        *,
        connected_user_id: str | None,
        status_ids: set[int],
        list_ids: set[int],
    ) -> None:
        """Invalidate caches affected by a target membership change."""
        metadata_cache = _metadata_cache()

        if connected_user_id:
            metadata_cache.invalidate(cache_key("hardcover:user_lists", connected_user_id))
            for status_id in status_ids:
                metadata_cache.invalidate_prefix(
                    cache_key("hardcover:user_books:status", connected_user_id, status_id)
                )

        for list_id in list_ids:
            metadata_cache.invalidate_prefix(cache_key("hardcover:list:id", list_id))

    @staticmethod
    def _parse_prefixed_int(value: str, label: str = "target") -> int:
        """Parse an integer from a colon-prefixed value like 'status:1' or 'id:42'."""
        try:
            return int(value.split(":", 1)[1])
        except (IndexError, ValueError) as exc:
            msg = f"Invalid Hardcover {label}"
            raise ValueError(msg) from exc

    @staticmethod
    def _check_mutation_result(result: Any, key: str, *, check_error: bool = True) -> None:
        """Raise if a Hardcover mutation failed.

        When *check_error* is True (the default) the ``error`` field inside
        the payload is inspected and surfaced as a ``ValueError``.  Pass
        ``check_error=False`` for delete mutations that don't return an
        error field.
        """
        payload = result.get(key, {}) if isinstance(result, dict) else {}
        if isinstance(payload, dict):
            if check_error:
                error_text = str(payload.get("error") or "").strip()
                if error_text:
                    raise ValueError(error_text)
            if payload.get("id") is not None:
                return
        msg = "Hardcover could not complete this action"
        raise RuntimeError(msg)

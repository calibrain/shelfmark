"""GraphQL transport helpers for Hardcover."""

from http import HTTPStatus
from typing import Any

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.download.network import get_ssl_verify

from .constants import HARDCOVER_API_URL
from .models import HardcoverGraphQLError

logger = setup_logger(__name__)


def _extract_graphql_error_message(payload: Any) -> str:
    """Extract a readable message from a GraphQL error payload."""
    if not isinstance(payload, dict):
        return ""

    errors = payload.get("errors", [])
    if not isinstance(errors, list):
        return ""

    messages: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        message = str(error.get("message") or "").strip()
        if message:
            messages.append(message)

    return "; ".join(messages)


class HardcoverClientMixin:
    session: requests.Session

    def _execute_query(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        raise_on_error: bool = False,
    ) -> dict | None:
        """Execute a GraphQL query and return data or None on error."""

        def _raise_graphql_error(message: str) -> None:
            raise HardcoverGraphQLError(message)

        try:
            response = self.session.post(
                HARDCOVER_API_URL,
                json={"query": query, "variables": variables},
                timeout=15,
                verify=get_ssl_verify(HARDCOVER_API_URL),
            )
            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                logger.error("GraphQL errors: %s", data["errors"])
                if raise_on_error:
                    message = (
                        _extract_graphql_error_message(data) or "Hardcover rejected this request"
                    )
                    _raise_graphql_error(message)
                return None

            return data.get("data")

        except requests.Timeout as e:
            logger.warning("Hardcover API request timed out")
            if raise_on_error:
                msg = "Hardcover API request timed out"
                raise RuntimeError(msg) from e
            return None
        except requests.HTTPError as e:
            if e.response.status_code == HTTPStatus.UNAUTHORIZED:
                logger.exception("Hardcover API key is invalid")
                if raise_on_error:
                    msg = "Hardcover API key is invalid"
                    raise RuntimeError(msg) from e
            else:
                logger.exception("Hardcover API HTTP error")
                if raise_on_error:
                    msg = f"Hardcover API HTTP error: {e}"
                    raise RuntimeError(msg) from e
            return None
        except HardcoverGraphQLError:
            raise
        except ValueError as e:
            logger.exception("Hardcover API returned invalid JSON")
            if raise_on_error:
                msg = "Hardcover API returned an invalid response"
                raise RuntimeError(msg) from e
            return None
        except (TypeError, requests.RequestException) as e:
            logger.exception("Hardcover API request failed")
            if raise_on_error:
                msg = "Hardcover API request failed"
                raise RuntimeError(msg) from e
            return None

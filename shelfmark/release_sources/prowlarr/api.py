"""Prowlarr API client for connection testing, indexer listing, and search."""

from typing import Any, Dict, List, Optional, Tuple

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml

logger = setup_logger(__name__)


class ProwlarrClient:
    """Client for interacting with the Prowlarr API."""

    def __init__(self, url: str, api_key: str, timeout: int = 30):
        self.base_url = normalize_http_url(url)
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({
            "X-Api-Key": api_key,
            "Accept": "application/json",
        })

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Make an API request to Prowlarr. Returns parsed JSON response."""
        url = self.base_url + endpoint
        logger.debug(f"Prowlarr API: {method} {url}")

        try:
            response = self._session.request(
                method=method,
                url=url,
                params=params,
                json=json_data,
                timeout=self.timeout,
                verify=get_ssl_verify(url),
            )

            if not response.ok:
                try:
                    error_body = response.text[:500]
                    logger.error(f"Prowlarr API error response: {error_body}")
                except Exception:
                    pass

            response.raise_for_status()
            return response.json()

        except requests.exceptions.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Prowlarr: {e}")
            raise ValueError(f"Invalid JSON response: {e}")
        except requests.exceptions.HTTPError as e:
            logger.error(f"Prowlarr API HTTP error: {e.response.status_code} {e.response.reason}")
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Prowlarr API request failed: {e}")
            raise

    def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Prowlarr. Returns (success, message)."""
        logger.info(f"Testing Prowlarr connection to: {self.base_url}")
        try:
            data = self._request("GET", "/api/v1/system/status")
            version = data.get("version", "unknown")
            logger.info(f"Prowlarr connection successful: version {version}")
            return True, f"Connected to Prowlarr {version}"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect to Prowlarr. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if e.response is not None and e.response.status_code == 401:
                return False, "Invalid API key"
            return False, f"HTTP error {status}"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"

    def get_indexers(self) -> List[Dict[str, Any]]:
        """Get all configured indexers."""
        try:
            indexers = self._request("GET", "/api/v1/indexer")
            return indexers
        except Exception as e:
            logger.error(f"Failed to get indexers: {e}")
            return []

    def get_enabled_indexers_detailed(self) -> List[Dict[str, Any]]:
        """
        Get enabled indexers, including implementation metadata.

        Note: Prowlarr indexer "name" is user-configurable; prefer
        "implementation"/"implementationName" for stable identification.
        """
        indexers = self.get_indexers()
        return [idx for idx in indexers if idx.get("enable", False)]

    def get_enriched_indexer_ids(self, *, restrict_to: Optional[List[int]] = None) -> List[int]:
        """
        Return enabled indexer IDs that should use Torznab for richer metadata.

        Args:
            restrict_to: Optional list of candidate indexer IDs to consider.
        """
        enriched_ids: List[int] = []

        for idx in self.get_enabled_indexers_detailed():
            idx_id = idx.get("id")
            if idx_id is None:
                continue
            try:
                idx_id_int = int(idx_id)
            except (TypeError, ValueError):
                continue

            if restrict_to is not None and idx_id_int not in restrict_to:
                continue

            impl = str(idx.get("implementation") or idx.get("implementationName") or idx.get("definitionName") or "")
            # Currently only MyAnonamouse provides consistently rich Torznab metadata.
            if impl.strip().lower() == "myanonamouse":
                enriched_ids.append(idx_id_int)

        return enriched_ids

    def get_enabled_indexers(self) -> List[Dict[str, Any]]:
        """Get enabled indexers with book capability info."""
        indexers = self.get_indexers()
        result = []

        for idx in indexers:
            if not idx.get("enable", False):
                continue

            # Check for book categories (7000-7999 range)
            categories = idx.get("capabilities", {}).get("categories", [])
            has_books = self._has_book_categories(categories)

            result.append({
                "id": idx.get("id"),
                "name": idx.get("name"),
                "protocol": idx.get("protocol"),
                "has_books": has_books,
            })

        return result

    def torznab_search(
        self,
        *,
        indexer_id: int,
        query: str,
        categories: Optional[List[int]] = None,
        search_type: str = "book",
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Search a specific indexer via Prowlarr's Torznab/Newznab endpoint.

        This returns richer fields (e.g., author/booktitle, torznab tags like
        FreeLeech) than the JSON /api/v1/search endpoint.
        """
        if not query:
            return []

        endpoint = f"/api/v1/indexer/{int(indexer_id)}/newznab"
        url = self.base_url + endpoint

        params: Dict[str, Any] = {
            "t": search_type,
            "q": query,
            "limit": limit,
            "offset": offset,
        }
        if categories:
            params["cat"] = ",".join(str(c) for c in categories)

        logger.debug(f"Prowlarr API: GET {url} (torznab)")

        try:
            response = self._session.get(
                url=url,
                params=params,
                timeout=self.timeout,
                headers={
                    # Override the session default JSON accept header.
                    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
                },
                verify=get_ssl_verify(url),
            )
            if not response.ok:
                try:
                    error_body = response.text[:500]
                    logger.error(f"Prowlarr Torznab error response: {error_body}")
                except Exception:
                    pass
            response.raise_for_status()

            results = parse_torznab_xml(response.text)
            # Ensure indexerId is always set (Prowlarr includes it, but be defensive).
            for r in results:
                if r.get("indexerId") is None:
                    r["indexerId"] = int(indexer_id)
            return results
        except Exception as e:
            logger.error(f"Prowlarr Torznab search failed for indexer {indexer_id}: {e}")
            return []

    def _has_book_categories(self, categories: List[Dict[str, Any]]) -> bool:
        """Check if any category or subcategory is in the book range (7000-7999)."""
        for cat in categories:
            cat_id = cat.get("id", 0)
            if 7000 <= cat_id <= 7999:
                return True
            for subcat in cat.get("subCategories", []):
                if 7000 <= subcat.get("id", 0) <= 7999:
                    return True
        return False

    def search(
        self,
        query: str,
        indexer_ids: Optional[List[int]] = None,
        categories: Optional[List[int]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Search for releases via Prowlarr."""
        if not query:
            return []

        params: Dict[str, Any] = {"query": query, "limit": limit}
        if indexer_ids:
            params["indexerIds"] = indexer_ids
        if categories:
            params["categories"] = categories

        try:
            results = self._request("GET", "/api/v1/search", params=params)
            return results if isinstance(results, list) else []
        except Exception as e:
            logger.error(f"Prowlarr search failed: {e}")
            return []

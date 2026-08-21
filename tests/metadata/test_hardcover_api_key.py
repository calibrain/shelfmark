import pytest

from shelfmark.metadata_providers import hardcover
from shelfmark.metadata_providers.hardcover import (
    HardcoverProvider,
    _test_hardcover_connection,
)

# Hardcover replaced its ~500 char JWTs with short opaque tokens.
PAT = "hc_pat_" + "a" * 32


@pytest.fixture(autouse=True)
def _no_config_writes(monkeypatch):
    """Keep the connection test from touching the on-disk provider config."""
    monkeypatch.setattr(hardcover, "_save_connected_user", lambda user_id, username: None)


class TestHardcoverApiKey:
    def test_personal_access_token_is_accepted(self, monkeypatch):
        monkeypatch.setattr(
            HardcoverProvider,
            "_execute_query",
            lambda self, query, variables: {"me": [{"id": 1, "username": "alex"}]},
        )

        result = _test_hardcover_connection({"HARDCOVER_API_KEY": PAT})

        assert result == {"success": True, "message": "Connected as: alex"}

    def test_short_key_without_the_prefix_is_rejected(self):
        result = _test_hardcover_connection({"HARDCOVER_API_KEY": "eyJhbGciOiJIUzI1NiJ9.short"})

        assert result["success"] is False
        assert "too short" in result["message"]

    def test_short_prefixed_key_still_reaches_the_api(self, monkeypatch):
        """A key wearing the hc_pat_ prefix is Hardcover's to accept or reject."""
        monkeypatch.setattr(
            HardcoverProvider,
            "_execute_query",
            lambda self, query, variables: None,
        )

        result = _test_hardcover_connection({"HARDCOVER_API_KEY": "hc_pat_ab"})

        assert result == {"success": False, "message": "API request failed - check your API key"}

    @pytest.mark.parametrize("pasted", [f"Bearer {PAT}", f"bearer {PAT}", f"  {PAT}  "])
    def test_pasted_auth_header_noise_is_stripped(self, pasted):
        provider = HardcoverProvider(api_key=pasted)

        assert provider.session.headers["Authorization"] == f"Bearer {PAT}"

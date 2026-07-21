"""Tests for the OIDC Test Connection handler."""

from unittest.mock import MagicMock, patch

from shelfmark.config.security_handlers import check_oidc_connection

DISCOVERY_URL = "https://auth.example.com/.well-known/openid-configuration"

DISCOVERY_DOCUMENT = {
    "issuer": "https://auth.example.com",
    "authorization_endpoint": "https://auth.example.com/authorize",
    "token_endpoint": "https://auth.example.com/token",
    "jwks_uri": "https://auth.example.com/jwks",
}


def _mock_response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _run_check(responses):
    """Run check_oidc_connection with requests.get returning the given responses."""
    with (
        patch("requests.get", side_effect=responses) as mock_get,
        patch("shelfmark.config.security_handlers.get_ssl_verify", return_value=True),
    ):
        result = check_oidc_connection(
            load_security_config=lambda: {"OIDC_DISCOVERY_URL": DISCOVERY_URL},
            current_values={},
            logger=MagicMock(),
        )
    return result, mock_get


class TestCheckOIDCConnection:
    def test_succeeds_when_discovery_and_jwks_are_valid(self):
        responses = [
            _mock_response(DISCOVERY_DOCUMENT),
            _mock_response({"keys": [{"kty": "RSA", "kid": "abc"}]}),
        ]
        result, mock_get = _run_check(responses)
        assert result["success"] is True
        assert "Connected to" in result["message"]
        jwks_call = mock_get.call_args_list[1]
        assert jwks_call.args[0] == DISCOVERY_DOCUMENT["jwks_uri"]

    def test_fails_with_signing_key_guidance_when_jwks_is_empty(self):
        responses = [
            _mock_response(DISCOVERY_DOCUMENT),
            _mock_response({}),
        ]
        result, _ = _run_check(responses)
        assert result["success"] is False
        assert "no token signing keys" in result["message"]
        assert "Signing Key" in result["message"]

    def test_fails_with_signing_key_guidance_when_jwks_keys_list_is_empty(self):
        responses = [
            _mock_response(DISCOVERY_DOCUMENT),
            _mock_response({"keys": []}),
        ]
        result, _ = _run_check(responses)
        assert result["success"] is False
        assert "no token signing keys" in result["message"]

    def test_fails_when_discovery_document_missing_jwks_uri(self):
        document = {k: v for k, v in DISCOVERY_DOCUMENT.items() if k != "jwks_uri"}
        responses = [_mock_response(document)]
        result, _ = _run_check(responses)
        assert result["success"] is False
        assert "jwks_uri" in result["message"]

    def test_fails_when_jwks_request_errors(self):
        jwks_response = MagicMock()
        jwks_response.raise_for_status.side_effect = RuntimeError("boom")
        responses = [_mock_response(DISCOVERY_DOCUMENT), jwks_response]
        result, _ = _run_check(responses)
        assert result["success"] is False
        assert "Connection failed" in result["message"]

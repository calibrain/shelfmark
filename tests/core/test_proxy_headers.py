import importlib
from unittest.mock import Mock, patch

import pytest


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.mark.parametrize(
    "headers",
    [
        {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "library.example.com:12345",
        },
        {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "library.example.com",
            "X-Forwarded-Port": "12345",
        },
    ],
)
def test_oidc_redirect_uses_forwarded_external_port(main_module, headers):
    oidc_client = Mock()
    oidc_client.authorize_redirect.return_value = ("", 302)

    with patch(
        "shelfmark.core.oidc_routes._get_oidc_client",
        return_value=(oidc_client, {}),
    ):
        response = main_module.app.test_client().get(
            "/api/auth/oidc/login",
            headers=headers,
        )

    assert response.status_code == 302
    oidc_client.authorize_redirect.assert_called_once_with(
        "https://library.example.com:12345/api/auth/oidc/callback"
    )

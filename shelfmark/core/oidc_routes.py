"""OIDC Flask route handlers using Authlib.

Registers /api/auth/oidc/login and /api/auth/oidc/callback endpoints.
Business logic remains in oidc_auth.py.
"""

from typing import Any
from urllib.parse import quote

from authlib.jose.errors import InvalidClaimError
from authlib.integrations.flask_client import OAuth
from flask import Flask, jsonify, redirect, request, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.oidc_auth import (
    extract_user_info,
    parse_group_claims,
    provision_oidc_user,
)
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)
oauth = OAuth()


def _normalize_claims(raw_claims: Any) -> dict[str, Any]:
    """Return a plain dict for claims from Authlib token/userinfo payloads."""
    if raw_claims is None:
        return {}
    if isinstance(raw_claims, dict):
        return raw_claims
    if hasattr(raw_claims, "to_dict"):
        return raw_claims.to_dict()  # type: ignore[no-any-return]
    try:
        return dict(raw_claims)
    except Exception:
        return {}


def _has_username_or_email(claims: dict[str, Any]) -> bool:
    """Return True when claims include a usable username or email."""
    for key in ("preferred_username", "email"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def _login_error_url(message: str) -> str:
    """Build a login URL (with script_root) that includes an OIDC error message."""
    script_root = request.script_root.rstrip("/")
    login_url = f"{script_root}/login" if script_root else "/login"
    return f"{login_url}?oidc_error={quote(message)}"


def _get_oidc_client() -> tuple[Any, dict[str, Any]]:
    """Register and return an OIDC client from the current security config."""
    config = load_config_file("security")
    discovery_url = config.get("OIDC_DISCOVERY_URL", "")
    client_id = config.get("OIDC_CLIENT_ID", "")

    if not discovery_url or not client_id:
        raise ValueError("OIDC not configured")

    configured_scopes = config.get("OIDC_SCOPES", ["openid", "email", "profile"])
    if isinstance(configured_scopes, list):
        scope_values = [str(scope).strip() for scope in configured_scopes if str(scope).strip()]
    elif isinstance(configured_scopes, str):
        delimiter = "," if "," in configured_scopes else " "
        scope_values = [scope.strip() for scope in configured_scopes.split(delimiter) if scope.strip()]
    else:
        scope_values = []

    scopes = list(dict.fromkeys(["openid"] + scope_values))

    admin_group = config.get("OIDC_ADMIN_GROUP", "")
    group_claim = config.get("OIDC_GROUP_CLAIM", "groups")
    use_admin_group = config.get("OIDC_USE_ADMIN_GROUP", True)
    if admin_group and use_admin_group and group_claim and group_claim not in scopes:
        scopes.append(group_claim)

    def _ssl_compliance_fix(session, **kwargs):
        """Set session.verify based on the Certificate Validation setting."""
        session.verify = get_ssl_verify(discovery_url)
        return session

    oauth._clients.pop("shelfmark_idp", None)
    oauth.register(
        name="shelfmark_idp",
        client_id=client_id,
        client_secret=config.get("OIDC_CLIENT_SECRET", ""),
        server_metadata_url=discovery_url,
        client_kwargs={
            "scope": " ".join(scopes),
            "code_challenge_method": "S256",
        },
        compliance_fix=_ssl_compliance_fix,
        overwrite=True,
    )

    client = oauth.create_client("shelfmark_idp")
    if client is None:
        raise RuntimeError("OIDC client initialization failed")

    return client, config


def register_oidc_routes(app: Flask, user_db: UserDB) -> None:
    """Register OIDC authentication routes on the Flask app."""
    oauth.init_app(app)

    @app.route("/api/auth/oidc/login", methods=["GET"])
    def oidc_login():
        """Initiate OIDC login flow and redirect to the provider."""
        try:
            client, _ = _get_oidc_client()
            redirect_uri = request.url_root.rstrip("/") + "/api/auth/oidc/callback"
            return client.authorize_redirect(redirect_uri)
        except ValueError:
            return jsonify({"error": "OIDC not configured"}), 500
        except Exception as e:
            logger.error(f"OIDC login error: {e}")
            return jsonify({"error": "OIDC login failed"}), 500

    @app.route("/api/auth/oidc/callback", methods=["GET"])
    def oidc_callback():
        """Handle OIDC callback from identity provider."""
        try:
            error = request.args.get("error")
            if error:
                logger.warning(f"OIDC callback error from IdP: {error}")
                return redirect(_login_error_url("Authentication failed"))

            client, config = _get_oidc_client()
            try:
                token = client.authorize_access_token()
            except InvalidClaimError as e:
                claim_name = getattr(e, "claim_name", "unknown")
                discovery_url = str(config.get("OIDC_DISCOVERY_URL", ""))
                provider_issuer = ""
                try:
                    metadata = client.load_server_metadata()
                    if isinstance(metadata, dict):
                        provider_issuer = str(metadata.get("issuer", ""))
                except Exception as metadata_error:
                    logger.debug(f"OIDC metadata lookup failed during claim diagnostics: {metadata_error}")

                logger.error(
                    "OIDC callback claim validation failed: claim=%s error=%s discovery_url=%s provider_issuer=%s",
                    claim_name,
                    e,
                    discovery_url or "<unset>",
                    provider_issuer or "<unknown>",
                )
                if claim_name == "iss":
                    msg = (
                        "OIDC issuer validation failed. Verify your discovery URL and IdP issuer/"
                        "external URL configuration."
                    )
                    return redirect(_login_error_url(msg))

                return redirect(_login_error_url(f"OIDC token claim validation failed: {claim_name}"))
            claims = _normalize_claims(token.get("userinfo"))

            # If userinfo is missing or claims are too sparse, request it explicitly.
            if not claims or not _has_username_or_email(claims):
                fetched_claims: dict[str, Any] = {}
                try:
                    fetched_claims = _normalize_claims(client.userinfo(token=token))
                except TypeError:
                    fetched_claims = _normalize_claims(client.userinfo())
                except Exception as e:
                    logger.error(f"Failed to fetch OIDC userinfo: {e}")
                if fetched_claims:
                    claims = {**claims, **fetched_claims}

            if not claims:
                msg = "OIDC authentication failed: missing user claims"
                logger.error(msg)
                return redirect(_login_error_url(msg))

            group_claim = config.get("OIDC_GROUP_CLAIM", "groups")
            admin_group = config.get("OIDC_ADMIN_GROUP", "")
            use_admin_group = config.get("OIDC_USE_ADMIN_GROUP", True)
            auto_provision = config.get("OIDC_AUTO_PROVISION", True)

            user_info = extract_user_info(claims)
            groups = parse_group_claims(claims, group_claim)

            is_admin = None
            if admin_group and use_admin_group:
                is_admin = admin_group in groups

            allow_email_link = bool(user_info.get("email"))
            user = provision_oidc_user(
                user_db,
                user_info,
                is_admin=is_admin,
                allow_email_link=allow_email_link,
                allow_create=bool(auto_provision),
            )
            if user is None:
                logger.warning(
                    f"OIDC login rejected: auto-provision disabled for {user_info['username']}"
                )
                return redirect(_login_error_url("Account not found. Contact your administrator."))

            session["user_id"] = user["username"]
            session["is_admin"] = user.get("role") == "admin"
            session["db_user_id"] = user["id"]
            session.permanent = True

            logger.info(f"OIDC login successful: {user['username']} (admin={is_admin})")
            return redirect(request.script_root or "/")

        except ValueError as e:
            logger.error(f"OIDC callback error: {e}")
            return redirect(_login_error_url(str(e)))
        except Exception as e:
            logger.error(f"OIDC callback error: {e}")
            return redirect(_login_error_url("Authentication failed"))

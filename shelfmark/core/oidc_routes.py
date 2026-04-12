"""OIDC Flask route handlers using Authlib.

Registers /api/auth/oidc/login and /api/auth/oidc/callback endpoints.
Business logic remains in oidc_auth.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard
from urllib.parse import urlencode, urlsplit, urlunsplit

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.flask_client import OAuth
from authlib.jose.errors import InvalidClaimError
from flask import Flask, jsonify, redirect, request, session

from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.core.oidc_auth import (
    extract_user_info,
    parse_group_claims,
    provision_oidc_user,
)
from shelfmark.download.network import get_ssl_verify

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue

    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)
oauth = OAuth()
_RETURN_TO_SESSION_KEY = "oidc_return_to"
_OIDC_CLIENT_ERRORS = (OAuthError, OSError, RuntimeError, TypeError, ValueError)


class _ClaimsMappingLike(Protocol):
    """Protocol for Authlib claims payloads that expose a to_dict method."""

    def to_dict(self) -> Mapping[object, object]: ...


def _has_claims_to_dict(candidate: object) -> TypeGuard[_ClaimsMappingLike]:
    """Return True when a claims object exposes a callable to_dict method."""
    return callable(getattr(candidate, "to_dict", None))


def _normalize_claim_mapping(raw_claims: Mapping[object, object]) -> dict[str, Any]:
    """Return only string-keyed claims for downstream OIDC helpers."""
    return {key: value for key, value in raw_claims.items() if isinstance(key, str)}


def _normalize_claims(raw_claims: object) -> dict[str, Any]:
    """Return a plain dict for claims from Authlib token/userinfo payloads."""
    if raw_claims is None:
        return {}
    if isinstance(raw_claims, Mapping):
        return _normalize_claim_mapping(raw_claims)
    if _has_claims_to_dict(raw_claims):
        converted_claims = raw_claims.to_dict()
        if isinstance(converted_claims, Mapping):
            return _normalize_claim_mapping(converted_claims)
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
    params = {"oidc_error": message}
    return_to = _get_pending_return_to()
    if return_to and return_to != "/":
        params["return_to"] = return_to
    return f"{login_url}?{urlencode(params)}"


def _normalize_return_to(raw_return_to: object) -> str | None:
    """Return a safe app-relative post-login target."""
    if not isinstance(raw_return_to, str):
        return None

    value = raw_return_to.strip()
    if not value or not value.startswith("/") or value.startswith("//"):
        return None

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        return None

    script_root = request.script_root.rstrip("/")
    path = parsed.path or "/"
    if script_root:
        if path == script_root:
            path = "/"
        elif path.startswith(f"{script_root}/"):
            path = path[len(script_root) :] or "/"

    if path in {"/login", "/api"} or path.startswith(("/login/", "/api/")):
        return None

    return urlunsplit(("", "", path, parsed.query, parsed.fragment))


def _get_pending_return_to(*, clear: bool = False) -> str | None:
    """Read the pending post-login target from the session."""
    raw_return_to = (
        session.pop(_RETURN_TO_SESSION_KEY, None) if clear else session.get(_RETURN_TO_SESSION_KEY)
    )
    normalized = _normalize_return_to(raw_return_to)
    if normalized is None and not clear:
        session.pop(_RETURN_TO_SESSION_KEY, None)
    return normalized


def _post_login_redirect_target(return_to: str | None) -> str:
    """Build the final redirect target, honoring script_root when present."""
    normalized = _normalize_return_to(return_to) or "/"
    script_root = request.script_root.rstrip("/")
    if not script_root:
        return normalized
    if normalized == "/":
        return f"{script_root}/"
    return f"{script_root}{normalized}"


def _get_oidc_client() -> tuple[Any, dict[str, Any]]:
    """Register and return an OIDC client from the current security config."""
    discovery_url = str(app_config.get("OIDC_DISCOVERY_URL", "") or "")
    client_id = str(app_config.get("OIDC_CLIENT_ID", "") or "")

    if not discovery_url or not client_id:
        msg = "OIDC not configured"
        raise ValueError(msg)

    configured_scopes = app_config.get("OIDC_SCOPES", ["openid", "email", "profile"])
    if isinstance(configured_scopes, list):
        scope_values = [str(scope).strip() for scope in configured_scopes if str(scope).strip()]
    elif isinstance(configured_scopes, str):
        delimiter = "," if "," in configured_scopes else " "
        scope_values = [
            scope.strip() for scope in configured_scopes.split(delimiter) if scope.strip()
        ]
    else:
        scope_values = []

    scopes = list(dict.fromkeys(["openid", *scope_values]))

    admin_group_value = app_config.get("OIDC_ADMIN_GROUP", "")
    admin_group = admin_group_value.strip() if isinstance(admin_group_value, str) else ""
    group_claim_value = app_config.get("OIDC_GROUP_CLAIM", "groups")
    group_claim = (
        group_claim_value.strip()
        if isinstance(group_claim_value, str) and group_claim_value.strip()
        else "groups"
    )
    use_admin_group = app_config.get("OIDC_USE_ADMIN_GROUP", True)
    if admin_group and use_admin_group and group_claim and group_claim not in scopes:
        scopes.append(group_claim)

    def _ssl_compliance_fix(session: Any, **kwargs: Any) -> Any:
        """Set session.verify based on the Certificate Validation setting."""
        session.verify = get_ssl_verify(discovery_url)
        return session

    oauth._clients.pop("shelfmark_idp", None)
    oauth.register(
        name="shelfmark_idp",
        client_id=client_id,
        client_secret=app_config.get("OIDC_CLIENT_SECRET", ""),
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
        msg = "OIDC client initialization failed"
        raise RuntimeError(msg)

    return client, {
        "OIDC_DISCOVERY_URL": discovery_url,
        "OIDC_GROUP_CLAIM": group_claim,
        "OIDC_ADMIN_GROUP": admin_group,
        "OIDC_AUTO_PROVISION": app_config.get("OIDC_AUTO_PROVISION", True),
        "OIDC_USE_ADMIN_GROUP": use_admin_group,
    }


def register_oidc_routes(app: Flask, user_db: UserDB) -> None:
    """Register OIDC authentication routes on the Flask app."""
    oauth.init_app(app)

    @app.route("/api/auth/oidc/login", methods=["GET"])
    def oidc_login() -> ResponseReturnValue:
        """Initiate OIDC login flow and redirect to the provider."""
        try:
            client, _ = _get_oidc_client()
            return_to = _normalize_return_to(request.args.get("return_to"))
            if return_to and return_to != "/":
                session[_RETURN_TO_SESSION_KEY] = return_to
            else:
                session.pop(_RETURN_TO_SESSION_KEY, None)
            redirect_uri = request.url_root.rstrip("/") + "/api/auth/oidc/callback"
            return client.authorize_redirect(redirect_uri)
        except ValueError:
            return jsonify({"error": "OIDC not configured"}), 500
        except Exception:
            logger.exception("OIDC login error")
            return jsonify({"error": "OIDC login failed"}), 500

    @app.route("/api/auth/oidc/callback", methods=["GET"])
    def oidc_callback() -> ResponseReturnValue:
        """Handle OIDC callback from identity provider."""
        try:
            error = request.args.get("error")
            if error:
                logger.warning("OIDC callback error from IdP: %s", error)
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
                except _OIDC_CLIENT_ERRORS as metadata_error:
                    logger.debug(
                        "OIDC metadata lookup failed during claim diagnostics: %s",
                        metadata_error,
                    )

                logger.exception(
                    "OIDC callback claim validation failed: claim=%s discovery_url=%s provider_issuer=%s",
                    claim_name,
                    discovery_url or "<unset>",
                    provider_issuer or "<unknown>",
                )
                if claim_name == "iss":
                    msg = (
                        "OIDC issuer validation failed. Verify your discovery URL and IdP issuer/"
                        "external URL configuration."
                    )
                    return redirect(_login_error_url(msg))

                return redirect(
                    _login_error_url(f"OIDC token claim validation failed: {claim_name}")
                )
            claims = _normalize_claims(token.get("userinfo"))

            # If userinfo is missing or claims are too sparse, request it explicitly.
            if not claims or not _has_username_or_email(claims):
                fetched_claims: dict[str, Any] = {}
                try:
                    fetched_claims = _normalize_claims(client.userinfo(token=token))
                except TypeError:
                    fetched_claims = _normalize_claims(client.userinfo())
                except Exception:
                    logger.exception("Failed to fetch OIDC userinfo")
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
                    "OIDC login rejected: auto-provision disabled for %s",
                    user_info["username"],
                )
                return redirect(_login_error_url("Account not found. Contact your administrator."))

            session["user_id"] = user["username"]
            session["is_admin"] = user.get("role") == "admin"
            session["db_user_id"] = user["id"]
            session.permanent = True

            logger.info("OIDC login successful: %s (admin=%s)", user["username"], is_admin)
            return redirect(_post_login_redirect_target(_get_pending_return_to(clear=True)))

        except ValueError as e:
            logger.exception("OIDC callback error")
            return redirect(_login_error_url(str(e)))
        except Exception:
            logger.exception("OIDC callback error")
            return redirect(_login_error_url("Authentication failed"))

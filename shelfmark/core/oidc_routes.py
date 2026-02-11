"""OIDC Flask route handlers using Authlib.

Registers /api/auth/oidc/login and /api/auth/oidc/callback endpoints.
Business logic remains in oidc_auth.py.
"""

from typing import Any

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


def _is_email_verified(claims: dict[str, Any]) -> bool:
    """Normalize provider-specific email_verified values into a strict boolean."""
    value = claims.get("email_verified", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


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

    oauth.register(
        name="shelfmark_idp",
        client_id=client_id,
        client_secret=config.get("OIDC_CLIENT_SECRET", ""),
        server_metadata_url=discovery_url,
        client_kwargs={
            "scope": " ".join(scopes),
            "code_challenge_method": "S256",
        },
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
                return jsonify({"error": "Authentication failed"}), 400

            client, config = _get_oidc_client()
            token = client.authorize_access_token()
            claims = _normalize_claims(token.get("userinfo"))

            # If userinfo isn't present in token payload, request it explicitly.
            if not claims:
                try:
                    claims = _normalize_claims(client.userinfo(token=token))
                except TypeError:
                    claims = _normalize_claims(client.userinfo())
                except Exception as e:
                    logger.error(f"Failed to fetch OIDC userinfo: {e}")

            if not claims:
                raise ValueError("OIDC authentication failed: missing user claims")

            group_claim = config.get("OIDC_GROUP_CLAIM", "groups")
            admin_group = config.get("OIDC_ADMIN_GROUP", "")
            use_admin_group = config.get("OIDC_USE_ADMIN_GROUP", True)
            auto_provision = config.get("OIDC_AUTO_PROVISION", True)

            user_info = extract_user_info(claims)
            groups = parse_group_claims(claims, group_claim)

            is_admin = None
            if admin_group and use_admin_group:
                is_admin = admin_group in groups

            # Account linking: OIDC subject first, then verified email.
            existing_user = user_db.get_user(oidc_subject=user_info["oidc_subject"])

            if not existing_user and user_info.get("email") and _is_email_verified(claims):
                matching_users = [
                    u for u in user_db.list_users()
                    if u.get("email") and u["email"].lower() == user_info["email"].lower()
                ]
                if len(matching_users) == 1:
                    existing_user = matching_users[0]
                    user_db.update_user(existing_user["id"], oidc_subject=user_info["oidc_subject"])
                    logger.info(
                        f"Linked OIDC subject {user_info['oidc_subject']} to existing user "
                        f"{existing_user['username']}"
                    )
                elif len(matching_users) > 1:
                    logger.warning(
                        "OIDC email linking skipped: multiple local accounts match email "
                        f"{user_info['email']}"
                    )

            if not existing_user and not auto_provision:
                logger.warning(
                    f"OIDC login rejected: auto-provision disabled for {user_info['username']}"
                )
                return jsonify({"error": "Account not found. Contact your administrator."}), 403

            user = provision_oidc_user(user_db, user_info, is_admin=is_admin)

            session["user_id"] = user["username"]
            session["is_admin"] = user.get("role") == "admin"
            session["db_user_id"] = user["id"]
            session.permanent = True

            logger.info(f"OIDC login successful: {user['username']} (admin={is_admin})")
            return redirect(request.script_root or "/")

        except ValueError as e:
            logger.error(f"OIDC callback error: {e}")
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.error(f"OIDC callback error: {e}")
            return jsonify({"error": "Authentication failed"}), 500

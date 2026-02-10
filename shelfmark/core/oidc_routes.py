"""OIDC Flask route handlers.

Registers /api/auth/oidc/login and /api/auth/oidc/callback endpoints.
Separated from main.py to keep the OIDC logic self-contained.
"""

import hashlib
import secrets
import base64
from urllib.parse import urlencode

import requests as http_requests
from flask import Flask, redirect, request, session, jsonify

from shelfmark.core.logger import setup_logger
from shelfmark.core.oidc_auth import (
    extract_user_info,
    is_admin_from_groups,
    parse_group_claims,
    provision_oidc_user,
)
from shelfmark.core.settings_registry import load_config_file
from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

# Cache discovery document in memory (refreshed on restart)
_discovery_cache = {}


def _fetch_discovery(discovery_url: str) -> dict:
    """Fetch and cache the OIDC discovery document."""
    if discovery_url in _discovery_cache:
        return _discovery_cache[discovery_url]

    resp = http_requests.get(discovery_url, timeout=10)
    resp.raise_for_status()
    doc = resp.json()
    _discovery_cache[discovery_url] = doc
    return doc


def _generate_pkce():
    """Generate PKCE code_verifier and code_challenge."""
    code_verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


def _exchange_code(
    token_endpoint: str,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange authorization code for tokens and return ID token claims."""
    resp = http_requests.post(
        token_endpoint,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    resp.raise_for_status()
    token_data = resp.json()

    # Decode ID token (we trust the IdP since we just exchanged the code over TLS)
    import json as json_mod

    id_token_raw = token_data.get("id_token", "")
    if id_token_raw:
        # Decode JWT payload without verification (already validated by TLS + code exchange)
        payload = id_token_raw.split(".")[1]
        # Add padding
        payload += "=" * (4 - len(payload) % 4)
        claims = json_mod.loads(base64.urlsafe_b64decode(payload))
    else:
        # Fallback to userinfo endpoint if no ID token
        claims = token_data

    return claims


def register_oidc_routes(app: Flask, user_db: UserDB) -> None:
    """Register OIDC authentication routes on the Flask app."""

    @app.route("/api/auth/oidc/login", methods=["GET"])
    def oidc_login():
        """Initiate OIDC login flow. Redirects to IdP."""
        try:
            config = load_config_file("security")
            discovery_url = config.get("OIDC_DISCOVERY_URL", "")
            client_id = config.get("OIDC_CLIENT_ID", "")
            scopes = config.get("OIDC_SCOPES", ["openid", "email", "profile", "groups"])

            if not discovery_url or not client_id:
                return jsonify({"error": "OIDC not configured"}), 500

            discovery = _fetch_discovery(discovery_url)
            auth_endpoint = discovery["authorization_endpoint"]

            # Generate PKCE and state
            code_verifier, code_challenge = _generate_pkce()
            state = secrets.token_urlsafe(32)

            # Store in session for callback validation
            session["oidc_state"] = state
            session["oidc_code_verifier"] = code_verifier

            # Build callback URL
            redirect_uri = request.url_root.rstrip("/") + "/api/auth/oidc/callback"

            params = {
                "client_id": client_id,
                "response_type": "code",
                "scope": " ".join(scopes),
                "redirect_uri": redirect_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }

            return redirect(f"{auth_endpoint}?{urlencode(params)}")

        except Exception as e:
            logger.error(f"OIDC login error: {e}")
            return jsonify({"error": f"OIDC login failed: {str(e)}"}), 500

    @app.route("/api/auth/oidc/callback", methods=["GET"])
    def oidc_callback():
        """Handle OIDC callback from IdP."""
        try:
            code = request.args.get("code")
            state = request.args.get("state")
            error = request.args.get("error")

            if error:
                logger.warning(f"OIDC callback error from IdP: {error}")
                return jsonify({"error": f"Authentication failed: {error}"}), 400

            # Validate state
            expected_state = session.get("oidc_state")
            code_verifier = session.get("oidc_code_verifier")

            if not state or state != expected_state:
                return jsonify({"error": "Invalid state parameter"}), 400

            if not code:
                return jsonify({"error": "Missing authorization code"}), 400

            # Load config
            config = load_config_file("security")
            discovery_url = config.get("OIDC_DISCOVERY_URL", "")
            client_id = config.get("OIDC_CLIENT_ID", "")
            client_secret = config.get("OIDC_CLIENT_SECRET", "")
            group_claim = config.get("OIDC_GROUP_CLAIM", "groups")
            admin_group = config.get("OIDC_ADMIN_GROUP", "")
            auto_provision = config.get("OIDC_AUTO_PROVISION", True)

            discovery = _fetch_discovery(discovery_url)
            token_endpoint = discovery["token_endpoint"]
            redirect_uri = request.url_root.rstrip("/") + "/api/auth/oidc/callback"

            # Exchange code for tokens
            claims = _exchange_code(
                token_endpoint=token_endpoint,
                code=code,
                code_verifier=code_verifier,
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
            )

            # Extract user info and check groups
            user_info = extract_user_info(claims)
            groups = parse_group_claims(claims, group_claim)
            is_admin = is_admin_from_groups(groups, admin_group)

            # Check if user exists by OIDC subject first
            existing_user = user_db.get_user(oidc_subject=user_info["oidc_subject"])

            # If no match by subject, try email (for pre-created users)
            if not existing_user and user_info.get("email"):
                for u in user_db.list_users():
                    if u.get("email") and u["email"].lower() == user_info["email"].lower():
                        existing_user = u
                        # Link OIDC subject to existing user
                        user_db.update_user(u["id"], oidc_subject=user_info["oidc_subject"])
                        logger.info(f"Linked OIDC subject {user_info['oidc_subject']} to existing user {u['username']}")
                        break

            if not existing_user and not auto_provision:
                logger.warning(f"OIDC login rejected: auto-provision disabled for {user_info['username']}")
                return jsonify({"error": "Account not found. Contact your administrator."}), 403

            # Provision or update user
            user = provision_oidc_user(user_db, user_info, is_admin=is_admin)

            # Set session
            session["user_id"] = user["username"]
            session["is_admin"] = is_admin
            session["db_user_id"] = user["id"]
            session.permanent = True

            # Clean up OIDC session data
            session.pop("oidc_state", None)
            session.pop("oidc_code_verifier", None)

            logger.info(f"OIDC login successful: {user['username']} (admin={is_admin})")

            # Redirect to frontend
            return redirect("/")

        except Exception as e:
            logger.error(f"OIDC callback error: {e}")
            return jsonify({"error": f"Authentication failed: {str(e)}"}), 500

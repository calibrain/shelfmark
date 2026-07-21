# OpenID Connect (OIDC) Authentication

## Callback URL

```
https://<your-shelfmark-domain>/api/auth/oidc/callback
```

With a subpath (`URL_BASE=/shelfmark/`):

```
https://<your-shelfmark-domain>/shelfmark/api/auth/oidc/callback
```

The callback URL is constructed from the incoming request, so your reverse proxy must forward `X-Forwarded-Proto` and `X-Forwarded-Host` correctly. PKCE (S256) is used automatically.

## Settings

Configure in **Settings → Security → Authentication Method → OIDC**.

| Setting | Description | Default |
|---------|-------------|---------|
| Discovery URL | `/.well-known/openid-configuration` endpoint | — |
| Client ID | OAuth2 client ID | — |
| Client Secret | OAuth2 client secret | — |
| Scopes | Scopes to request. The group claim is added automatically when admin group authorization is enabled | `openid email profile` |
| Group Claim Name | Claim containing user groups | `groups` |
| Admin Group Name | Group granted admin access. Leave empty for database-only roles | — |
| Use Admin Group for Authorization | Toggle group-based admin detection | `true` |
| Auto-Provision Users | Create accounts on first login | `true` |
| Login Button Label | Custom text for the sign-in button | — |

Use **Test Connection** to verify discovery, client configuration, and the provider's token signing keys (JWKS) before attempting login.

> **Authentik users:** make sure your provider has a **Signing Key** selected (e.g. the default self-signed certificate). Without one, Authentik serves an empty JWKS document and every login fails with an OIDC callback error, even though the discovery document looks healthy.

## Account Linking

On login, Shelfmark matches the OIDC identity to a user account in this order:

1. **OIDC subject** — a user who has logged in through this provider before.
2. **Email** — a local account with the same (unique) email address. This only happens when the provider also asserts `email_verified: true` for the address; an unverified email would let anyone claim a local account by registering its address at the IdP.
3. Otherwise, a new account is created when **Auto-Provision Users** is enabled (username conflicts get a numeric suffix), or the login is rejected with "Account not found" when it is disabled.

If the `email_verified` claim is missing or `false`, email linking is silently skipped — a common surprise when the address was never verified at the identity provider (e.g. Keycloak's **Email verified** toggle on the user, or Authentik accounts created without email verification). Make sure the `email` scope is requested and the address is marked verified in your IdP.

## Environment Variables

These optional environment variables control login page behavior when OIDC is enabled.

| Variable | Description | Default |
|----------|-------------|---------|
| `HIDE_LOCAL_AUTH` | Hide the username/password login option, so only the OIDC button is shown | `false` |
| `DISABLE_LOCAL_AUTH` | Disable username/password login and remove the local-admin prerequisite for OIDC. Implies `HIDE_LOCAL_AUTH`; with `AUTH_METHOD=builtin`, everyone is locked out until auth env vars are changed. | `false` |
| `OIDC_AUTO_REDIRECT` | Automatically redirect to the OIDC provider instead of showing the login page | `false` |

If `DISABLE_LOCAL_AUTH` and `OIDC_AUTO_REDIRECT` are both enabled, users are redirected straight to the OIDC provider. On failure they return to the login page with an error message but no password fallback.

## Troubleshooting

- **No token signing keys (empty JWKS)** — The provider's JWKS endpoint returned no keys, so ID tokens can't be verified. In Authentik this happens when the provider has no **Signing Key** selected; pick one (e.g. the default self-signed certificate) and try again.
- **Issuer validation failed** — The issuer in the token doesn't match the discovery document. Check your provider's external URL / issuer configuration.
- **Callback URL mismatch** — Reverse proxy isn't forwarding `X-Forwarded-Proto` or `X-Forwarded-Host`, so the constructed callback URL doesn't match what's registered in the provider.
- **Account not found** — Auto-provision is disabled and the user hasn't been pre-created by an admin. If you pre-created the account with a matching email, see [Account Linking](#account-linking): the provider must send `email_verified: true` for linking to happen.
- **Login created a duplicate account instead of linking to my local one** — Email linking requires a verified email; see [Account Linking](#account-linking). With `DEBUG=true`, the log notes when linking is skipped because the address isn't verified.

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

Use **Test Connection** to verify discovery and client configuration before attempting login.

## Troubleshooting

- **Issuer validation failed** — The issuer in the token doesn't match the discovery document. Check your provider's external URL / issuer configuration.
- **Callback URL mismatch** — Reverse proxy isn't forwarding `X-Forwarded-Proto` or `X-Forwarded-Host`, so the constructed callback URL doesn't match what's registered in the provider.
- **Account not found** — Auto-provision is disabled and the user hasn't been pre-created by an admin.

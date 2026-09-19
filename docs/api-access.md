# API access with personal API keys

Shelfmark's web interface is driven entirely by a JSON API under `/api/`. A
personal API key lets scripts, dashboards and assistants call the same API
without a browser session.

A key acts **exactly as the user who created it**: same role, request policy
and delivery settings. To give an integration limited access, create the key
on a non-admin user.

## Create a key

1. Open **My Account** (your avatar in the header).
2. Under **API keys**, enter a name, choose an expiry, and click **Create key**.
3. Copy the key. It is shown once and only its prefix is stored.

Admins can see and revoke any user's keys from **Settings → Users → edit**.
Admins can also turn keys off for everyone with **Settings → Security →
Allow personal API keys**; existing keys are kept and work again when re-enabled.

## Send the key

Either header works; `Authorization` wins if both are present.

```bash
curl -s -H "Authorization: Bearer smk_…" https://shelfmark.example.com/api/downloads/active
curl -s -H "X-Api-Key: smk_…" https://shelfmark.example.com/api/downloads/active
```

A request that carries a key is authenticated by the key alone. Session
cookies are ignored and none are set. An invalid, expired or revoked key
returns `401 {"error": "Invalid or expired API key"}`.

## Examples

Find a book with a metadata provider. Each result carries `provider` and
`provider_id`, which the release search below takes as `provider` and
`book_id`:

```bash
curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" \
  "https://shelfmark.example.com/api/metadata/search?query=dune"
```

Search for releases of that book (`provider` and `book_id` are required;
`content_type` defaults to `ebook`, use `audiobook` for audiobooks):

```bash
curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" \
  "https://shelfmark.example.com/api/releases?provider=hardcover&book_id=12345&content_type=ebook"
```

Queue a download by posting one release object from the search result above.
`source` and `source_id` are required; `title`, `format`, `size` and `extra`
carry through from the release, and `content_type` or `priority` can be added
or overridden:

```bash
curl -s -X POST -H "Authorization: Bearer $SHELFMARK_API_KEY" \
  -H "Content-Type: application/json" \
  -d @release.json \
  https://shelfmark.example.com/api/releases/download
```

Poll progress:

```bash
curl -s -H "Authorization: Bearer $SHELFMARK_API_KEY" https://shelfmark.example.com/api/status
```

## Security notes

- Keys carry 256 bits of entropy and are stored as SHA-256 hashes.
- Revocation takes effect on the next request.
- Role changes apply immediately: a key follows the user's current role.
- WebSocket (live activity) connections do not accept keys; poll `/api/status` instead.

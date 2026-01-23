# Reverse Proxy & Subpath Hosting

Shelfmark can run behind a reverse proxy at the root path (recommended) or
under a subpath like `/shelfmark`.

## Subpath setup

1) Set the base path in Shelfmark:
- UI: Settings → Advanced → Base Path
- Env var: `URL_BASE=/shelfmark`

2) Configure your reverse proxy to forward the subpath to Shelfmark and
**strip the prefix** before sending to the backend. The proxy must also allow
WebSocket upgrades for Socket.IO.

Example (Nginx-style):

```
location /shelfmark/ {
  proxy_pass http://shelfmark:8084/;
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
}
```

Notes:
- Use a trailing slash on the `location` and `proxy_pass` to ensure the
  `/shelfmark` prefix is removed.
- Health checks still work at `/api/health` without the subpath.

## Root path setup

If you can serve Shelfmark at the root path (`https://shelfmark.example.com/`),
leave `URL_BASE` empty. This is the simplest option.

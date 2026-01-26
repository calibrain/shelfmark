# Reverse Proxy & Subpath Hosting

Shelfmark can run behind a reverse proxy at the root path (recommended) or under a subpath like `/shelfmark`.

## Root path setup (Recommended)

If you can serve Shelfmark at the root path (`https://shelfmark.example.com/`), leave `URL_BASE` empty. This is the simplest option and avoids the workarounds needed for subpath deployments.

```nginx
server {
    listen 443 ssl;
    server_name shelfmark.example.com;

    location / {
        proxy_pass http://shelfmark:8084;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## Subpath setup

Running Shelfmark under a subpath like `/shelfmark` requires additional configuration due to how the frontend generates certain URLs.

### 1. Set the base path in Shelfmark

- **UI**: Settings → Advanced → Base Path → `/shelfmark/`
- **Environment variable**: `URL_BASE=/shelfmark/`

### 2. Configure your reverse proxy

The frontend generates some URLs at the root level (`/socket.io/`, `/api/`, `/logo.png`) regardless of the `URL_BASE` setting. You'll need to add proxy rules to handle these paths.

---

### Without Authentication Proxy

**Complete Nginx configuration for subpath deployment:**

```nginx
# Redirect /logo.png to subpath (frontend bug workaround)
location = /logo.png {
    return 302 /shelfmark/logo.png;
}

# Proxy root /api/ to /shelfmark/api/ (frontend generates root paths)
location /api/ {
    proxy_pass http://shelfmark:8084/shelfmark/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}

# Proxy root /socket.io/ to backend (frontend connects to root)
location /socket.io/ {
    proxy_pass http://shelfmark:8084/socket.io/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}

# Rewrite /shelfmark/socket.io/ to /socket.io/ on backend
# (Socket.IO endpoint is always at /socket.io/ regardless of URL_BASE)
location ^~ /shelfmark/socket.io/ {
    proxy_pass http://shelfmark:8084/socket.io/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}

# Main shelfmark location
location /shelfmark/ {
    proxy_pass http://shelfmark:8084/shelfmark/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}
```

---

### With Authentication Proxy (Authelia, Authentik, etc.)

Shelfmark supports Proxy Authentication. When enabled, Shelfmark trusts the authenticated user from headers set by your auth proxy.

#### Shelfmark Settings

Configure in Settings → Security:

| Setting | Value |
|---------|-------|
| Authentication Method | Proxy Authentication |
| Proxy Auth User Header | `Remote-User` |
| Proxy Auth Logout URL | `https://auth.example.com/logout` |
| Proxy Auth Admin Group Header | `Remote-Groups` |
| Proxy Auth Admin Group Name | `admins` (or your admin group) |

#### Nginx Configuration with Authelia

This example uses Authelia snippets. Adapt for your auth proxy.

**Authelia auth request snippet** (`/etc/nginx/snippets/authelia-authrequest.conf`):

```nginx
location /authelia {
    internal;
    proxy_pass http://authelia:9091/api/authz/auth-request;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header Host $host;
    proxy_set_header X-Original-URL $scheme://$http_host$request_uri;
    proxy_set_header X-Original-Method $request_method;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

**Authelia location snippet** (`/etc/nginx/snippets/authelia-location.conf`):

```nginx
auth_request /authelia;
auth_request_set $target_url $scheme://$http_host$request_uri;
auth_request_set $user $upstream_http_remote_user;
auth_request_set $groups $upstream_http_remote_groups;
auth_request_set $name $upstream_http_remote_name;
auth_request_set $email $upstream_http_remote_email;
proxy_set_header Remote-User $user;
proxy_set_header Remote-Groups $groups;
proxy_set_header Remote-Name $name;
proxy_set_header Remote-Email $email;
error_page 401 =302 https://auth.example.com/?rd=$target_url;
```

**Complete Nginx configuration with Authelia:**

```nginx
# Include Authelia auth endpoint in your server block
include /etc/nginx/snippets/authelia-authrequest.conf;

# Redirect /logo.png to subpath (frontend bug workaround)
location = /logo.png {
    return 302 /shelfmark/logo.png;
}

# Proxy root /api/ to /shelfmark/api/ (frontend generates root paths)
location /api/ {
    include /etc/nginx/snippets/authelia-location.conf;

    proxy_pass http://shelfmark:8084/shelfmark/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}

# Proxy root /socket.io/ to backend (frontend connects to root)
location /socket.io/ {
    include /etc/nginx/snippets/authelia-location.conf;

    proxy_pass http://shelfmark:8084/socket.io/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}

# Rewrite /shelfmark/socket.io/ to /socket.io/ on backend
location ^~ /shelfmark/socket.io/ {
    include /etc/nginx/snippets/authelia-location.conf;

    proxy_pass http://shelfmark:8084/socket.io/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}

# Main shelfmark location
location /shelfmark/ {
    include /etc/nginx/snippets/authelia-location.conf;

    proxy_pass http://shelfmark:8084/shelfmark/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 86400;
    proxy_send_timeout 86400;
    proxy_buffering off;
}
```

---

## Known issues with subpath deployments

The following issues require the workarounds above:

1. **Socket.IO connects to root**: The frontend Socket.IO client connects to `https://yourdomain.com/socket.io/` instead of `https://yourdomain.com/shelfmark/socket.io/`

2. **API calls use root path**: Cover image requests and some API calls go to `/api/` instead of `/shelfmark/api/`

3. **Logo uses root path**: The logo is requested from `/logo.png` instead of `/shelfmark/logo.png`

4. **Socket.IO backend path**: The Socket.IO endpoint on the backend is always at `/socket.io/`, not `/shelfmark/socket.io/`, regardless of the `URL_BASE` setting

## Health checks

Health checks work at `/shelfmark/api/health` when using a subpath configuration.

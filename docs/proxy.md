# Outbound Proxy

Set a proxy under **Settings -> Network -> Proxy Mode** (`PROXY_MODE`): HTTP/HTTPS or SOCKS5. In HTTP/HTTPS mode, an empty HTTPS proxy means the HTTP proxy is used for both. The variables are listed in the [Environment Variables](environment-variables.md) reference.

Shelfmark's web requests then go through the proxy, apart from the local addresses below. IRC and email are not web requests and don't use it. An external bypasser makes its own connections, so give it a proxy in its own settings if it needs one.

## What always goes direct

A host counts as local from its name or IP address alone, without a DNS lookup:

- `localhost`, and private, loopback and link-local IP addresses
- names without a dot, such as Docker service names (`prowlarr`, `qbittorrent`)
- names ending in `.local`, `.lan`, `.internal`, `.home`, `.docker` or `.localdomain`

## No Proxy

A service on your network that you reach by a public hostname, such as `abs.example.org`, doesn't look local. Add it to **No Proxy** (`NO_PROXY`), comma-separated. Exact names, `*.example.com` and `10.*` are accepted.

Notifications, qBittorrent, Transmission and [custom scripts](custom-scripts.md#proxy-settings) read the proxy from environment variables instead. There a host name also covers its subdomains (`abs.example.org` covers `www.abs.example.org`), and a pattern such as `10.*` has no effect.

For signing in through a proxy, see [OIDC](oidc.md#using-an-outbound-proxy).

## Environment variables

At startup, and after each settings save, Shelfmark sets `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` (and their lowercase forms) to match the settings.

- A proxy variable you give the container is left as you set it. With Proxy Mode set to None, Shelfmark's own requests to hosts that aren't local still use it. `curl` in a custom script only reads the lowercase `http_proxy` for `http://` addresses, so set that one if your scripts need it.
- A `NO_PROXY` you give the container locks the No Proxy field. With a proxy set, Shelfmark adds the local entries to your value.

## Tor and WireGuard

With `USING_TOR=true` or `USING_WIREGUARD=true`, the tunnel carries the traffic and the proxy settings are not used. `NO_PROXY` is set to `*`, including over a value you gave the container, so that nothing goes around the tunnel through a proxy.

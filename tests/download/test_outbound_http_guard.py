"""Every outbound connection goes through the network session, or is on the allowlist.

The proxy and TLS settings from Settings → Network are applied in one place,
``shelfmark.download.network.NetworkSession``. A plain ``requests`` call, a second HTTP
client or a raw socket elsewhere would skip them, which is how #1524, #1526, #1685 and
#1687 happened. This scan fails on any of those outside ``network.py`` unless the
allowlist below names it with a reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "shelfmark"
NETWORK_MODULE = Path("shelfmark/download/network.py")

_REQUESTS_CALLS = {"get", "post", "put", "patch", "delete", "head", "options", "request"}
_REQUESTS_SESSIONS = {"Session", "session"}
_HTTPX_CALLS = {"Client", "AsyncClient", *_REQUESTS_CALLS, "stream"}
_SOCKET_CALLS = {"socket", "create_connection"}
_CLIENT_LIBRARIES = {
    "aiohttp",
    "http.client",
    "httpx",
    "nodriver",
    "qbittorrentapi",
    "seleniumbase",
    "transmission_rpc",
    "urllib.request",
    "websockets",
}

# (file, what it uses) -> why it cannot go through NetworkSession.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("shelfmark/download/clients/qbittorrent.py", "qbittorrentapi"): (
        "qbittorrent-api keeps its own HTTP session inside the library"
    ),
    ("shelfmark/download/clients/settings.py", "qbittorrentapi"): (
        "the qBittorrent connection test uses the same library"
    ),
    ("shelfmark/download/clients/transmission.py", "transmission_rpc"): (
        "transmission-rpc keeps its own HTTP session inside the library"
    ),
    ("shelfmark/download/clients/settings.py", "transmission_rpc"): (
        "the Transmission connection test uses the same library"
    ),
    ("shelfmark/bypass/internal_bypasser.py", "seleniumbase"): (
        "Chromium takes its proxy from launch arguments built by _get_proxy_string(),"
        " which calls get_proxies()"
    ),
    ("shelfmark/release_sources/irc/client.py", "socket.socket"): (
        "IRC is a raw TCP connection, which an HTTP proxy does not carry"
    ),
    ("shelfmark/release_sources/irc/dcc.py", "socket.socket"): (
        "DCC transfers are raw TCP connections, which an HTTP proxy does not carry"
    ),
}


def _dotted(node: ast.expr) -> str:
    """``requests.get`` for an attribute chain on a name, otherwise an empty string."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


def _uses(tree: ast.AST) -> set[str]:
    """What a module uses to reach the network on its own, as short names."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name in _CLIENT_LIBRARIES)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.update(
                library
                for library in _CLIENT_LIBRARIES
                if node.module == library or node.module.startswith(f"{library}.")
            )
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            base, _, attr = name.rpartition(".")
            if base == "requests" and attr in _REQUESTS_CALLS | _REQUESTS_SESSIONS:
                found.add(name)
            elif base == "httpx" and attr in _HTTPX_CALLS:
                found.add(name)
            elif base == "socket" and attr in _SOCKET_CALLS:
                found.add("socket.socket")
            elif name in {"urllib.request.urlopen", "urllib.request.build_opener"}:
                found.add(name)
    return found


def scan(package: Path, root: Path) -> set[tuple[str, str]]:
    """Every (file, use) under ``package`` that bypasses the network session."""
    hits: set[tuple[str, str]] = set()
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(root)
        if relative == NETWORK_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        hits.update((relative.as_posix(), use) for use in _uses(tree))
    return hits


def test_nothing_outside_the_network_module_skips_the_session():
    """A new outbound call has to go through NetworkSession or be allowlisted with a reason."""
    unexpected = sorted(scan(PACKAGE, ROOT) - set(ALLOWLIST))

    assert unexpected == [], (
        "Use shelfmark.download.network.get_session() or NetworkSession instead, "
        f"or add the entry to ALLOWLIST with the reason it cannot: {unexpected}"
    )


def test_every_allowlist_entry_is_still_needed():
    """An entry whose use has gone is removed, so the allowlist cannot hide a new one."""
    stale = sorted(set(ALLOWLIST) - scan(PACKAGE, ROOT))

    assert stale == []


def test_the_scan_catches_each_kind_of_bypass(tmp_path):
    """Each pattern the guard exists for is reported when planted in a module."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "planted.py").write_text(
        "import aiohttp\n"
        "import httpx\n"
        "import requests\n"
        "import socket\n"
        "from transmission_rpc import Client\n"
        "requests.get('https://example.com')\n"
        "requests.post('https://example.com')\n"
        "requests.Session()\n"
        "httpx.Client()\n"
        "socket.socket()\n",
        encoding="utf-8",
    )
    (package / "fine.py").write_text(
        "import requests\n"
        "from shelfmark.download.network import get_session\n"
        "get_session().get('https://example.com')\n"
        "requests.exceptions.RequestException\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == {
        ("shelfmark/planted.py", "aiohttp"),
        ("shelfmark/planted.py", "httpx"),
        ("shelfmark/planted.py", "transmission_rpc"),
        ("shelfmark/planted.py", "requests.get"),
        ("shelfmark/planted.py", "requests.post"),
        ("shelfmark/planted.py", "requests.Session"),
        ("shelfmark/planted.py", "httpx.Client"),
        ("shelfmark/planted.py", "socket.socket"),
    }

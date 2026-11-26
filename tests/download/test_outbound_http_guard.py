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

# Arguments NetworkSession supplies itself. Passing one by hand makes it explicit, which
# stops the session choosing it again for each redirect hop.
_SESSION_ARGUMENTS = {"verify": "get_ssl_verify", "proxies": "get_proxies"}

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


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

# One (is a class body, import aliases, other bound names) triple per enclosing scope,
# outermost first.
Aliases = list[tuple[bool, dict[str, str], set[str]]]


def _own_aliases(scope: ast.AST) -> dict[str, str]:
    """Names ``scope`` itself binds to something else's dotted path.

    ``import requests as r`` gives ``r -> requests``; ``from requests import get as g``
    gives ``g -> requests.get``; ``from urllib import request`` gives
    ``request -> urllib.request``. A plain ``import requests`` binds the name it already is.
    An import inside a function or class nested in ``scope`` belongs to that scope, not this.
    Statements are read in source order, so a later import of the same name replaces an
    earlier one, as it does when Python runs them.
    """
    aliases: dict[str, str] = {}
    pending = list(reversed(list(ast.iter_child_nodes(scope))))
    while pending:
        node = pending.pop()
        if isinstance(node, _SCOPES):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        pending.extend(reversed(list(ast.iter_child_nodes(node))))
    return aliases


def _own_bindings(scope: ast.AST) -> set[str]:
    """Names ``scope`` binds other than by import: parameters, assignments, nested defs.

    A name bound here hides an import alias of the same name from an outer scope, so
    ``def fetch(r): r.get(url)`` is not a call on a module-level ``import requests as r``.
    This is a best effort at Python's scoping, enough for the shapes the code uses.
    """
    bound: set[str] = set()
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        args = scope.args
        for argument in (*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg):
            if argument is not None:
                bound.add(argument.arg)
    pending = list(ast.iter_child_nodes(scope))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        if isinstance(node, _SCOPES):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        pending.extend(ast.iter_child_nodes(node))
    return bound


def _scope_entry(node: ast.AST) -> tuple[bool, dict[str, str], set[str]]:
    """The ``Aliases`` entry for the scope ``node`` opens."""
    return (isinstance(node, ast.ClassDef), _own_aliases(node), _own_bindings(node))


def _resolve(node: ast.expr, scopes: Aliases) -> str:
    """``_dotted`` with the first name replaced by what the nearest scope imported it as."""
    name = _dotted(node)
    head, dot, rest = name.partition(".")
    for _, aliases, bound in reversed(scopes):
        if head in aliases:
            return f"{aliases[head]}{dot}{rest}"
        if head in bound:
            return name
    return name


def _note_hand_passed(found: set[str], argument: str, value: ast.expr, scopes: Aliases) -> None:
    """Record ``verify=get_ssl_verify(...)`` or ``proxies=get_proxies(...)`` passed by hand."""
    helper = _SESSION_ARGUMENTS.get(argument)
    if (
        helper
        and isinstance(value, ast.Call)
        and _resolve(value.func, scopes).rpartition(".")[2] == helper
    ):
        found.add(f"{argument}={helper}()")


def _uses(tree: ast.AST) -> set[str]:
    """What a module uses to reach the network on its own, as short names."""
    found: set[str] = set()
    _walk(tree, [(False, _own_aliases(tree), _own_bindings(tree))], found)
    return found


def _walk(node: ast.AST, scopes: Aliases, found: set[str]) -> None:
    """Record what ``node`` and everything under it uses, resolving names scope by scope.

    A class body's names are not visible inside its methods, so a function drops them.
    Defaults, decorators, annotations and base classes run where the function or class is
    defined, so only the body sees the scope it opens.
    """
    _note(node, scopes, found)
    if not isinstance(node, _SCOPES):
        for child in ast.iter_child_nodes(node):
            _walk(child, scopes, found)
        return
    if isinstance(node, ast.ClassDef):
        inner = [*scopes, _scope_entry(node)]
    else:
        inner = [*(s for s in scopes if not s[0]), _scope_entry(node)]
    body = node.body if isinstance(node.body, list) else [node.body]
    for child in ast.iter_child_nodes(node):
        _walk(child, inner if any(child is statement for statement in body) else scopes, found)


def _note(node: ast.AST, scopes: Aliases, found: set[str]) -> None:
    """Record what this one node uses, if anything."""
    if isinstance(node, ast.Import):
        found.update(alias.name for alias in node.names if alias.name in _CLIENT_LIBRARIES)
    elif isinstance(node, ast.ImportFrom) and node.module:
        found.update(
            library
            for library in _CLIENT_LIBRARIES
            if node.module == library or node.module.startswith(f"{library}.")
        )
    elif isinstance(node, ast.Dict):
        # request kwargs built as a dict literal: {"verify": get_ssl_verify(url), ...}
        for key, value in zip(node.keys, node.values, strict=True):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                _note_hand_passed(found, key.value, value, scopes)
    elif isinstance(node, ast.Call):
        for keyword in node.keywords:
            if keyword.arg:
                _note_hand_passed(found, keyword.arg, keyword.value, scopes)
        name = _resolve(node.func, scopes)
        base, _, attr = name.rpartition(".")
        if (base == "requests.api" and attr in _REQUESTS_CALLS) or (
            base == "requests.sessions" and attr in _REQUESTS_SESSIONS
        ):
            found.add(name)
        elif base == "requests" and attr in _REQUESTS_CALLS | _REQUESTS_SESSIONS:
            found.add(name)
        elif base == "httpx" and attr in _HTTPX_CALLS:
            found.add(name)
        elif base == "socket" and attr in _SOCKET_CALLS:
            found.add("socket.socket")
        elif name in {"urllib.request.urlopen", "urllib.request.build_opener"}:
            found.add(name)


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
        "socket.socket()\n"
        "get_session().get('https://example.com', verify=get_ssl_verify('x'))\n"
        "get_session().get('https://example.com', proxies=network.get_proxies('x'))\n"
        "kwargs = {'verify': get_ssl_verify('x')}\n",
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
        ("shelfmark/planted.py", "verify=get_ssl_verify()"),
        ("shelfmark/planted.py", "proxies=get_proxies()"),
    }


def test_the_scan_resolves_import_aliases(tmp_path):
    """``from requests import get`` and ``import requests as r`` are the same bypass."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "aliased.py").write_text(
        "import requests as r\n"
        "import socket as s\n"
        "import urllib.request as ur\n"
        "from requests import get, post as send, Session as S\n"
        "from socket import create_connection\n"
        "from urllib import request\n"
        "from shelfmark.download.network import get_ssl_verify as ssl_verify\n"
        "get('https://example.com')\n"
        "send('https://example.com')\n"
        "S()\n"
        "r.put('https://example.com')\n"
        "s.socket()\n"
        "create_connection(('example.com', 80))\n"
        "ur.urlopen('https://example.com')\n"
        "request.build_opener()\n"
        "r.Session().get('https://example.com', verify=ssl_verify('x'))\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == {
        ("shelfmark/aliased.py", "requests.get"),
        ("shelfmark/aliased.py", "requests.post"),
        ("shelfmark/aliased.py", "requests.Session"),
        ("shelfmark/aliased.py", "requests.put"),
        ("shelfmark/aliased.py", "socket.socket"),
        ("shelfmark/aliased.py", "urllib.request"),
        ("shelfmark/aliased.py", "urllib.request.urlopen"),
        ("shelfmark/aliased.py", "urllib.request.build_opener"),
        ("shelfmark/aliased.py", "verify=get_ssl_verify()"),
    }


def test_an_alias_imported_in_a_function_stays_in_that_function(tmp_path):
    """A function-local or class-level import is a hit where it is used, and only there."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "scoped.py").write_text(
        "import requests\n"
        "\n"
        "def fetch():\n"
        "    import requests as r\n"
        "    return r.get('https://example.com')\n"
        "\n"
        "def other(r):\n"
        "    return r.get('https://example.com')\n"
        "\n"
        "def shadowed():\n"
        "    from somewhere import get\n"
        "    return get()\n"
        "\n"
        "def uses_module_level():\n"
        "    return requests.post('https://example.com')\n"
        "\n"
        "class Holder:\n"
        "    from requests import put\n"
        "    put('https://example.com')\n"
        "    def run(self):\n"
        "        return put('https://example.com')\n",
        encoding="utf-8",
    )

    # The class-body call is a hit; the method's ``put`` cannot see the class namespace.
    assert scan(package, tmp_path) == {
        ("shelfmark/scoped.py", "requests.get"),
        ("shelfmark/scoped.py", "requests.post"),
        ("shelfmark/scoped.py", "requests.put"),
    }


def test_a_class_level_import_is_not_visible_inside_its_methods(tmp_path):
    """Python resolves a name in a method from the function and the module, never the class."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "klass.py").write_text(
        "class Holder:\n"
        "    from requests import put\n"
        "    def run(self, put):\n"
        "        return put('https://example.com')\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == set()


def test_an_alias_does_not_reach_a_sibling_function(tmp_path):
    """The aliased name is only a hit where it was imported, so ``r`` elsewhere is not one."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "sibling.py").write_text(
        "def fetch():\n"
        "    import requests as r\n"
        "    return 1\n"
        "\n"
        "def other(r):\n"
        "    return r.get('https://example.com')\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == set()


def test_the_scan_catches_requests_submodule_calls(tmp_path):
    """``requests.get`` is ``requests.api.get``; ``requests.Session`` is in ``requests.sessions``."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "submodules.py").write_text(
        "import requests.api\n"
        "import requests.sessions\n"
        "import requests.api as api\n"
        "from requests.api import post\n"
        "from requests.sessions import Session\n"
        "requests.api.get('https://example.com')\n"
        "requests.sessions.Session()\n"
        "requests.sessions.session()\n"
        "api.put('https://example.com')\n"
        "post('https://example.com')\n"
        "Session()\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == {
        ("shelfmark/submodules.py", "requests.api.get"),
        ("shelfmark/submodules.py", "requests.api.put"),
        ("shelfmark/submodules.py", "requests.api.post"),
        ("shelfmark/submodules.py", "requests.sessions.Session"),
        ("shelfmark/submodules.py", "requests.sessions.session"),
    }


def test_imports_are_read_in_source_order(tmp_path):
    """The last import of a name is the one a later call uses."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "ordered.py").write_text(
        "from shelfmark.download.network import get_session as client\n"
        "import requests as client\n"
        "client.get('https://example.com')\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == {("shelfmark/ordered.py", "requests.get")}


def test_a_parameter_hides_a_module_alias_of_the_same_name(tmp_path):
    """``def fetch(r)`` is not a call on ``import requests as r``, but the module's own use is."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "shadowed.py").write_text(
        "import requests as r\n"
        "\n"
        "def fetch(r):\n"
        "    return r.get('https://example.com')\n"
        "\n"
        "def direct():\n"
        "    return r.post('https://example.com')\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == {("shelfmark/shadowed.py", "requests.post")}


def test_a_default_runs_in_the_scope_that_defines_the_function(tmp_path):
    """An import in the body does not change what the default expression calls."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "defaults.py").write_text(
        "import requests as r\n"
        "\n"
        "def fetch(response=r.get('https://example.com')):\n"
        "    from shelfmark.download.network import get_session as r\n"
        "    return r.get('https://example.com')\n",
        encoding="utf-8",
    )

    assert scan(package, tmp_path) == {("shelfmark/defaults.py", "requests.get")}

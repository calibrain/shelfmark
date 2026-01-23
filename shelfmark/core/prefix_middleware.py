"""WSGI middleware for hosting Shelfmark under a URL prefix."""

from __future__ import annotations

from typing import Iterable, Optional


class PrefixMiddleware:
    """Strip a configured URL prefix from PATH_INFO before routing."""

    def __init__(self, app, prefix: str, bypass_paths: Optional[Iterable[str]] = None) -> None:
        self.app = app
        self.prefix = prefix.rstrip("/")
        self.bypass_paths = set(bypass_paths or [])

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or ""

        if path in self.bypass_paths:
            return self.app(environ, start_response)

        if not self.prefix:
            return self.app(environ, start_response)

        if path == self.prefix or path.startswith(self.prefix + "/"):
            environ["SCRIPT_NAME"] = self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):] or "/"
            return self.app(environ, start_response)

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]

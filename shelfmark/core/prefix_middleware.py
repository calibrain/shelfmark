"""WSGI middleware for hosting Shelfmark under a URL prefix."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


class PrefixMiddleware:
    """Strip a configured URL prefix from PATH_INFO before routing."""

    def __init__(
        self,
        app: Callable[[dict[str, object], Callable[..., object]], object],
        prefix: str,
        bypass_paths: Iterable[str] | None = None,
    ) -> None:
        """Initialize the middleware with a prefix and optional bypass paths."""
        self.app = app
        self.prefix = prefix.rstrip("/")
        self.bypass_paths = set(bypass_paths or [])

    def __call__(self, environ: dict[str, object], start_response: Callable[..., object]) -> object:
        """Rewrite prefixed requests before handing them to the wrapped app."""
        path = environ.get("PATH_INFO", "") or ""

        if path in self.bypass_paths:
            return self.app(environ, start_response)

        if not self.prefix:
            return self.app(environ, start_response)

        if path == self.prefix or path.startswith(self.prefix + "/"):
            environ["SCRIPT_NAME"] = self.prefix
            environ["PATH_INFO"] = path[len(self.prefix) :] or "/"
            return self.app(environ, start_response)

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not Found"]

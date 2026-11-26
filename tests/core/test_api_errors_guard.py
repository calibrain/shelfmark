"""No route puts an exception into a JSON response itself (#1688).

The text of an exception can quote a path, a URL with credentials or a key. A response is
built from it only in ``shelfmark.core.api_errors``, which masks it. This scan fails on a
``jsonify`` call inside an ``except ... as name`` block that uses ``name``, however it is
used (``str(name)``, an f-string, the object itself).
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "shelfmark"
ALLOWED = {Path("shelfmark/core/api_errors.py")}


def _uses_name(node: ast.AST, name: str) -> bool:
    """Whether the variable ``name`` appears anywhere inside ``node``."""
    return any(isinstance(inner, ast.Name) and inner.id == name for inner in ast.walk(node))


def _is_jsonify(call: ast.Call) -> bool:
    """Whether a call is ``jsonify(...)`` or ``flask.jsonify(...)``."""
    func = call.func
    return (isinstance(func, ast.Name) and func.id == "jsonify") or (
        isinstance(func, ast.Attribute) and func.attr == "jsonify"
    )


def scan(package: Path, root: Path) -> list[tuple[str, int]]:
    """Return ``(file, line)`` for each response built from an exception variable."""
    found: list[tuple[str, int]] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(root)
        if relative in ALLOWED:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for handler in (n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.name):
            for call in (
                n for n in ast.walk(handler) if isinstance(n, ast.Call) and _is_jsonify(n)
            ):
                if _uses_name(call, handler.name or ""):
                    found.append((str(relative), call.lineno))
    return found


def test_no_route_builds_a_response_from_an_exception():
    """Use exception_response or error_response from shelfmark.core.api_errors instead."""
    assert scan(PACKAGE, ROOT) == []


def test_the_scan_catches_each_way_of_using_the_exception(tmp_path):
    """Each shape the guard exists for is reported when planted in a module."""
    package = tmp_path / "shelfmark"
    package.mkdir()
    (package / "planted.py").write_text(
        "from flask import jsonify\n"
        "import flask\n"
        "def a():\n"
        "    try:\n"
        "        pass\n"
        "    except ValueError as e:\n"
        "        return jsonify({'error': str(e)}), 400\n"
        "def b():\n"
        "    try:\n"
        "        pass\n"
        "    except OSError as exc:\n"
        "        return jsonify({'error': f'failed: {exc}'}), 500\n"
        "def c():\n"
        "    try:\n"
        "        pass\n"
        "    except OSError as err:\n"
        "        return flask.jsonify(error=err.args), 500\n"
        "def fine():\n"
        "    try:\n"
        "        pass\n"
        "    except OSError as err:\n"
        "        return jsonify({'error': 'Could not read the file'}), 500\n"
    )

    assert scan(package, tmp_path) == [
        ("shelfmark/planted.py", 7),
        ("shelfmark/planted.py", 12),
        ("shelfmark/planted.py", 17),
    ]

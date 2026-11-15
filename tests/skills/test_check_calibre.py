"""A book title reaches calibredb as data: one argv item and one query term."""

import importlib.util
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "skills" / "shelfmark" / "download_books.py"


def _load_module():
    # The script imports playwright at module level; checkout CI installs it,
    # a bare test run may not.
    if "playwright.sync_api" not in sys.modules:
        try:
            import playwright.sync_api  # noqa: F401
        except ModuleNotFoundError:
            playwright = types.ModuleType("playwright")
            sync_api = types.ModuleType("playwright.sync_api")
            sync_api.Error = type("Error", (Exception,), {})
            sync_api.sync_playwright = lambda: None
            playwright.sync_api = sync_api
            sys.modules["playwright"] = playwright
            sys.modules["playwright.sync_api"] = sync_api

    spec = importlib.util.spec_from_file_location("shelfmark_download_books", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "title",
    [
        "Mistborn",
        'Dune"; touch /tmp/pwned; echo "',
        "Neuromancer$(id)",
        "Snow Crash`whoami`",
        "The \"Ball\" and The \"Chain\"",
        r"C:\Books\Dune",
    ],
)
def test_title_is_one_argument_and_one_query_term(title):
    module = _load_module()
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="id title authors\n", stderr="")

    with patch.object(subprocess, "run", return_value=completed) as run:
        module.check_calibre(title)

    args, kwargs = run.call_args
    command = args[0]
    assert isinstance(command, list), "the command must be an argv list"
    assert command[:3] == ["calibredb", "list", "--search"]
    assert not kwargs.get("shell"), "a shell must not re-parse the title"

    term = command[3]
    assert term.startswith('title:"') and term.endswith('"'), term
    inner = term[len('title:"'):-1]
    # Nothing inside the term may end it early: every quote and every
    # backslash is escaped, and unescaping gives the title back.
    assert _unescape(inner) == title
    assert _every_quote_escaped(inner), inner


def _every_quote_escaped(inner: str) -> bool:
    i = 0
    while i < len(inner):
        if inner[i] == "\\":
            i += 2
            continue
        if inner[i] == '"':
            return False
        i += 1
    return True


def _unescape(inner: str) -> str:
    out = []
    i = 0
    while i < len(inner):
        char = inner[i]
        if char == "\\":
            i += 1
            out.append(inner[i])
        else:
            out.append(char)
        i += 1
    return "".join(out)


def test_plain_title_is_unchanged_by_escaping():
    module = _load_module()
    assert module.calibre_title_search("Mistborn") == 'title:"Mistborn"'

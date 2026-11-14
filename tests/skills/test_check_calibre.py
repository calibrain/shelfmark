"""The calibre check must pass a title as data, never as shell syntax."""

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
    ],
)
def test_title_is_an_argument_not_shell_syntax(title):
    module = _load_module()
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="id title authors\n", stderr="")

    with patch.object(subprocess, "run", return_value=completed) as run:
        module.check_calibre(title)

    args, kwargs = run.call_args
    command = args[0]
    assert isinstance(command, list), "the command must be an argv list"
    assert command[:3] == ["calibredb", "list", "--search"]
    assert command[3] == f'title:="{title}"'
    assert not kwargs.get("shell"), "a shell must not re-parse the title"

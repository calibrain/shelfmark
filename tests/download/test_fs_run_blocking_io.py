from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


def test_run_blocking_io_handles_gevent_patched_subprocess_run() -> None:
    pytest.importorskip("gevent")

    repo_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import os
        import subprocess
        import sys
        import tempfile

        from gevent import monkey

        monkey.patch_all()
        os.environ.setdefault("LOG_ROOT", tempfile.mkdtemp(prefix="shelfmark-log-root-"))

        from shelfmark.download.fs import run_blocking_io

        result = run_blocking_io(
            subprocess.run,
            [sys.executable, "-c", "print('ok')"],
            check=True,
            capture_output=True,
            text=True,
        )
        assert result.stdout.strip() == "ok"
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, (
        f"Subprocess failed with exit code {completed.returncode}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

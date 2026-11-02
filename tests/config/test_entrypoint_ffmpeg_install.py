"""The Debug mode ffmpeg install: what it runs, what it logs, and what it can't block."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ENTRYPOINT_PATH = Path(__file__).resolve().parents[2] / "entrypoint.sh"
BASH_PATH = shutil.which("bash") or "/bin/bash"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _install_ffmpeg_source() -> str:
    text = ENTRYPOINT_PATH.read_text()
    start = text.index("install_ffmpeg() {")
    return text[start : text.index("\n}\n", start) + 3]


def _run(
    tmp_path: Path, *, apt_body: str, ffmpeg_works: bool = True
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "apt-calls.txt"
    _stub(
        bin_dir,
        "apt-get",
        f'echo "frontend=$DEBIAN_FRONTEND $*" >> "{calls}"\n{apt_body}',
    )
    _stub(bin_dir, "dpkg", "exit 0")
    _stub(bin_dir, "ffmpeg", "exit 0" if ffmpeg_works else "exit 1")

    env = os.environ.copy()
    env.update({"PATH": f"{bin_dir}:{env.get('PATH', '')}", "FFMPEG_INSTALL_TIMEOUT": "1"})
    result = subprocess.run(
        [BASH_PATH, "-c", f"{_install_ffmpeg_source()}\ninstall_ffmpeg"],
        capture_output=True,
        env=env,
        text=True,
        check=False,
        timeout=30,
    )
    return result, calls.read_text().splitlines() if calls.exists() else []


def test_install_downloads_first_then_installs_without_the_network(tmp_path):
    result, calls = _run(tmp_path, apt_body="exit 0")

    assert "ffmpeg installed" in result.stdout
    assert [call.split()[1] for call in calls] == ["update", "install", "install"]
    assert "--download-only" in calls[1]
    assert "--no-download" in calls[2]
    assert all(call.startswith("frontend=noninteractive ") for call in calls)


def test_install_gives_up_on_a_mirror_that_never_answers(tmp_path):
    result, calls = _run(tmp_path, apt_body="exec sleep 60")

    assert "Timed out installing ffmpeg after 1s" in result.stdout
    assert "ffmpeg installed" not in result.stdout
    # Killed during the download, so dpkg's install step never ran.
    assert not any("--no-download" in call for call in calls)


def test_install_reports_a_failure(tmp_path):
    result, calls = _run(tmp_path, apt_body="exit 100")

    assert "Could not install ffmpeg (exit 100)" in result.stdout
    assert not any("--no-download" in call for call in calls)


def test_install_does_not_claim_success_when_ffmpeg_does_not_run(tmp_path):
    result, _ = _run(tmp_path, apt_body="exit 0", ffmpeg_works=False)

    assert "ffmpeg does not run" in result.stdout
    assert "ffmpeg installed" not in result.stdout


def test_install_starts_after_the_log_pipe_is_released():
    """stop_file_logging waits for the log pipe to close, so the job must not hold it."""
    text = ENTRYPOINT_PATH.read_text()

    assert text.rindex("stop_file_logging\n") < text.index('if [ "$INSTALL_FFMPEG" = "true" ]')
    assert text.index('if [ "$INSTALL_FFMPEG" = "true" ]') < text.rindex("exec_as_target_user env")

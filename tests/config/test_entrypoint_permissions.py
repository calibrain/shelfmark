from __future__ import annotations

import contextlib
import fcntl
import os
import shutil
import subprocess
from pathlib import Path

ENTRYPOINT_PATH = Path(__file__).resolve().parents[2] / "entrypoint.sh"
ENTRYPOINT_LOCK_PATH = Path("/tmp/shelfmark_entrypoint_test.lock")
BASH_PATH = shutil.which("bash") or "/bin/bash"


@contextlib.contextmanager
def _entrypoint_lock():
    ENTRYPOINT_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ENTRYPOINT_LOCK_PATH.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _build_stub_bin(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    runtime_home_file = tmp_path / "gunicorn-home.txt"
    runtime_args_file = tmp_path / "gunicorn-args.txt"

    _write_executable(
        bin_dir / "getent",
        """#!/bin/sh
if [ "$1" = "passwd" ] && [ "$2" = "$ENTRYPOINT_STUB_UID" ]; then
  printf 'shelfmark:x:%s:%s:Shelfmark:%s:/bin/sh\\n' "$ENTRYPOINT_STUB_UID" "$ENTRYPOINT_STUB_GID" "$ENTRYPOINT_STUB_HOME"
  exit 0
fi
if [ "$1" = "group" ] && [ "$2" = "$ENTRYPOINT_STUB_GID" ]; then
  printf 'shelfmark:x:%s:\\n' "$ENTRYPOINT_STUB_GID"
  exit 0
fi
exit 2
""",
    )
    _write_executable(
        bin_dir / "gunicorn",
        """#!/bin/sh
printf '%s' "$HOME" > "$ENTRYPOINT_GUNICORN_HOME_FILE"
printf '%s' "$*" > "$ENTRYPOINT_GUNICORN_ARGS_FILE"
exit 0
""",
    )

    return bin_dir, runtime_home_file, runtime_args_file


def _run_entrypoint(
    tmp_path: Path,
    *,
    extra_env: dict[str, str] | None = None,
    stub_home: Path | str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    runtime_home = tmp_path / "runtime-home"
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    tmp_dir = tmp_path / "tmp"

    if stub_home is None:
        stub_home = runtime_home

    bin_dir, runtime_home_file, runtime_args_file = _build_stub_bin(tmp_path)

    env = os.environ.copy()
    env.update(
        {
            "BUILD_VERSION": "test-build",
            "CONFIG_DIR": str(config_dir),
            "DEBUG": "false",
            "ENABLE_LOGGING": "false",
            "ENTRYPOINT_GUNICORN_ARGS_FILE": str(runtime_args_file),
            "ENTRYPOINT_GUNICORN_HOME_FILE": str(runtime_home_file),
            "ENTRYPOINT_STUB_GID": str(os.getgid()),
            "ENTRYPOINT_STUB_HOME": str(stub_home),
            "ENTRYPOINT_STUB_UID": str(os.getuid()),
            "FLASK_PORT": "8084",
            "LOG_LEVEL": "info",
            "LOG_ROOT": str(tmp_path / "logs"),
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "RELEASE_VERSION": "test-release",
            "TMP_DIR": str(tmp_dir),
            "TZ": "",
            "USING_EXTERNAL_BYPASSER": "true",
        }
    )
    if extra_env:
        env.update(extra_env)

    with _entrypoint_lock():
        result = subprocess.run(
            [BASH_PATH, str(ENTRYPOINT_PATH)],
            capture_output=True,
            cwd=ENTRYPOINT_PATH.parent,
            env=env,
            text=True,
            check=False,
        )

    return result, runtime_home_file, runtime_args_file, runtime_home


def test_entrypoint_rejects_tor_in_non_root_mode(tmp_path):
    result, _, _, _ = _run_entrypoint(tmp_path, extra_env={"USING_TOR": "true"})

    assert result.returncode == 1
    assert "USING_TOR=true requires the container to start as root." in result.stderr
    assert (
        "Non-root mode skips the privileged filesystem and network setup Tor depends on."
        in result.stderr
    )


def test_entrypoint_non_root_mode_runs_with_stub_gunicorn(tmp_path):
    result, runtime_home_file, runtime_args_file, runtime_home = _run_entrypoint(tmp_path)

    assert result.returncode == 0
    assert "Startup mode: non-root" in result.stdout
    assert f"Runtime identity: shelfmark ({os.getuid()}:{os.getgid()})" in result.stdout
    assert runtime_home.exists()
    assert runtime_home_file.read_text() == str(runtime_home)
    assert "shelfmark.main:app" in runtime_args_file.read_text()


def test_entrypoint_avoids_app_as_home(tmp_path):
    result, runtime_home_file, runtime_args_file, _ = _run_entrypoint(
        tmp_path,
        stub_home="/app",
    )

    fallback_home = tmp_path / "tmp" / "home"
    assert result.returncode == 0
    assert fallback_home.exists()
    assert runtime_home_file.read_text() == str(fallback_home)
    assert "shelfmark.main:app" in runtime_args_file.read_text()


def test_entrypoint_non_root_mode_requires_writable_config_dir(tmp_path):
    readonly_config_dir = tmp_path / "readonly-config"
    readonly_config_dir.mkdir()
    readonly_config_dir.chmod(0o555)

    try:
        result, _, _, _ = _run_entrypoint(
            tmp_path,
            extra_env={"CONFIG_DIR": str(readonly_config_dir)},
        )
    finally:
        readonly_config_dir.chmod(0o755)

    assert result.returncode == 1
    assert (
        f"Config directory is not writable in non-root mode: {readonly_config_dir}" in result.stdout
    )
    assert "Prepare ownership outside the container" in result.stdout

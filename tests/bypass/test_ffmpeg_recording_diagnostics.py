"""A recording that never happened must say why.

Issue #1276: the debug bundle's recording/ directory was empty, and the only trace was
three "FFmpeg already stopped" debug lines - one per bypass, each logged 20-56s after
the recorder was started, meaning ffmpeg had exited almost immediately every time. It ran
with `-loglevel 0` and no stderr capture, so nothing anywhere recorded the reason. The
screen recording is the single most useful artifact for diagnosing a bypass failure.
"""

import subprocess

import pytest

import shelfmark.bypass.internal_bypasser as ib


@pytest.fixture(autouse=True)
def _clean_display():
    before = dict(ib.DISPLAY)
    ib.DISPLAY["ffmpeg"] = None
    ib.DISPLAY["ffmpeg_output"] = None
    ib.DISPLAY["ffmpeg_error_log"] = None
    yield
    ib.DISPLAY.update(before)


@pytest.fixture(autouse=True)
def _ffmpeg_on_path(monkeypatch):
    # The recorder checks for the binary first; these tests stub the process itself.
    monkeypatch.setattr(ib.shutil, "which", lambda _name: "/usr/bin/ffmpeg")


class _Proc:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_ffmpeg_errors_are_captured_to_a_file_beside_the_recording(monkeypatch, tmp_path):
    monkeypatch.setattr(ib, "RECORDING_DIR", tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["stderr"] = kwargs.get("stderr")
        return _Proc(None)

    monkeypatch.setattr(ib.subprocess, "Popen", fake_popen)

    ib._start_ffmpeg_recording(display=":99")

    cmd = captured["cmd"]
    # Errors must not be thrown away any more.
    assert "-loglevel" in cmd
    assert cmd[cmd.index("-loglevel") + 1] == "error"
    # stderr goes to a real file, not a pipe nothing would drain.
    assert captured["stderr"] is not None
    assert captured["stderr"] is not subprocess.PIPE

    error_log = ib.DISPLAY["ffmpeg_error_log"]
    assert error_log is not None
    assert error_log.parent == tmp_path
    # It sits beside the mp4, so it travels in the debug bundle.
    assert error_log.name.startswith("screen_recording_")


def test_capture_is_not_pinned_to_the_fingerprint_size(monkeypatch, tmp_path):
    """Issue #1364: every recording died on "Capture area ... outside the screen size".

    The capture size was the fingerprint size plus margin, but the Xvfb is not built at
    that size (SeleniumBase falls back to a fixed 1440x1880 screen when xauth is missing),
    so any fingerprint wider than 1340px asked for more than the screen had. Left unset,
    x11grab records the whole screen, whatever it turned out to be.
    """
    monkeypatch.setattr(ib, "RECORDING_DIR", tmp_path)
    captured: dict[str, object] = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _Proc(None)

    monkeypatch.setattr(ib.subprocess, "Popen", fake_popen)

    ib._start_ffmpeg_recording(display=":99")

    cmd = captured["cmd"]
    assert "-video_size" not in cmd
    assert cmd[cmd.index("-f") + 1] == "x11grab"
    assert cmd[cmd.index("-i") + 1] == ":99"


def test_an_early_exit_is_reported_with_ffmpegs_own_reason(monkeypatch, tmp_path, caplog):
    reason = "[x11grab @ 0x1] Cannot open display :99, error 1."
    error_log = tmp_path / "screen_recording_x.ffmpeg.log"
    error_log.write_text(reason, encoding="utf-8")

    ib.DISPLAY["ffmpeg"] = _Proc(1)
    ib.DISPLAY["ffmpeg_output"] = tmp_path / "screen_recording_x.mp4"
    ib.DISPLAY["ffmpeg_error_log"] = error_log

    messages: list[str] = []

    class _Capture:
        def emit(self, record):
            messages.append(record.getMessage())

    import logging

    handler = logging.Handler()
    handler.emit = _Capture().emit  # type: ignore[method-assign]
    ib.logger.addHandler(handler)
    previous = ib.logger.level
    ib.logger.setLevel(logging.DEBUG)
    ib.logger._cache.clear()
    try:
        ib._stop_ffmpeg_recording()
    finally:
        ib.logger.removeHandler(handler)
        ib.logger.setLevel(previous)

    line = next((m for m in messages if "exited early" in m), None)
    assert line is not None, messages
    assert "code 1" in line
    assert "Cannot open display" in line
    assert ib.DISPLAY["ffmpeg"] is None


def test_summary_is_explicit_when_ffmpeg_logged_nothing(tmp_path):
    empty = tmp_path / "screen_recording_y.ffmpeg.log"
    empty.write_text("", encoding="utf-8")
    ib.DISPLAY["ffmpeg_error_log"] = empty

    assert "logged nothing" in ib._ffmpeg_error_summary()


def test_summary_survives_a_missing_log():
    ib.DISPLAY["ffmpeg_error_log"] = None

    assert "No FFmpeg error log" in ib._ffmpeg_error_summary()


def test_debug_mode_without_ffmpeg_skips_the_recording_and_says_so(monkeypatch, tmp_path):
    """Debug mode can be on with no ffmpeg in the image; that must not fail the bypass."""
    monkeypatch.setattr(ib, "RECORDING_DIR", tmp_path / "recording")
    monkeypatch.setattr(ib.shutil, "which", lambda _name: None)

    def popen_must_not_run(*_args, **_kwargs):
        raise AssertionError("ffmpeg should not be started when it is not installed")

    monkeypatch.setattr(ib.subprocess, "Popen", popen_must_not_run)

    warnings: list[str] = []
    monkeypatch.setattr(ib.logger, "warning", lambda msg, *_args: warnings.append(msg))

    ib._start_ffmpeg_recording(display=":99")

    assert ib.DISPLAY["ffmpeg"] is None
    assert any("ffmpeg is not installed" in message for message in warnings)
    assert not (tmp_path / "recording").exists()

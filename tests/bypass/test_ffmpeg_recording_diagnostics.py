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

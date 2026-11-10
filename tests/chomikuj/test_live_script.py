"""Offline tests for ``scripts/chomikuj_live_qa.py``.

The script is the owner-run live gate, so the live path is deliberately not
executed here. These tests load the script as a module (its ``__main__`` guard
must not fire), then cover the pieces that must stay correct without a network:
the live-proven constants, the redactor, the fully offline ``--self-test``, and
the missing-credentials exit code 2.

Every test here is offline. ``run_live`` is always patched with a sentinel so a
mistake that reaches the credentialed path fails loudly instead of making a
request.
"""

from __future__ import annotations

import importlib.util
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "chomikuj_live_qa.py"


@pytest.fixture(scope="module")
def live_script():
    """Load the script without running ``main``."""
    spec = importlib.util.spec_from_file_location("chomikuj_live_qa_under_test", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _no_live_run(_checked):
    raise AssertionError("the live path must not run in the offline tests")


def test_constants_carry_the_live_proven_contract(live_script):
    from shelfmark.release_sources.chomikuj.filename import IMPORTABLE_EBOOK_FORMATS

    assert live_script.DEFAULT_QUERY == "test"
    assert live_script.PROBE_HEADERS == {
        "User-Agent": "android/3.8.4 (python; python)",
        "Accept": "*/*",
    }
    assert "Range" not in live_script.PROBE_HEADERS
    assert tuple(live_script._formats_override()) == tuple(IMPORTABLE_EBOOK_FORMATS)
    assert "pdf" in live_script._formats_override()

    vectors = {
        (path, query, body, expected) for path, query, body, expected in live_script.TOKEN_VECTORS
    }
    assert (
        "api/v3/account/info",
        "",
        "",
        "22dbb80e516477793934fa0add1b8929",
    ) in vectors
    assert (
        "api/v3/files/search",
        "Query=test&PageNumber=1&MediaType=Documents&Extension=pdf",
        "",
        "83b420ec92a611e58b884b89e68ac530",
    ) in vectors
    assert (
        "api/v3/files/download",
        "FileId=123456",
        "",
        "bfdebdece4cef4a17ebe44808ab4cda4",
    ) in vectors
    assert (
        "api/v3/account/login",
        "",
        '{"AccountName":"alice","Password":"p@ss"}',
        "9f78082445ecf973cb41180065a0fc50",
    ) in vectors


def test_redact_removes_credentials_account_fields_urls_and_emails(live_script):
    secrets = [
        "owner@example.invalid",
        "hunter2-configured-password",
        "API-KEY-SECRET-0000",
        "424242",
        "Owner Display Name",
    ]
    text = " | ".join(
        [
            *secrets,
            "https://cdn.example.test/files/book.pdf?sig=SECRET",
            "someone@example.com",
        ]
    )

    redacted = live_script._redact(text, secrets)

    for leaked in [
        *secrets,
        "https://",
        "cdn.example.test",
        "someone@example.com",
    ]:
        assert leaked not in redacted, f"redaction leaked {leaked!r}: {redacted!r}"
    assert live_script.REDACTED in redacted


def test_redact_replaces_every_url_even_without_extra_secrets(live_script):
    text = "probe https://example.test/a?token=abc and http://other.test/b"

    redacted = live_script._redact(text)

    assert "https://" not in redacted
    assert "http://" not in redacted
    assert "example.test" not in redacted
    assert "other.test" not in redacted


def test_check_environment_reports_only_missing_names(live_script):
    unresolved = live_script.check_environment({})

    assert unresolved.ok is False
    assert unresolved.exit_code == 2
    assert "CHOMIKUJ_USERNAME" in unresolved.message
    assert "CHOMIKUJ_PASSWORD" in unresolved.message

    only_password = live_script.check_environment({"CHOMIKUJ_USERNAME": "someone"})
    assert only_password.ok is False
    assert only_password.exit_code == 2
    assert "CHOMIKUJ_PASSWORD" in only_password.message
    assert "someone" not in only_password.message


def test_check_environment_defaults_and_optional_numeric_ids(live_script):
    checked = live_script.check_environment(
        {"CHOMIKUJ_USERNAME": "alice", "CHOMIKUJ_PASSWORD": "secret"}
    )
    assert checked.ok is True
    assert checked.query == "test"
    assert checked.test_file_id == ""
    assert checked.paid_file_id == ""

    custom = live_script.check_environment(
        {
            "CHOMIKUJ_USERNAME": "alice",
            "CHOMIKUJ_PASSWORD": "secret",
            "CHOMIKUJ_QUERY": "  Dune  ",
            "CHOMIKUJ_TEST_FILE_ID": "123456",
            "CHOMIKUJ_TEST_PAID_FILE_ID": "654321",
        }
    )
    assert custom.query == "Dune"
    assert custom.test_file_id == "123456"
    assert custom.paid_file_id == "654321"


@pytest.mark.parametrize("variable", ["CHOMIKUJ_TEST_FILE_ID", "CHOMIKUJ_TEST_PAID_FILE_ID"])
@pytest.mark.parametrize("value", ["not-a-file-id", "0", "-1"])
def test_non_numeric_file_ids_are_rejected_before_any_network_call(live_script, variable, value):
    checked = live_script.check_environment(
        {
            "CHOMIKUJ_USERNAME": "alice",
            "CHOMIKUJ_PASSWORD": "secret",
            variable: value,
        }
    )

    assert checked.ok is False
    assert checked.exit_code == 1
    assert variable in checked.message
    assert "numeric" in checked.message


def test_missing_credentials_exit_2_without_running_the_live_path(live_script, monkeypatch):
    monkeypatch.setattr(live_script, "run_live", _no_live_run)
    stdout = StringIO()
    stderr = StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = live_script.main([], env={})

    output = stdout.getvalue() + stderr.getvalue()
    assert code == 2
    assert "CHOMIKUJ_USERNAME" in output
    assert "CHOMIKUJ_PASSWORD" in output
    assert "STEP" not in output


def test_self_test_passes_offline(live_script, monkeypatch):
    monkeypatch.setattr(live_script, "run_live", _no_live_run)
    stdout = StringIO()

    with redirect_stdout(stdout):
        code = live_script.run_self_test()

    output = stdout.getvalue()
    assert code == 0, output
    assert "SELF-TEST: PASS" in output
    assert "token" in output
    assert "decide_download" in output or "quota" in output
    assert "missing" in output
    assert "redaction" in output


def test_main_self_test_flag_returns_zero_offline(live_script, monkeypatch):
    monkeypatch.setattr(live_script, "run_live", _no_live_run)
    stdout = StringIO()

    with redirect_stdout(stdout):
        code = live_script.main(["--self-test"], env={})

    assert code == 0
    assert "SELF-TEST: PASS" in stdout.getvalue()

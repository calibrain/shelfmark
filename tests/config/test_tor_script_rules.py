from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
from contextlib import suppress
from pathlib import Path

import pytest

TOR_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tor.sh"

IPTABLES_BLOCK_START = 'echo "[*] Setting up iptables rules..."'
IPTABLES_BLOCK_END = 'echo "[✓] Transparent Tor routing enabled."'


def _generated_tor_healthcheck_script() -> str:
    script = TOR_SCRIPT_PATH.read_text()
    start = script.index("cat <<'HC' > /app/tor_healthcheck.sh")
    content_start = script.index("\n", start) + 1
    content_end = script.index("\nHC", content_start)
    return script[content_start:content_end]


def _tor_script_rule_lines() -> list[str]:
    """Return the iptables invocations in tor.sh, in file order.

    Rules that are applied on a best-effort basis are written as
    ``if iptables ...; then``, so the shell wrapper is stripped to keep the
    ordering assertions below comparing rules rather than syntax.
    """
    lines = []
    for raw_line in TOR_SCRIPT_PATH.read_text().splitlines():
        line = raw_line.strip().removeprefix("if ")
        if line.startswith("iptables "):
            lines.append(line)
    return lines


def _line_index(lines: list[str], needle: str) -> int:
    return next(index for index, line in enumerate(lines) if needle in line)


def _tor_iptables_block() -> str:
    """Extract the firewall setup section of tor.sh so it can be executed."""
    script = TOR_SCRIPT_PATH.read_text()
    start = script.index(IPTABLES_BLOCK_START)
    end = script.index(IPTABLES_BLOCK_END)
    return script[start:end]


# Verbatim stderr of an iptables build whose kernel lacks the xt_owner module,
# as reported from Synology DSM in issue #1150.
MISSING_XT_OWNER_STDERR = (
    "Warning: Extension owner revision 0 not supported, missing kernel module?\n"
    "iptables: No chain/target/match by that name.\n"
)

IPTABLES_STUB = f"""#!/bin/bash
if [ "$FAKE_OWNER_MATCH_SUPPORTED" != "1" ]; then
    case "$*" in
        *--uid-owner*)
            printf '%s' {MISSING_XT_OWNER_STDERR!r} >&2
            exit 1
            ;;
    esac
fi
echo "$*" >> "$FAKE_IPTABLES_LOG"
exit 0
"""

# tor.sh resolves the Tor uid with `id -u debian-tor`; that account only exists
# inside the image, so stub it out with the uid the Debian tor package uses.
ID_STUB = """#!/bin/bash
echo 107
"""


def _run_tor_iptables_block(tmp_path: Path, *, owner_match_supported: bool):
    """Run tor.sh's firewall block against a fake iptables.

    Returns the completed process plus the rules the fake iptables accepted.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, source in (("iptables", IPTABLES_STUB), ("id", ID_STUB)):
        stub = bin_dir / name
        stub.write_text(source)
        stub.chmod(0o755)

    applied_rules_log = tmp_path / "applied-rules.log"
    # tor.sh runs under `set -e`, which is what turns a rejected rule into a
    # container restart loop, so reproduce that here.
    result = subprocess.run(
        ["bash", "-c", "set -e\n" + _tor_iptables_block()],
        capture_output=True,
        text=True,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_IPTABLES_LOG": str(applied_rules_log),
            "FAKE_OWNER_MATCH_SUPPORTED": "1" if owner_match_supported else "0",
        },
    )
    applied_rules = applied_rules_log.read_text().splitlines() if applied_rules_log.exists() else []
    return result, applied_rules


requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash is required to execute tor.sh"
)

# The watchdog shells out to `timeout`, which is not present on stock macOS.
requires_timeout = pytest.mark.skipif(
    shutil.which("timeout") is None, reason="coreutils timeout is required"
)

SUPERVISORCTL_STUB = """#!/bin/bash
# Emulates `supervisorctl status tor`. Normally reports FAKE_TOR_STATE, but when
# FAKE_TOR_FAIL_FIRST is set the first call reports a transient failure so the
# watchdog's retry behaviour can be exercised.
if [ -n "$FAKE_TOR_FAIL_FIRST" ] && [ ! -f "$FAKE_TOR_CALL_MARKER" ]; then
    touch "$FAKE_TOR_CALL_MARKER"
    echo "tor    FATAL     Exited too quickly"
    exit 3
fi
echo "tor    $FAKE_TOR_STATE   pid 42, uptime 0:01:00"
"""


def _watchdog_path_env(bin_dir: Path) -> str:
    """PATH exposing the stubs plus the real bash/timeout the watchdog calls."""
    parts = [str(bin_dir)]
    for tool in ("bash", "timeout"):
        resolved = shutil.which(tool)
        if resolved:
            parts.append(str(Path(resolved).parent))
    parts += ["/usr/bin", "/bin"]
    return ":".join(parts)


def _run_tor_watchdog(
    tmp_path: Path,
    *,
    tor_state: str = "RUNNING",
    bootstrapped: bool = True,
    trans_port_open: bool = True,
    fail_first: bool = False,
    run_for: float = 2.0,
):
    """Run tor.sh's watchdog against a fake Tor and see if it resets the container.

    Returns ``(reset_signal, stdout)`` where ``reset_signal`` is the signal the
    stand-in for PID 1 received, or ``None`` if it was left alone.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    supervisorctl = bin_dir / "supervisorctl"
    supervisorctl.write_text(SUPERVISORCTL_STUB)
    supervisorctl.chmod(0o755)

    notices_log = tmp_path / "notices.log"
    notices_log.write_text(
        "Bootstrapped 100%: Done\n" if bootstrapped else "Bootstrapped 45%: Loading\n"
    )

    script = tmp_path / "tor_healthcheck.sh"
    script.write_text(_generated_tor_healthcheck_script())
    script.chmod(0o755)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    trans_port = listener.getsockname()[1]
    if trans_port_open:
        listener.listen(5)
    else:
        # Closing it leaves a port nothing is listening on, which is what a dead
        # or wedged Tor looks like from the outside.
        listener.close()

    # Stands in for PID 1: the watchdog signals it instead of dumb-init.
    container_process = subprocess.Popen(
        ["sleep", "120"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    env = {
        "PATH": _watchdog_path_env(bin_dir),
        "TOR_CHECK_INTERVAL": "0.2",
        "TOR_NOTICES_LOG": str(notices_log),
        "TOR_TRANS_PORT": str(trans_port),
        "TOR_CONTAINER_PID": str(container_process.pid),
        "FAKE_TOR_STATE": tor_state,
        "FAKE_TOR_CALL_MARKER": str(tmp_path / "supervisorctl.called"),
    }
    if fail_first:
        env["FAKE_TOR_FAIL_FIRST"] = "1"

    watchdog = subprocess.Popen(
        ["bash", str(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        start_new_session=True,
    )
    try:
        try:
            container_process.wait(timeout=run_for)
        except subprocess.TimeoutExpired:
            pass
        reset_signal = (
            -container_process.returncode if container_process.poll() is not None else None
        )
    finally:
        # Only the watchdog gets its own session; signalling the group is how we
        # reap the `sleep` and `timeout` children it spawns.
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(watchdog.pid), signal.SIGKILL)
        with suppress(ProcessLookupError):
            container_process.kill()
        with suppress(OSError):
            listener.close()
        container_process.wait()
        stdout = watchdog.communicate()[0] or ""

    return reset_signal, stdout


def test_tor_nat_rules_bypass_private_networks_before_tcp_redirect():
    lines = _tor_script_rule_lines()
    tcp_redirect_index = _line_index(lines, "--syn -j REDIRECT --to-ports 9040")

    for cidr in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        rule_index = _line_index(lines, f"-d {cidr} -j RETURN")
        assert rule_index < tcp_redirect_index


def test_tor_nat_rules_exempt_tor_process_before_dns_and_tcp_redirects():
    lines = _tor_script_rule_lines()

    owner_index = _line_index(lines, "-m owner --uid-owner")
    udp_dns_index = _line_index(lines, "-p udp --dport 53")
    tcp_dns_index = _line_index(lines, "-p tcp --dport 53")
    tcp_redirect_index = _line_index(lines, "--syn -j REDIRECT --to-ports 9040")

    assert owner_index < udp_dns_index
    assert owner_index < tcp_dns_index
    assert owner_index < tcp_redirect_index


def test_tor_nat_rules_handle_dns_before_tcp_redirect():
    lines = _tor_script_rule_lines()

    tcp_redirect_index = _line_index(lines, "--syn -j REDIRECT --to-ports 9040")

    assert _line_index(lines, "-p udp --dport 53") < tcp_redirect_index
    assert _line_index(lines, "-p tcp --dport 53") < tcp_redirect_index


def test_tor_healthcheck_uses_local_tor_state_without_clear_net_probe():
    healthcheck_script = _generated_tor_healthcheck_script()

    assert "google.com" not in healthcheck_script
    assert "curl " not in healthcheck_script
    assert "supervisorctl status tor" in healthcheck_script
    assert "Bootstrapped 100%" in healthcheck_script


@requires_bash
def test_tor_iptables_setup_survives_kernel_without_owner_match(tmp_path):
    """Regression test for issue #1150.

    Synology (and other NAS/embedded) kernels ship without xt_owner. The owner
    exemption is a best-effort optimisation, so a kernel that rejects it must
    not abort tor.sh and put the container into a restart loop.
    """
    result, _ = _run_tor_iptables_block(tmp_path, owner_match_supported=False)

    assert result.returncode == 0, (
        "tor.sh aborted on a kernel without xt_owner:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@requires_bash
def test_tor_routing_still_applied_when_owner_match_unsupported(tmp_path):
    """Degrading past the owner rule must not skip the rules that torify traffic."""
    _, applied_rules = _run_tor_iptables_block(tmp_path, owner_match_supported=False)

    assert any("--syn -j REDIRECT --to-ports 9040" in rule for rule in applied_rules)
    for cidr in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"):
        assert any(f"-d {cidr} -j RETURN" in rule for rule in applied_rules)
    for protocol in ("udp", "tcp"):
        assert any(f"-p {protocol} --dport 53" in rule for rule in applied_rules)


@requires_bash
def test_tor_warns_when_owner_match_unsupported(tmp_path):
    """The degraded path has to be visible in the logs, not silent."""
    result, _ = _run_tor_iptables_block(tmp_path, owner_match_supported=False)

    assert "owner" in result.stdout.lower()
    assert "[!]" in result.stdout


@requires_bash
def test_tor_exempts_own_traffic_when_owner_match_supported(tmp_path):
    """On a normal kernel the exemption must still be installed."""
    result, applied_rules = _run_tor_iptables_block(tmp_path, owner_match_supported=True)

    assert result.returncode == 0, result.stderr
    owner_rules = [rule for rule in applied_rules if "--uid-owner" in rule]
    assert len(owner_rules) == 1
    assert "-j RETURN" in owner_rules[0]

    owner_index = applied_rules.index(owner_rules[0])
    redirect_index = next(
        index for index, rule in enumerate(applied_rules) if "--syn -j REDIRECT" in rule
    )
    assert owner_index < redirect_index


@requires_bash
@requires_timeout
def test_tor_watchdog_resets_container_when_tor_dies(tmp_path):
    """A Tor that is no longer running must take the container down with it."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path, tor_state="STOPPED")

    assert reset_signal == signal.SIGTERM, f"container was not reset:\n{stdout}"


@requires_bash
@requires_timeout
def test_tor_watchdog_resets_container_when_tor_stops_accepting_connections(tmp_path):
    """Tor can be 'running' yet unusable; the container must still be reset."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path, tor_state="RUNNING", trans_port_open=False)

    assert reset_signal == signal.SIGTERM, f"container was not reset:\n{stdout}"


@requires_bash
@requires_timeout
def test_tor_watchdog_leaves_healthy_tor_alone(tmp_path):
    """A healthy Tor must never be reset, no matter how many cycles run."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path)

    assert reset_signal is None, f"healthy Tor was reset:\n{stdout}"


@requires_bash
@requires_timeout
def test_tor_watchdog_retries_once_before_resetting(tmp_path):
    """A single transient failure is retried, not escalated to a reset."""
    reset_signal, stdout = _run_tor_watchdog(tmp_path, fail_first=True)

    assert reset_signal is None, f"transient failure caused a reset:\n{stdout}"
    assert "Healthcheck failed (1/2)" in stdout
    assert "Tor recovered." in stdout


@requires_bash
@requires_timeout
def test_tor_watchdog_waits_for_first_bootstrap_before_policing(tmp_path):
    """A slow first bootstrap must not be mistaken for a failure.

    Otherwise every user on a slow link would be reset into a restart loop
    before Tor ever had a chance to come up.
    """
    reset_signal, stdout = _run_tor_watchdog(
        tmp_path, tor_state="STOPPED", bootstrapped=False, trans_port_open=False
    )

    assert reset_signal is None, f"reset during initial bootstrap:\n{stdout}"
    assert "Waiting for initial Tor bootstrap" in stdout


def test_tor_watchdog_escalates_to_container_reset_not_in_place_restart():
    """The watchdog must signal PID 1 rather than bounce Tor under supervisor."""
    healthcheck_script = _generated_tor_healthcheck_script()

    assert "kill -TERM" in healthcheck_script
    assert "supervisorctl restart" not in healthcheck_script

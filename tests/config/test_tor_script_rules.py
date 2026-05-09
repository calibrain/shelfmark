from __future__ import annotations

from pathlib import Path

TOR_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "tor.sh"


def _generated_tor_healthcheck_script() -> str:
    script = TOR_SCRIPT_PATH.read_text()
    start = script.index("cat <<'HC' > /app/tor_healthcheck.sh")
    content_start = script.index("\n", start) + 1
    content_end = script.index("\nHC", content_start)
    return script[content_start:content_end]


def _tor_script_rule_lines() -> list[str]:
    return [
        line.strip()
        for line in TOR_SCRIPT_PATH.read_text().splitlines()
        if line.strip().startswith("iptables ")
    ]


def _line_index(lines: list[str], needle: str) -> int:
    return next(index for index, line in enumerate(lines) if needle in line)


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

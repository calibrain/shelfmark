"""Tests for the pure Chomikuj transfer-quota preflight.

These cases are the executable specification for the plan's quota matrix:
unlimited transfer, owner-marked-free files, paid downloads with spending
disabled, sufficient/insufficient balance, the exact kilobyte boundary, and
unknown inputs that fall back to the authoritative 605 check.

Chomikuj ``account["Transfer"]`` is kilobytes while the handler supplies
``file["size_bytes"]`` in bytes, so the comparison must round the file size up
(``ceil``) and never down. A denial is only a preflight opinion: code 605 from
``files/download`` remains authoritative.
"""

import dataclasses

import pytest

from shelfmark.release_sources.chomikuj.quota import QuotaDecision, decide_download

KB = 1024
ADVISORY = "preflight is advisory and code 605 is authoritative"


def _decide(
    file: dict[str, object],
    account: dict[str, object] | None = None,
    *,
    allow_transfer_spend: bool = True,
) -> QuotaDecision:
    return decide_download(
        file=file,
        account=account,
        allow_transfer_spend=allow_transfer_spend,
    )


def _verdict(
    decision: QuotaDecision,
    expected_allowed: bool,
    needles: tuple[str, ...] = (),
) -> None:
    """Assert the decision shape plus optional reason substrings."""
    assert decision.allowed is expected_allowed
    assert decision.kind == (None if expected_allowed else "error")
    assert isinstance(decision.reason, str)
    assert decision.reason
    for needle in needles:
        assert needle in decision.reason, decision.reason


def test_quota_decision_is_a_frozen_dataclass():
    decision = QuotaDecision(allowed=True, kind=None, reason="ok")
    assert decision.allowed is True
    assert decision.kind is None
    assert decision.reason == "ok"
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.allowed = False  # type: ignore[misc]


def test_unlimited_transfer_wins_even_when_spend_disabled():
    _verdict(
        _decide(
            {"size_bytes": 500 * KB, "is_free_for_user": False},
            {"HasUnlimitedTransfer": True, "Transfer": 0},
            allow_transfer_spend=False,
        ),
        True,
        ("unlimited",),
    )


@pytest.mark.parametrize("marker", ["is_free_for_user", "freeleech"])
def test_free_file_wins_when_spend_disabled(marker: str):
    _verdict(
        _decide(
            {"size_bytes": 500 * KB, marker: True},
            {"HasUnlimitedTransfer": False, "Transfer": 0},
            allow_transfer_spend=False,
        ),
        True,
        ("free",),
    )


def test_spend_disabled_paid_file_denies_with_balance():
    decision = _decide(
        {"size_bytes": 10 * KB, "is_free_for_user": False, "freeleech": False},
        {
            "HasUnlimitedTransfer": False,
            "Transfer": 100,
            "BalanceInfo": "7,0 GB",
            "PointsInfo": "120 pkt",
        },
        allow_transfer_spend=False,
    )
    _verdict(
        decision,
        False,
        (
            "disabled",
            "allow_transfer_spend",
            "BalanceInfo: 7,0 GB",
            "PointsInfo: 120 pkt",
        ),
    )
    assert "not enough transfer" not in decision.reason


def test_spend_check_runs_before_the_balance_check():
    decision = _decide(
        {"size_bytes": 200 * KB},
        {"HasUnlimitedTransfer": False, "Transfer": 100},
        allow_transfer_spend=False,
    )
    assert "not enough transfer" not in decision.reason
    assert "disabled" in decision.reason


def test_enough_transfer_allows():
    _verdict(
        _decide(
            {"size_bytes": 100 * KB},
            {"HasUnlimitedTransfer": False, "Transfer": 100},
        ),
        True,
        ("enough transfer",),
    )


def test_not_enough_transfer_denies():
    decision = _decide(
        {"size_bytes": 200 * KB},
        {
            "HasUnlimitedTransfer": False,
            "Transfer": 100,
            "BalanceInfo": "7,0 GB",
        },
    )
    _verdict(
        decision,
        False,
        ("not enough transfer", "need 200 kB", "have 100 kB", "BalanceInfo: 7,0 GB"),
    )


def test_ceil_boundary_102401_bytes_denies():
    # 102 401 / 1024 = 100.0009... kB, so ceil() needs 101 kB from a 100 kB balance.
    decision = _decide(
        {"size_bytes": 102_401},
        {"HasUnlimitedTransfer": False, "Transfer": 100},
    )
    _verdict(decision, False, ("not enough transfer", "need 101 kB", "have 100 kB"))


def test_exact_kilobyte_boundary_allows():
    _verdict(
        _decide(
            {"size_bytes": 102_400},
            {"HasUnlimitedTransfer": False, "Transfer": 100},
        ),
        True,
        ("enough transfer", "need 100 kB"),
    )


@pytest.mark.parametrize(
    "file",
    [
        {},
        {"size_bytes": None},
        {"size_bytes": ""},
        {"size_bytes": "   "},
        {"size_bytes": "not-a-number"},
        {"size_bytes": True},
    ],
    ids=["missing", "null", "empty", "whitespace", "non-numeric", "bool"],
)
def test_unknown_size_allows_with_advisory(file: dict[str, object]):
    _verdict(
        _decide(file, {"HasUnlimitedTransfer": False, "Transfer": 1}),
        True,
        ("unknown file size", ADVISORY),
    )


@pytest.mark.parametrize(
    "account",
    [
        None,
        {},
        {"HasUnlimitedTransfer": False},
        {"HasUnlimitedTransfer": False, "Transfer": None},
        {"HasUnlimitedTransfer": False, "Transfer": ""},
        {"HasUnlimitedTransfer": False, "Transfer": "unlimited?"},
        {"HasUnlimitedTransfer": False, "Transfer": True},
    ],
    ids=["none", "empty", "missing", "null", "empty-string", "non-numeric", "bool"],
)
def test_unknown_balance_allows_with_advisory(account: dict[str, object] | None):
    _verdict(
        _decide({"size_bytes": 100 * KB}, account),
        True,
        ("unknown balance", ADVISORY),
    )


def test_numeric_string_transfer_is_coerced():
    _verdict(
        _decide(
            {"size_bytes": 1024 * KB},
            {"HasUnlimitedTransfer": False, "Transfer": "1024"},
        ),
        True,
        ("enough transfer",),
    )
    _verdict(
        _decide(
            {"size_bytes": 1025 * KB},
            {"HasUnlimitedTransfer": False, "Transfer": "1024"},
        ),
        False,
        ("not enough transfer",),
    )


def test_numeric_float_values_are_coerced():
    _verdict(
        _decide(
            {"size_bytes": 1024 * KB},
            {"HasUnlimitedTransfer": False, "Transfer": 1024.0},
        ),
        True,
        ("enough transfer", "have 1024 kB"),
    )


def test_literal_true_is_required_for_free_and_unlimited():
    # Non-literal truthy values must not bypass either allow rule.
    _verdict(
        _decide(
            {"size_bytes": 10 * KB, "is_free_for_user": 1, "freeleech": "true"},
            {"HasUnlimitedTransfer": "true", "Transfer": 0},
            allow_transfer_spend=False,
        ),
        False,
        ("disabled",),
    )

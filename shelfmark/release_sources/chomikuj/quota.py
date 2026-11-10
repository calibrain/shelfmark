"""Chomikuj transfer-quota preflight.

:func:`decide_download` is pure and dependency-free: it never performs I/O,
logs, raises, or calls ``files/download``. It answers one question before a
download URL is requested, because Chomikuj charges transfer when the URL is
issued, not when the bytes arrive.

Units are inferred, not documented by the API: ``account["Transfer"]`` and the
search-file ``Size`` are kilobytes, so ``size_bytes`` is divided by 1024 before
comparing. This preflight is advisory; business code 605 from
``files/download`` remains authoritative and is never retryable.

Ported from a validated Chomikuj implementation (``src/lib/quota.mjs``); rule
order is part of the contract. Unlimited transfer and owner-marked-free files
are always allowed, even when paid spending is disabled.
"""

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

FREE_REASON = "allow: file is free for this user"
UNLIMITED_REASON = "allow: unlimited transfer"
ADVISORY = "preflight is advisory and code 605 is authoritative"

_BALANCE_KEYS = ("BalanceInfo", "PointsInfo")


@dataclass(frozen=True)
class QuotaDecision:
    """Outcome of the transfer-quota preflight.

    ``kind`` is ``None`` when the download is allowed, otherwise ``"error"``.
    """

    allowed: bool
    kind: str | None
    reason: str


def _allow(reason: str) -> QuotaDecision:
    """Return an allowed decision with the given reason."""
    return QuotaDecision(allowed=True, kind=None, reason=reason)


def _deny(reason: str) -> QuotaDecision:
    """Return a denied error decision with the given reason."""
    return QuotaDecision(allowed=False, kind="error", reason=reason)


def _finite_number(value: object) -> int | float | None:
    """Coerce a number-like value, treating non-numeric or empty values as unknown.

    Only ``int``, ``float``, and non-empty numeric strings are accepted; ``bool``
    is rejected even though it is an ``int`` subclass, and non-finite floats are
    unknown. Numeric strings keep an integral value exact.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _format_kb(value: int | float) -> str:
    """Render a kilobyte value without a spurious ``.0`` suffix."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _balance_suffix(account: Mapping[str, object] | None) -> str:
    """Render ``BalanceInfo``/``PointsInfo`` for a denial, when the API sent them."""
    if account is None:
        return ""
    parts = []
    for key in _BALANCE_KEYS:
        value = account.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(f"{key}: {text}")
    return f" ({', '.join(parts)})" if parts else ""


def decide_download(
    *,
    file: Mapping[str, object],
    account: Mapping[str, object] | None,
    allow_transfer_spend: bool,
) -> QuotaDecision:
    """Decide whether a file may be downloaded without spending unavailable transfer.

    Rule order matters: unlimited transfer and owner-marked-free files are always
    allowed, even when ``allow_transfer_spend`` is ``False``. Only a literal
    ``False`` disables paid spending.
    """
    # 1. Unlimited transfer wins over every other rule.
    if account is not None and account.get("HasUnlimitedTransfer") is True:
        return _allow(UNLIMITED_REASON)

    # 2. The owner marked the file free for this user: never a paid download.
    if file.get("is_free_for_user") is True or file.get("freeleech") is True:
        return _allow(FREE_REASON)

    # 3. Paid spending disabled: refuse before any chargeable API call.
    if allow_transfer_spend is False:
        return _deny(
            "paid download blocked: transfer spending is disabled "
            f"(allow_transfer_spend=False){_balance_suffix(account)}; "
            "the file is not marked free"
        )

    # 4. Known balance and known size: compare in kilobytes.
    transfer_kb = _finite_number(account.get("Transfer") if account is not None else None)
    size_bytes = _finite_number(file.get("size_bytes"))

    if transfer_kb is not None and size_bytes is not None and size_bytes > 0:
        file_kb = math.ceil(size_bytes / 1024)
        if file_kb > transfer_kb:
            return _deny(
                f"not enough transfer: need {file_kb} kB, "
                f"have {_format_kb(transfer_kb)} kB{_balance_suffix(account)}"
            )
        return _allow(
            f"allow: enough transfer (need {file_kb} kB, have {_format_kb(transfer_kb)} kB)"
        )

    # 5. Missing/unknown balance or size: allow and say the preflight is advisory.
    if size_bytes is None or size_bytes <= 0:
        return _allow(f"allow: unknown file size; {ADVISORY}")
    return _allow(f"allow: unknown balance; {ADVISORY}")

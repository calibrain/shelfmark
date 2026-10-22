"""Answer anti-leech challenges that Soulseek uploaders send as private messages.

Some uploaders run bots that block your downloads until you type a token in a
private message ("please type 'download' in this chat to be added to my
whitelist"). The downloads then sit queued until someone answers.

The challenge patterns and the one-reply-per-cooldown idea come from SoulSync's
``core/chat_autoprove.py`` (https://github.com/Nezreka/SoulSync, commit 445473b, MIT license,
by Nezreka), reworked here for slskd's client and Shelfmark's download handler.

Deliberately conservative:
  - private messages only; only inbound, unacknowledged messages can trigger a reply
  - the token comes from the message itself and must match ``[A-Za-z0-9_-]{1,24}``
  - only the known phrasings below match; anything else is ignored
  - one reply per user per cooldown window (bots repeat their challenge)
  - the cooldown is recorded only after the reply was actually sent
  - every reply is logged at INFO so the operator can see it
"""

from __future__ import annotations

import re
import threading
from typing import TYPE_CHECKING, Any

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.release_sources.slskd.api import SlskdError

if TYPE_CHECKING:
    from collections.abc import Callable

    from shelfmark.release_sources.slskd.api import SlskdClient

logger = setup_logger(__name__)

SCAN_INTERVAL_SECONDS = 60.0
COOLDOWN_SECONDS = 12 * 3600.0

_ERRORS = (SlskdError, requests.exceptions.RequestException)

_WORD = r"[A-Za-z0-9_-]{1,24}"
_PATTERNS = (
    # please type "human" in this chat
    re.compile(rf"""type\s+["']?({_WORD})["']?\s+in\s+(?:this\s+)?chat""", re.IGNORECASE),
    # reply with "human" to be whitelisted
    re.compile(rf"""reply\s+(?:with\s+)?["']?({_WORD})["']?\s+to\s+(?:be|get)""", re.IGNORECASE),
    # say "human" in chat
    re.compile(rf"""say\s+["']?({_WORD})["']?\s+(?:in|to)\b""", re.IGNORECASE),
)


def extract_token(text: object) -> str | None:
    """Return the token a challenge message demands, or None when it is not one."""
    body = str(text or "")
    for pattern in _PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1)
    return None


def _is_unread_inbound(message: Any) -> bool:
    if not isinstance(message, dict):
        return False
    if str(message.get("direction") or "").lower() != "in":
        return False
    return not message.get("acknowledged", message.get("isAcknowledged", False))


def _has_unread(conversation: dict[str, Any]) -> bool:
    count = conversation.get("unAcknowledgedMessageCount")
    return bool(conversation.get("hasUnAcknowledgedMessages")) or (
        isinstance(count, int) and count > 0
    )


def scan_and_respond(
    client: SlskdClient,
    state: dict[str, float],
    *,
    now: float,
    cooldown: float = COOLDOWN_SECONDS,
) -> list[tuple[str, str]]:
    """Run one pass and return the ``(username, token)`` replies sent.

    ``state`` maps a username to the time of the last reply and is owned by the
    caller. A failure on one conversation does not stop the others.
    """
    replies: list[tuple[str, str]] = []
    for conversation in client.list_conversations():
        username = str(conversation.get("username") or "")
        if not username or not _has_unread(conversation):
            continue
        last = state.get(username)
        if last is not None and now - last < cooldown:
            continue

        try:
            detail = client.get_conversation(username) or {}
            token = None
            for message in detail.get("messages") or []:
                if _is_unread_inbound(message):
                    token = extract_token(message.get("message"))
                    if token:
                        break
            if not token:
                continue
            client.send_private_message(username, token)
        except _ERRORS as e:
            logger.warning("slskd chat challenge: reply to %s failed: %s", username, e)
            continue

        state[username] = now
        logger.info("slskd chat challenge: answered %r to %s", token, username)
        replies.append((username, token))
        try:
            client.acknowledge_conversation(username)
        except _ERRORS as e:
            logger.debug("slskd chat challenge: acknowledge for %s failed: %s", username, e)
    return replies


class ChatAutoProver:
    """Runs ``scan_and_respond`` at most once per interval, from whoever calls it.

    The slskd handler already loops while a download is queued, so it calls
    ``maybe_scan`` each iteration; no thread or scheduler of its own.
    """

    def __init__(
        self,
        *,
        interval: float = SCAN_INTERVAL_SECONDS,
        cooldown: float = COOLDOWN_SECONDS,
    ) -> None:
        self._interval = interval
        self._cooldown = cooldown
        self._lock = threading.Lock()
        self._last_scan: float | None = None
        self._state: dict[str, float] = {}

    def maybe_scan(
        self,
        get_client: Callable[[], SlskdClient | None],
        clock: Callable[[], float],
    ) -> list[tuple[str, str]]:
        """Scan when the interval has passed; never raises, quiet when slskd is down."""
        now = clock()
        with self._lock:
            if self._last_scan is not None and now - self._last_scan < self._interval:
                return []
            self._last_scan = now
        try:
            client = get_client()
            if client is None:
                return []
            return scan_and_respond(client, self._state, now=now, cooldown=self._cooldown)
        except _ERRORS as e:
            logger.debug("slskd chat challenge: scan skipped: %s", e)
            return []

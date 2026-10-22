"""Unit tests for the slskd anti-leech chat challenge responder."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.release_sources.slskd import handler as handler_module
from shelfmark.release_sources.slskd.api import SlskdClient, SlskdError
from shelfmark.release_sources.slskd.chat_autoprove import (
    COOLDOWN_SECONDS,
    ChatAutoProver,
    extract_token,
    scan_and_respond,
)

# ── helpers ────────────────────────────────────────────────────────────────────


def _convo(username="peer", *, unread=True):
    return {"username": username, "hasUnAcknowledgedMessages": unread}


def _msg(text, *, direction="In", acknowledged=False):
    return {"direction": direction, "acknowledged": acknowledged, "message": text}


def _client(conversations, messages_by_user):
    client = MagicMock(spec=SlskdClient)
    client.list_conversations.return_value = conversations
    client.get_conversation.side_effect = lambda u: {
        "username": u,
        "messages": messages_by_user.get(u, []),
    }
    return client


CHALLENGE = "ProveIt: To prove you are human, please type 'download' in this chat."

# ── token extraction ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "token"),
    [
        (CHALLENGE, "download"),
        ('please type "human" in chat to be whitelisted', "human"),
        ('Please reply with "human" to be added to my whitelist', "human"),
        ("reply open_sesame to get access", "open_sesame"),
        ('say "ok-1" in chat', "ok-1"),
        ("SAY hello TO me", "hello"),
    ],
)
def test_extract_token_known_phrasings(text, token):
    assert extract_token(text) == token


@pytest.mark.parametrize(
    "text",
    [
        "",
        None,
        "thanks for downloading!",
        "i will type the title in a minute",
        f"please type '{'a' * 25}' in this chat",
        "please type 'rm -rf /' in this chat",
        "please type 'bad;word' in this chat",
        "reply with 'x y' to be added",
    ],
)
def test_extract_token_ignores_non_challenges(text):
    assert extract_token(text) is None


# ── scan_and_respond ───────────────────────────────────────────────────────────


def test_replies_with_token_and_acknowledges():
    client = _client([_convo()], {"peer": [_msg(CHALLENGE)]})
    state: dict[str, float] = {}

    replies = scan_and_respond(client, state, now=100.0)

    assert replies == [("peer", "download")]
    client.send_private_message.assert_called_once_with("peer", "download")
    client.acknowledge_conversation.assert_called_once_with("peer")
    assert state == {"peer": 100.0}


def test_acknowledge_happens_after_send():
    order = []
    client = _client([_convo()], {"peer": [_msg(CHALLENGE)]})
    client.send_private_message.side_effect = lambda *a: order.append("send")
    client.acknowledge_conversation.side_effect = lambda *a: order.append("ack")

    scan_and_respond(client, {}, now=1.0)

    assert order == ["send", "ack"]


def test_cooldown_blocks_second_reply_until_it_expires():
    client = _client([_convo()], {"peer": [_msg(CHALLENGE)]})
    state: dict[str, float] = {}

    assert scan_and_respond(client, state, now=0.0)
    assert scan_and_respond(client, state, now=COOLDOWN_SECONDS - 1) == []
    assert scan_and_respond(client, state, now=COOLDOWN_SECONDS + 1) == [("peer", "download")]
    assert client.send_private_message.call_count == 2


def test_only_unacknowledged_inbound_messages_are_answered():
    client = _client(
        [_convo()],
        {
            "peer": [
                _msg(CHALLENGE, acknowledged=True),
                _msg(CHALLENGE, direction="Out"),
            ]
        },
    )
    assert scan_and_respond(client, {}, now=0.0) == []
    client.send_private_message.assert_not_called()


def test_conversation_without_unread_flag_is_not_fetched():
    client = _client([_convo(unread=False)], {"peer": [_msg(CHALLENGE)]})
    assert scan_and_respond(client, {}, now=0.0) == []
    client.get_conversation.assert_not_called()


def test_unread_count_alone_marks_a_conversation_unread():
    convo = {"username": "peer", "unAcknowledgedMessageCount": 2}
    client = _client([convo], {"peer": [_msg(CHALLENGE)]})
    assert scan_and_respond(client, {}, now=0.0) == [("peer", "download")]


def test_send_failure_does_not_record_cooldown_or_acknowledge():
    client = _client([_convo()], {"peer": [_msg(CHALLENGE)]})
    client.send_private_message.side_effect = SlskdError("boom")
    state: dict[str, float] = {}

    assert scan_and_respond(client, state, now=0.0) == []

    assert state == {}
    client.acknowledge_conversation.assert_not_called()
    # The next pass may try again.
    client.send_private_message.side_effect = None
    assert scan_and_respond(client, state, now=1.0) == [("peer", "download")]


def test_acknowledge_failure_keeps_the_reply_and_cooldown():
    client = _client([_convo()], {"peer": [_msg(CHALLENGE)]})
    client.acknowledge_conversation.side_effect = requests.exceptions.ConnectionError()
    state: dict[str, float] = {}

    assert scan_and_respond(client, state, now=5.0) == [("peer", "download")]
    assert state == {"peer": 5.0}


def test_one_failing_conversation_does_not_stop_the_others():
    client = _client(
        [_convo("a"), _convo("b")],
        {"a": [_msg(CHALLENGE)], "b": [_msg(CHALLENGE)]},
    )
    client.send_private_message.side_effect = [SlskdError("no"), None]

    assert scan_and_respond(client, {}, now=0.0) == [("b", "download")]


def test_reply_is_logged():
    client = _client([_convo()], {"peer": [_msg(CHALLENGE)]})
    with patch("shelfmark.release_sources.slskd.chat_autoprove.logger") as log:
        scan_and_respond(client, {}, now=0.0)
    log.info.assert_called_once()
    assert log.info.call_args.args[1:] == ("download", "peer")


# ── ChatAutoProver ─────────────────────────────────────────────────────────────


def test_prover_scans_at_most_once_per_interval():
    client = _client([], {})
    prover = ChatAutoProver(interval=60)
    clock = iter([0.0, 30.0, 61.0])

    for _ in range(3):
        prover.maybe_scan(lambda: client, lambda: next(clock))

    assert client.list_conversations.call_count == 2


def test_prover_is_quiet_when_slskd_is_unreachable():
    client = MagicMock(spec=SlskdClient)
    client.list_conversations.side_effect = requests.exceptions.ConnectionError()
    prover = ChatAutoProver()

    assert prover.maybe_scan(lambda: client, lambda: 0.0) == []


def test_prover_skips_when_no_client_configured():
    assert ChatAutoProver().maybe_scan(lambda: None, lambda: 0.0) == []


# ── handler wiring / setting ───────────────────────────────────────────────────


def _handler():
    return handler_module.SlskdHandler()


def test_handler_scans_when_setting_is_on():
    client = MagicMock(spec=SlskdClient)
    with (
        patch.object(handler_module, "_bool_setting", return_value=True),
        patch.object(handler_module._CHAT_AUTOPROVER, "maybe_scan") as scan,
    ):
        _handler()._answer_chat_challenges(client)
    scan.assert_called_once()


def test_handler_does_nothing_when_setting_is_off():
    client = MagicMock(spec=SlskdClient)
    with (
        patch.object(handler_module, "_bool_setting", return_value=False) as setting,
        patch.object(handler_module._CHAT_AUTOPROVER, "maybe_scan") as scan,
    ):
        _handler()._answer_chat_challenges(client)
    setting.assert_called_once_with("SLSKD_CHAT_AUTOPROVE", False)
    scan.assert_not_called()
    client.list_conversations.assert_not_called()

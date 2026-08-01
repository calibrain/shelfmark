"""Focused tests for personal and administrator notification delivery."""

import smtplib
from email.utils import parseaddr

from shelfmark.core import notifications as notifications_module
from shelfmark.download.outputs import email as email_module


class _Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


class _UserDB:
    def __init__(self, preferences, email="reader@example.com"):
        self.preferences = preferences
        self.email = email

    def get_personal_preferences(self, _user_id):
        return self.preferences

    def get_user(self, user_id):
        return {"id": user_id, "email": self.email}


def _context(event):
    return notifications_module.NotificationContext(
        event=event, title="Book", author="Author", book_id=17
    )


def test_personal_delivery_uses_canonical_email(monkeypatch):
    executor = _Executor()
    monkeypatch.setattr(notifications_module, "_executor", executor)
    user_db = _UserDB(
        {
            "notifications_enabled": True,
            "notification_transport": None,
            "notification_destination": None,
        }
    )
    notifications_module.notify_user(
        user_db,
        4,
        notifications_module.NotificationEvent.REQUEST_FULFILLED,
        _context(notifications_module.NotificationEvent.REQUEST_FULFILLED),
    )
    assert executor.calls[0][1][0:2] == ("email", "reader@example.com")


def test_personal_email_test_uses_configured_sender_and_account_recipient(monkeypatch):
    sent_messages = []

    class SenderRequiredSmtp:
        def __init__(self, *_args, **_kwargs):
            pass

        def ehlo(self):
            pass

        def send_message(self, message):
            sender = parseaddr(message["From"])[1]
            if not sender:
                raise smtplib.SMTPSenderRefused(550, "Sender required", "")
            sent_messages.append((sender, message))

        def quit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(
        notifications_module,
        "_get_email_settings",
        lambda: {
            "EMAIL_SMTP_HOST": "smtp.example.com",
            "EMAIL_SMTP_PORT": 25,
            "EMAIL_SMTP_SECURITY": "none",
            "EMAIL_FROM": "Shelfmark <sender@example.com>",
        },
    )
    monkeypatch.setattr(email_module.smtplib, "SMTP", SenderRequiredSmtp)

    result = notifications_module.send_personal_test_notification(
        _UserDB(
            {
                "notifications_enabled": True,
                "notification_transport": None,
                "notification_destination": None,
            }
        ),
        4,
    )

    assert result == {"success": True, "message": "Notification sent"}
    sender, message = sent_messages[0]
    assert sender == "sender@example.com"
    assert message["From"] == "Shelfmark <sender@example.com>"
    assert message["To"] == "reader@example.com"


def test_personal_email_delivery_uses_configured_sender_and_account_recipient(monkeypatch):
    smtp_config = type("SmtpConfig", (), {"from_addr": "Shelfmark <sender@example.com>"})()
    sent_messages = []
    monkeypatch.setattr(notifications_module, "_get_email_settings", lambda: {})
    monkeypatch.setattr(
        notifications_module, "build_email_smtp_config", lambda _settings: smtp_config
    )
    monkeypatch.setattr(
        notifications_module,
        "send_email_message",
        lambda config, message: sent_messages.append((config, message)),
    )

    result = notifications_module._deliver(
        "email",
        "reader@example.com",
        notifications_module.NotificationEvent.REQUEST_REJECTED,
        notifications_module.NotificationContext(
            event=notifications_module.NotificationEvent.REQUEST_REJECTED,
            title="Book",
            author="Author",
            admin_note="Not available",
        ),
    )

    assert result == {"success": True, "message": "Notification sent"}
    config, message = sent_messages[0]
    assert config is smtp_config
    assert message["From"] == "Shelfmark <sender@example.com>"
    assert message["To"] == "reader@example.com"
    assert "Note: Not available" in message.get_content()


def test_personal_delivery_ignores_operational_events(monkeypatch):
    executor = _Executor()
    monkeypatch.setattr(notifications_module, "_executor", executor)
    user_db = _UserDB(
        {
            "notifications_enabled": True,
            "notification_transport": "apprise",
            "notification_destination": "ntfys://ntfy.sh/reader",
        }
    )
    notifications_module.notify_user(
        user_db,
        4,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        _context(notifications_module.NotificationEvent.DOWNLOAD_FAILED),
    )
    assert executor.calls == []


def test_disabled_personal_notifications_do_not_queue_request_outcomes(monkeypatch):
    executor = _Executor()
    monkeypatch.setattr(notifications_module, "_executor", executor)
    user_db = _UserDB(
        {
            "notifications_enabled": False,
            "notification_transport": None,
            "notification_destination": None,
        }
    )

    for event in (
        notifications_module.NotificationEvent.REQUEST_REJECTED,
        notifications_module.NotificationEvent.REQUEST_FULFILLED,
    ):
        notifications_module.notify_user(user_db, 4, event, _context(event))

    assert executor.calls == []


def test_available_message_has_book_context_and_link():
    title, body = notifications_module._render_message(
        _context(notifications_module.NotificationEvent.REQUEST_FULFILLED)
    )
    assert title == "Requested Book Available"
    assert '"Book" by Author' in body
    assert "/library/17" in body


def test_admin_targets_only_receive_operational_event_subscriptions(monkeypatch):
    executor = _Executor()
    monkeypatch.setattr(notifications_module, "_executor", executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_targets",
        lambda: [
            {
                "transport": "apprise",
                "destination": "ntfys://ntfy.sh/ops",
                "events": ["request_created"],
            }
        ],
    )
    notifications_module.notify_admin(
        notifications_module.NotificationEvent.REQUEST_CREATED,
        _context(notifications_module.NotificationEvent.REQUEST_CREATED),
    )
    notifications_module.notify_admin(
        notifications_module.NotificationEvent.REQUEST_REJECTED,
        _context(notifications_module.NotificationEvent.REQUEST_REJECTED),
    )
    assert len(executor.calls) == 1


def test_personal_test_requires_an_active_valid_destination():
    result = notifications_module.send_personal_test_notification(
        _UserDB(
            {
                "notifications_enabled": False,
                "notification_transport": "email",
                "notification_destination": "reader@example.com",
            }
        ),
        1,
    )
    assert result["success"] is False

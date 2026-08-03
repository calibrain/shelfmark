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
    plain_part = message.get_body(preferencelist=("plain",))
    assert plain_part is not None
    assert "Note: Not available" in plain_part.get_content()
    html_part = message.get_body(preferencelist=("html",))
    assert html_part is not None
    assert message["Subject"] == "Request Rejected"


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


def _conf_get(base_url, base_path):
    def get(key, default=None):
        return {"NOTIFICATION_BASE_URL": base_url, "URL_BASE": base_path}.get(key, default)

    return get


def _book(monkeypatch):
    return {
        "id": 17,
        "title": "The Book & <Title>",
        "author": "Author",
        "subtitle": "A subtitle",
        "publish_year": 2007,
        "series_name": "Series",
        "series_position": 2,
        "language": "en",
        "isbn_13": "9780000000000",
        "cover_url": "https://img.example.com/cover.jpg",
        "metadata_json": {"description": "A short description.", "display_fields": []},
    }


def test_render_message_is_test_uses_test_subject():
    title, body = notifications_module._render_message(
        notifications_module.NotificationContext(
            event=notifications_module.NotificationEvent.REQUEST_CREATED,
            title="Shelfmark Test Notification",
            author="Shelfmark",
            username="Shelfmark",
            is_test=True,
        )
    )
    assert title == "Shelfmark Test Notification"
    assert "test notification" in body


def test_build_book_url_falls_back_to_relative_without_base_url(monkeypatch):
    monkeypatch.setattr(
        notifications_module.app_config, "get", _conf_get("", "")
    )
    assert notifications_module._build_book_url(17) == "/library/17"


def test_build_book_url_combines_base_and_base_path(monkeypatch):
    monkeypatch.setattr(
        notifications_module.app_config,
        "get",
        _conf_get("https://shelfmark.example.com", "/shelfmark"),
    )
    assert (
        notifications_module._build_book_url(17)
        == "https://shelfmark.example.com/shelfmark/library/17"
    )


def test_html_email_includes_absolute_link_and_book_card(monkeypatch):
    monkeypatch.setattr(
        notifications_module.app_config,
        "get",
        _conf_get("https://shelfmark.example.com", "/shelfmark"),
    )
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_FULFILLED,
        title="The Book",
        author="Author",
        book_id=17,
        book=_book(monkeypatch),
    )
    html = notifications_module._render_html_email(context)
    assert "https://shelfmark.example.com/shelfmark/library/17" in html
    assert "https://img.example.com/cover.jpg" in html
    assert "View in Library" in html
    assert "The Book &amp; &lt;Title&gt;" in html
    assert "About this book" in html


def test_html_email_hides_book_card_without_book(monkeypatch):
    monkeypatch.setattr(
        notifications_module.app_config, "get", _conf_get("", "")
    )
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_COMPLETE,
        title="A Book",
        author="Author",
    )
    html = notifications_module._render_html_email(context)
    assert "View in Library" not in html


def test_html_email_test_template_renders_sample_card(monkeypatch):
    monkeypatch.setattr(
        notifications_module.app_config,
        "get",
        _conf_get("https://shelfmark.example.com", ""),
    )
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_CREATED,
        title="Shelfmark Test Notification",
        author="Shelfmark",
        username="Shelfmark",
        is_test=True,
        book=dict(notifications_module._SAMPLE_BOOK),
    )
    html = notifications_module._render_html_email(context)
    assert "Shelfmark test email" in html
    assert "https://shelfmark.example.com/library" in html
    assert "No cover" in html
    assert "View in Library" in html


"""Focused tests for personal and administrator notification delivery."""

from shelfmark.core import notifications as notifications_module


class _Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))


class _UserDB:
    def __init__(self, preferences):
        self.preferences = preferences

    def get_personal_preferences(self, _user_id):
        return self.preferences


def _context(event):
    return notifications_module.NotificationContext(
        event=event, title="Book", author="Author", book_id=17
    )


def test_personal_delivery_uses_saved_enabled_email_destination(monkeypatch):
    executor = _Executor()
    monkeypatch.setattr(notifications_module, "_executor", executor)
    user_db = _UserDB(
        {
            "notifications_enabled": True,
            "notification_transport": "email",
            "notification_destination": "reader@example.com",
        }
    )
    notifications_module.notify_user(
        user_db,
        4,
        notifications_module.NotificationEvent.REQUEST_FULFILLED,
        _context(notifications_module.NotificationEvent.REQUEST_FULFILLED),
    )
    assert executor.calls[0][1][0:2] == ("email", "reader@example.com")


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

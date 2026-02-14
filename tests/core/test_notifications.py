"""Tests for core notification rendering and dispatch helpers."""

from shelfmark.core import notifications as notifications_module


class _FakeExecutor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        self.calls.append((fn, args, kwargs))
        return object()


class _FakeNotifyType:
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILURE = "FAILURE"


class _FakeAppriseClient:
    def __init__(self):
        self.add_calls = []
        self.notify_calls = []

    def add(self, url):
        self.add_calls.append(url)
        return True

    def notify(self, **kwargs):
        self.notify_calls.append(kwargs)
        return True


class _FakeAppriseModule:
    NotifyType = _FakeNotifyType

    def __init__(self):
        self.client = _FakeAppriseClient()

    def Apprise(self):
        return self.client


def test_render_message_includes_admin_note_for_rejection():
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_REJECTED,
        title="Example Book",
        author="Example Author",
        admin_note="Missing metadata",
    )

    title, body = notifications_module._render_message(context)

    assert title == "Request Rejected"
    assert "Missing metadata" in body


def test_render_message_includes_error_line_for_download_failure():
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        title="Example Book",
        author="Example Author",
        error_message="Connection timeout",
    )

    title, body = notifications_module._render_message(context)

    assert title == "Download Failed"
    assert "Connection timeout" in body


def test_notify_admin_submits_non_blocking_when_event_is_subscribed(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_urls_and_events",
        lambda: (["discord://Webhook/Token"], {"request_created"}),
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_CREATED,
        title="Example Book",
        author="Example Author",
        username="reader",
    )

    notifications_module.notify_admin(
        notifications_module.NotificationEvent.REQUEST_CREATED,
        context,
    )

    assert len(fake_executor.calls) == 1


def test_notify_admin_skips_when_event_not_subscribed(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_urls_and_events",
        lambda: (["discord://Webhook/Token"], {"download_failed"}),
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_CREATED,
        title="Example Book",
        author="Example Author",
    )

    notifications_module.notify_admin(
        notifications_module.NotificationEvent.REQUEST_CREATED,
        context,
    )

    assert fake_executor.calls == []


def test_send_admin_event_passes_expected_title_body_and_notify_type(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_REJECTED,
        title="Example Book",
        author="Example Author",
        admin_note="Rule blocked this source",
    )

    result = notifications_module._send_admin_event(
        notifications_module.NotificationEvent.REQUEST_REJECTED,
        context,
        ["discord://Webhook/Token"],
    )

    assert result["success"] is True
    assert fake_apprise.client.notify_calls
    notify_kwargs = fake_apprise.client.notify_calls[0]
    assert notify_kwargs["title"] == "Request Rejected"
    assert "Rule blocked this source" in notify_kwargs["body"]
    assert notify_kwargs["notify_type"] == _FakeNotifyType.WARNING


def test_resolve_admin_urls_and_events_returns_empty_when_disabled(monkeypatch):
    def _fake_get(key, default=None):
        if key == "NOTIFICATIONS_ENABLED":
            return False
        return default

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    urls, events = notifications_module._resolve_admin_urls_and_events()

    assert urls == []
    assert events == set()


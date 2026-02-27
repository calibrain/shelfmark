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
        self.notify_result = True

    def add(self, url):
        self.add_calls.append(url)
        return True

    def notify(self, **kwargs):
        self.notify_calls.append(kwargs)
        return self.notify_result


class _FakeAppriseModule:
    NotifyType = _FakeNotifyType
    asset_kwargs: dict[str, str] | None = None

    def __init__(self):
        self.client = _FakeAppriseClient()
        self.apprise_kwargs = {}

    class AppriseAsset:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def Apprise(self, *args, **kwargs):
        self.apprise_kwargs = kwargs
        asset = kwargs.get("asset")
        self.asset_kwargs = getattr(asset, "kwargs", None)
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


def test_render_message_uses_request_approved_copy():
    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.REQUEST_FULFILLED,
        title="Example Book",
        author="Example Author",
    )

    title, body = notifications_module._render_message(context)

    assert title == "Request Approved"
    assert "was approved." in body


def test_notify_admin_submits_non_blocking_when_route_matches_event(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_routes",
        lambda: [{"event": "request_created", "url": "discord://Webhook/Token"}],
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


def test_notify_admin_skips_when_no_route_matches_event(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_admin_routes",
        lambda: [{"event": "download_failed", "url": "discord://Webhook/Token"}],
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


def test_dispatch_to_apprise_uses_shelfmark_asset_defaults(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    result = notifications_module._dispatch_to_apprise(
        ["ntfys://ntfy.sh/shelfmark"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is True
    assert fake_apprise.asset_kwargs is not None
    assert fake_apprise.asset_kwargs["app_id"] == "Shelfmark"
    assert "logo.png" in fake_apprise.asset_kwargs["image_url_logo"]


def test_dispatch_to_apprise_notify_false_returns_generic_failure_and_logs(monkeypatch):
    fake_apprise = _FakeAppriseModule()
    fake_apprise.client.notify_result = False
    monkeypatch.setattr(notifications_module, "apprise", fake_apprise)

    warning_messages: list[str] = []

    def _fake_warning(message, *args, **kwargs):
        _ = kwargs
        warning_messages.append(message % args if args else str(message))

    monkeypatch.setattr(notifications_module.logger, "warning", _fake_warning)

    result = notifications_module._dispatch_to_apprise(
        ["pover://user_key@app_token"],
        title="Test",
        body="Body",
        notify_type=_FakeNotifyType.INFO,
    )

    assert result["success"] is False
    assert result["message"] == "Notification delivery failed"
    assert any("scheme(s): pover" in message for message in warning_messages)


def test_resolve_admin_routes_returns_empty_when_no_routes(monkeypatch):
    def _fake_get(key, default=None):
        if key == "ADMIN_NOTIFICATION_ROUTES":
            return []
        return default

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    routes = notifications_module._resolve_admin_routes()

    assert routes == []


def test_resolve_user_routes_uses_user_overrides(monkeypatch):
    def _fake_get(key, default=None, user_id=None):
        if user_id != 42:
            return default
        values = {
            "USER_NOTIFICATION_ROUTES": [
                {"event": "all", "url": " ntfys://ntfy.sh/alice "},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
                {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
            ],
        }
        return values.get(key, default)

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    routes = notifications_module._resolve_user_routes(42)

    assert routes == [
        {"event": "all", "url": "ntfys://ntfy.sh/alice"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
    ]


def test_notify_user_submits_non_blocking_when_route_matches_event(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)
    monkeypatch.setattr(
        notifications_module,
        "_resolve_user_routes",
        lambda _user_id: [{"event": "download_failed", "url": "discord://Webhook/Token"}],
    )

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        title="Example Book",
        author="Example Author",
        username="reader",
    )

    notifications_module.notify_user(
        7,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
        context,
    )

    assert len(fake_executor.calls) == 1


def test_notify_user_skips_when_user_id_is_invalid(monkeypatch):
    fake_executor = _FakeExecutor()
    monkeypatch.setattr(notifications_module, "_executor", fake_executor)

    context = notifications_module.NotificationContext(
        event=notifications_module.NotificationEvent.DOWNLOAD_COMPLETE,
        title="Example Book",
        author="Example Author",
    )

    notifications_module.notify_user(
        None,
        notifications_module.NotificationEvent.DOWNLOAD_COMPLETE,
        context,
    )

    assert fake_executor.calls == []


def test_resolve_route_urls_for_event_includes_all_and_specific_rows():
    routes = [
        {"event": "all", "url": "ntfys://ntfy.sh/all"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
        {"event": "request_created", "url": "ntfys://ntfy.sh/requests"},
    ]

    urls = notifications_module._resolve_route_urls_for_event(
        routes,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
    )

    assert urls == [
        "ntfys://ntfy.sh/all",
        "ntfys://ntfy.sh/errors",
    ]


def test_resolve_route_urls_for_event_deduplicates_matching_urls():
    routes = [
        {"event": "all", "url": "ntfys://ntfy.sh/shared"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/shared"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/errors"},
    ]

    urls = notifications_module._resolve_route_urls_for_event(
        routes,
        notifications_module.NotificationEvent.DOWNLOAD_FAILED,
    )

    assert urls == [
        "ntfys://ntfy.sh/shared",
        "ntfys://ntfy.sh/errors",
    ]


def test_resolve_admin_routes_expands_multiselect_event_rows(monkeypatch):
    def _fake_get(key, default=None):
        if key == "ADMIN_NOTIFICATION_ROUTES":
            return [
                {"event": ["request_created", "download_failed"], "url": "ntfys://ntfy.sh/multi"},
                {"event": ["all", "download_complete"], "url": "ntfys://ntfy.sh/all"},
            ]
        return default

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    routes = notifications_module._resolve_admin_routes()

    assert routes == [
        {"event": "request_created", "url": "ntfys://ntfy.sh/multi"},
        {"event": "download_failed", "url": "ntfys://ntfy.sh/multi"},
        {"event": "all", "url": "ntfys://ntfy.sh/all"},
    ]


def test_resolve_user_routes_expands_multiselect_event_rows(monkeypatch):
    def _fake_get(key, default=None, user_id=None):
        if key != "USER_NOTIFICATION_ROUTES" or user_id != 7:
            return default
        return [
            {"event": ["download_complete", "request_fulfilled"], "url": "ntfys://ntfy.sh/user-main"},
            {"event": ["all", "download_failed"], "url": "ntfys://ntfy.sh/user-all"},
        ]

    monkeypatch.setattr(notifications_module.app_config, "get", _fake_get)

    routes = notifications_module._resolve_user_routes(7)

    assert routes == [
        {"event": "download_complete", "url": "ntfys://ntfy.sh/user-main"},
        {"event": "request_fulfilled", "url": "ntfys://ntfy.sh/user-main"},
        {"event": "all", "url": "ntfys://ntfy.sh/user-all"},
    ]

def test_update_settings_network_logs_dns_apply_failure(monkeypatch):
    import shelfmark.config.settings  # noqa: F401
    import shelfmark.core.settings_registry as registry
    from shelfmark.core.config import config as config_obj
    from shelfmark.core.settings_registry import update_settings

    monkeypatch.setattr("shelfmark.core.settings_registry.save_config_file", lambda _tab, _values: True)
    monkeypatch.setattr(config_obj, "refresh", lambda: None)

    import shelfmark.download.network as network

    def failing_set_dns_provider(*args, **kwargs) -> None:
        raise RuntimeError("dns apply failed")

    monkeypatch.setattr(network, "set_dns_provider", failing_set_dns_provider)

    warnings: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        registry.logger,
        "warning",
        lambda message, *args: warnings.append((str(message), args)),
    )

    result = update_settings(
        "network",
        {
            "CUSTOM_DNS": "manual",
            "CUSTOM_DNS_MANUAL": "1.1.1.1,8.8.8.8",
        },
    )

    assert result["success"] is True
    assert len(warnings) == 1
    message, args = warnings[0]
    assert message == "Failed to apply DNS settings: %s"
    assert len(args) == 1
    assert isinstance(args[0], RuntimeError)
    assert str(args[0]) == "dns apply failed"

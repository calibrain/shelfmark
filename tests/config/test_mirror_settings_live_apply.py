def test_update_settings_mirrors_applies_aa_changes_live(monkeypatch):
    # Ensure settings tabs are registered (mirrors tab lives here).
    import shelfmark.config.settings  # noqa: F401
    from shelfmark.core.config import config as config_obj
    from shelfmark.core.settings_registry import update_settings

    monkeypatch.delenv("AA_BASE_URL", raising=False)
    monkeypatch.delenv("AA_ADDITIONAL_URLS", raising=False)

    # Avoid writing to disk and avoid forcing a full config refresh in this unit test.
    monkeypatch.setattr(
        "shelfmark.core.settings_registry.save_config_file", lambda _tab, _values: True
    )
    monkeypatch.setattr(config_obj, "refresh", lambda: None)

    called: dict[str, object] = {}

    def fake_init_aa(*, force: bool = False) -> None:
        called["force"] = force

    import shelfmark.download.network as network

    monkeypatch.setattr(network, "init_aa", fake_init_aa)

    result = update_settings("mirrors", {"AA_BASE_URL": "https://annas-archive.li"})

    assert result["success"] is True
    assert called["force"] is True


def test_update_settings_mirrors_logs_live_apply_failure(monkeypatch):
    import shelfmark.config.settings  # noqa: F401
    import shelfmark.core.settings_registry as registry
    from shelfmark.core.config import config as config_obj
    from shelfmark.core.settings_registry import update_settings

    monkeypatch.setattr(
        "shelfmark.core.settings_registry.save_config_file", lambda _tab, _values: True
    )
    monkeypatch.setattr(config_obj, "refresh", lambda: None)

    import shelfmark.download.network as network

    def failing_init_aa(*, force: bool = False) -> None:
        del force
        raise RuntimeError("mirror apply failed")

    monkeypatch.setattr(network, "init_aa", failing_init_aa)

    warnings: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(
        registry.logger,
        "warning",
        lambda message, *args: warnings.append((str(message), args)),
    )

    result = update_settings("mirrors", {"AA_BASE_URL": "https://annas-archive.li"})

    assert result["success"] is True
    assert len(warnings) == 1
    message, args = warnings[0]
    assert message == "Failed to apply AA mirror settings: %s"
    assert len(args) == 1
    assert isinstance(args[0], RuntimeError)
    assert str(args[0]) == "mirror apply failed"

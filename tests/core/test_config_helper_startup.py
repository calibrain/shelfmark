"""Configuration behavior for the internal bypass helper process."""


def _fresh_config(monkeypatch):
    import shelfmark.core.config as config_module

    monkeypatch.setattr(config_module.Config, "_instance", None)
    return config_module.Config(), config_module._get_registry()


def test_bypass_helper_does_not_persist_inherited_environment(monkeypatch) -> None:
    """A cold helper must not rewrite bind-mounted config before it can serve work."""
    monkeypatch.setenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", "1")
    config, registry = _fresh_config(monkeypatch)

    def _unexpected_sync() -> None:
        raise AssertionError("bypass helper attempted to persist inherited environment")

    monkeypatch.setattr(registry, "sync_env_to_config", _unexpected_sync)
    monkeypatch.setattr(registry, "get_settings_field_map", lambda: {})

    config._load_settings()


def test_config_load_reads_each_tab_once(monkeypatch) -> None:
    """Loading all fields must not reopen the same bind-mounted JSON for every field."""
    monkeypatch.setenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", "1")
    config, registry = _fresh_config(monkeypatch)
    load_calls: list[str] = []

    def _load_config(tab_name: str) -> dict:
        load_calls.append(tab_name)
        return {}

    monkeypatch.setattr(registry, "load_config_file", _load_config)

    config._load_settings()

    assert load_calls
    assert len(load_calls) == len(set(load_calls))


def test_parent_process_still_persists_environment(monkeypatch) -> None:
    """Skipping helper writes must not change normal application bootstrap."""
    monkeypatch.delenv("SHELFMARK_INTERNAL_BYPASSER_CHILD", raising=False)
    config, registry = _fresh_config(monkeypatch)
    sync_calls: list[bool] = []

    monkeypatch.setattr(registry, "sync_env_to_config", lambda: sync_calls.append(True))
    monkeypatch.setattr(registry, "get_settings_field_map", lambda: {})

    config._load_settings()

    assert sync_calls == [True]

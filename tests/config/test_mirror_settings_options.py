def test_aa_base_url_options_include_configured_custom_url(monkeypatch):
    import shelfmark.config.settings as settings
    from shelfmark.core.config import config as config_obj

    # Use a custom mirror not present in defaults/additional.
    monkeypatch.setenv("AA_BASE_URL", "https://custom-aa.example")
    config_obj.refresh()

    options = settings._get_aa_base_url_options()
    assert any(opt["value"] == "https://custom-aa.example" for opt in options)


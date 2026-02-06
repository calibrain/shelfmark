from shelfmark.core import mirrors


class _DummyConfig:
    def __init__(self, values: dict):
        self._values = values

    def get(self, key: str, default=None):
        return self._values.get(key, default)


def test_get_aa_mirrors_prefers_full_configured_list(monkeypatch):
    dummy = _DummyConfig(
        {
            "AA_MIRROR_URLS": ["annas-archive.gl/", "https://annas-archive.li"],
            "AA_ADDITIONAL_URLS": "https://should-not-be-appended.example",
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    assert mirrors.get_aa_mirrors() == [
        "https://annas-archive.gl",
        "https://annas-archive.li",
    ]


def test_get_aa_mirrors_falls_back_to_defaults_and_legacy_additional(monkeypatch):
    dummy = _DummyConfig(
        {
            "AA_MIRROR_URLS": [],
            "AA_ADDITIONAL_URLS": "extra.example, https://extra2.example/",
        }
    )
    monkeypatch.setattr(mirrors, "_get_config", lambda: dummy)

    aa = mirrors.get_aa_mirrors()
    assert "https://annas-archive.gl" in aa
    assert "https://annas-archive.li" in aa
    assert "https://extra.example" in aa
    assert "https://extra2.example" in aa


def test_bypass_tries_all_methods_before_abort(monkeypatch):
    """Regression test for issue #524: don't abort before cycling through bypass methods."""
    import shelfmark.bypass.internal_bypasser as internal_bypasser

    calls: list[str] = []

    def _make_method(name: str):
        def _method(_sb) -> bool:
            calls.append(name)
            return False

        _method.__name__ = name
        return _method

    methods = [_make_method(f"m{i}") for i in range(6)]

    monkeypatch.setattr(internal_bypasser, "BYPASS_METHODS", methods)
    monkeypatch.setattr(internal_bypasser, "_is_bypassed", lambda _sb, escape_emojis=True: False)
    monkeypatch.setattr(internal_bypasser, "_detect_challenge_type", lambda _sb: "ddos_guard")
    monkeypatch.setattr(internal_bypasser.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(internal_bypasser.random, "uniform", lambda _a, _b: 0)

    assert internal_bypasser._bypass(object(), max_retries=10) is False
    assert calls == [f"m{i}" for i in range(6)]

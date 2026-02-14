"""Tests for request-policy resolution."""

from shelfmark.core.request_policy import (
    PolicyMode,
    filter_request_policy_settings,
    get_source_content_type_capabilities,
    merge_request_policy_settings,
    normalize_content_type,
    resolve_policy_mode,
    validate_policy_rules,
)


def test_filter_request_policy_settings_uses_uppercase_allowlist_only():
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "request_book",
        "REQUEST_POLICY_RULES": [{"source": "*", "content_type": "*", "mode": "blocked"}],
        "REQUESTS_ENABLED": True,
        "REQUESTS_ALLOW_NOTES": False,
        "MAX_PENDING_REQUESTS_PER_USER": 7,
        "request_policy_default_ebook": "blocked",
        "DESTINATION": "/books/alice",
    }

    filtered = filter_request_policy_settings(settings)

    assert "REQUEST_POLICY_DEFAULT_EBOOK" in filtered
    assert "REQUEST_POLICY_DEFAULT_AUDIOBOOK" in filtered
    assert "REQUEST_POLICY_RULES" in filtered
    assert "REQUESTS_ENABLED" in filtered
    assert "REQUESTS_ALLOW_NOTES" in filtered
    assert "MAX_PENDING_REQUESTS_PER_USER" in filtered
    assert "request_policy_default_ebook" not in filtered
    assert "DESTINATION" not in filtered


def test_merge_request_policy_settings_applies_user_overrides():
    global_settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "download",
        "REQUEST_POLICY_RULES": [{"source": "*", "content_type": "*", "mode": "request_book"}],
    }
    user_settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "blocked",
        "DESTINATION": "/books/alice",
    }

    merged = merge_request_policy_settings(global_settings, user_settings)

    assert merged["REQUEST_POLICY_DEFAULT_EBOOK"] == "blocked"
    assert merged["REQUEST_POLICY_DEFAULT_AUDIOBOOK"] == "download"
    assert "DESTINATION" not in merged


def test_merge_request_policy_settings_overlays_user_rules_on_global_rules():
    global_settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "download",
        "REQUEST_POLICY_RULES": [
            {"source": "direct_download", "content_type": "ebook", "mode": "download"},
            {"source": "prowlarr", "content_type": "ebook", "mode": "request_release"},
        ],
    }
    user_settings = {
        "REQUEST_POLICY_RULES": [
            {"source": "direct_download", "content_type": "ebook", "mode": "blocked"},
        ],
    }

    merged = merge_request_policy_settings(global_settings, user_settings)

    assert sorted(merged["REQUEST_POLICY_RULES"], key=lambda row: (row["source"], row["content_type"])) == [
        {"source": "direct_download", "content_type": "ebook", "mode": "blocked"},
        {"source": "prowlarr", "content_type": "ebook", "mode": "request_release"},
    ]


def test_merge_request_policy_settings_empty_user_rules_preserve_global_rules():
    global_settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_RULES": [
            {"source": "prowlarr", "content_type": "ebook", "mode": "request_release"},
        ],
    }
    user_settings = {
        "REQUEST_POLICY_RULES": [],
    }

    merged = merge_request_policy_settings(global_settings, user_settings)

    assert merged["REQUEST_POLICY_RULES"] == [
        {"source": "prowlarr", "content_type": "ebook", "mode": "request_release"},
    ]


def test_normalize_content_type_defaults_to_ebook_for_unknown_values():
    assert normalize_content_type(None) == "ebook"
    assert normalize_content_type("") == "ebook"
    assert normalize_content_type("book (fiction)") == "ebook"
    assert normalize_content_type("mystery-value") == "ebook"


def test_normalize_content_type_detects_audiobook_aliases():
    assert normalize_content_type("audiobook") == "audiobook"
    assert normalize_content_type("AUDIOBOOKS") == "audiobook"
    assert normalize_content_type("book (audiobook)") == "audiobook"


def test_resolve_policy_mode_uses_wildcard_precedence():
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "download",
        "REQUEST_POLICY_RULES": [
            {"source": "*", "content_type": "*", "mode": "blocked"},
            {"source": "*", "content_type": "ebook", "mode": "request_release"},
            {"source": "prowlarr", "content_type": "*", "mode": "request_release"},
            {"source": "prowlarr", "content_type": "ebook", "mode": "download"},
        ],
    }

    # (prowlarr, ebook) exact match → download
    assert resolve_policy_mode(
        source="prowlarr",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.DOWNLOAD
    # (prowlarr, audiobook) → matches (prowlarr, *) → request_release
    assert resolve_policy_mode(
        source="prowlarr",
        content_type="audiobook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_RELEASE
    # (irc, ebook) → matches (*, ebook) → request_release
    assert resolve_policy_mode(
        source="irc",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_RELEASE
    # (irc, audiobook) → matches (*, *) → blocked
    assert resolve_policy_mode(
        source="irc",
        content_type="audiobook",
        global_settings=settings,
    ) == PolicyMode.BLOCKED


def test_resolve_policy_mode_uses_content_default_when_no_rule_matches():
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "blocked",
        "REQUEST_POLICY_RULES": [],
    }

    assert resolve_policy_mode(
        source="direct_download",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.DOWNLOAD
    assert resolve_policy_mode(
        source="direct_download",
        content_type="audiobook",
        global_settings=settings,
    ) == PolicyMode.BLOCKED


def test_resolve_policy_mode_caps_at_content_type_default_ceiling():
    """Matrix rules cannot grant more permissive access than the content-type default."""
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "request_release",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "request_release",
        "REQUEST_POLICY_RULES": [
            {"source": "prowlarr", "content_type": "ebook", "mode": "download"},
            {"source": "irc", "content_type": "ebook", "mode": "blocked"},
        ],
    }

    # prowlarr/ebook rule says download, but ceiling is request_release → capped
    assert resolve_policy_mode(
        source="prowlarr",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_RELEASE
    # irc/ebook rule says blocked, which is more restrictive than ceiling → stays blocked
    assert resolve_policy_mode(
        source="irc",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.BLOCKED
    # no rule for direct_download → falls to ceiling
    assert resolve_policy_mode(
        source="direct_download",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_RELEASE


def test_resolve_policy_mode_request_book_ceiling_overrides_all_rules():
    """When default is request_book, no matrix rule can open the release modal."""
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "request_book",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": "blocked",
        "REQUEST_POLICY_RULES": [
            {"source": "prowlarr", "content_type": "ebook", "mode": "download"},
            {"source": "direct_download", "content_type": "ebook", "mode": "request_release"},
        ],
    }

    # Both rules try to upgrade beyond request_book → capped
    assert resolve_policy_mode(
        source="prowlarr",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_BOOK
    assert resolve_policy_mode(
        source="direct_download",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_BOOK
    # audiobook default is blocked → even more restrictive ceiling
    assert resolve_policy_mode(
        source="prowlarr",
        content_type="audiobook",
        global_settings=settings,
    ) == PolicyMode.BLOCKED


def test_resolve_policy_mode_falls_back_to_request_book_when_unset():
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "not-a-mode",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": None,
        "REQUEST_POLICY_RULES": [],
    }

    assert resolve_policy_mode(
        source="direct_download",
        content_type="ebook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_BOOK
    assert resolve_policy_mode(
        source="prowlarr",
        content_type="audiobook",
        global_settings=settings,
    ) == PolicyMode.REQUEST_BOOK


def test_resolve_policy_mode_ignores_invalid_rule_rows():
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_RULES": [
            {"source": "direct_download", "content_type": "ebook", "mode": "request_book"},
            {"source": "direct_download", "content_type": "ebook", "mode": "not-valid"},
            {"source": "direct_download", "content_type": "invalid-type", "mode": "blocked"},
        ],
    }

    resolved = resolve_policy_mode(
        source="direct_download",
        content_type="ebook",
        global_settings=settings,
    )

    assert resolved == PolicyMode.DOWNLOAD


def test_resolve_policy_mode_uses_user_rules_when_present():
    global_settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_RULES": [
            {"source": "direct_download", "content_type": "ebook", "mode": "download"},
        ],
    }
    user_settings = {
        "REQUEST_POLICY_RULES": [
            {"source": "direct_download", "content_type": "ebook", "mode": "blocked"},
        ],
    }

    resolved = resolve_policy_mode(
        source="direct_download",
        content_type="ebook",
        global_settings=global_settings,
        user_settings=user_settings,
    )

    assert resolved == PolicyMode.BLOCKED


def test_resolve_policy_mode_treats_unknown_source_as_wildcard_context():
    settings = {
        "REQUEST_POLICY_DEFAULT_EBOOK": "download",
        "REQUEST_POLICY_RULES": [
            {"source": "*", "content_type": "ebook", "mode": "request_release"},
        ],
    }

    resolved = resolve_policy_mode(
        source=None,
        content_type="ebook",
        global_settings=settings,
    )

    assert resolved == PolicyMode.REQUEST_RELEASE


def test_validate_policy_rules_rejects_unknown_source():
    rules = [
        {"source": "not-a-source", "content_type": "ebook", "mode": "download"},
    ]
    normalized, errors = validate_policy_rules(
        rules,
        source_capabilities={
            "direct_download": {"ebook"},
            "prowlarr": {"ebook", "audiobook"},
        },
    )

    assert normalized == []
    assert "unknown source" in errors[0]


def test_validate_policy_rules_rejects_blank_source():
    rules = [
        {"source": "", "content_type": "ebook", "mode": "download"},
    ]
    normalized, errors = validate_policy_rules(
        rules,
        source_capabilities={
            "direct_download": {"ebook"},
        },
    )

    assert normalized == []
    assert "source is required" in errors[0]


def test_validate_policy_rules_rejects_unsupported_source_content_type_pair():
    rules = [
        {"source": "direct_download", "content_type": "audiobook", "mode": "download"},
    ]
    normalized, errors = validate_policy_rules(
        rules,
        source_capabilities={
            "direct_download": {"ebook"},
            "prowlarr": {"ebook", "audiobook"},
        },
    )

    assert normalized == []
    assert "does not support content_type 'audiobook'" in errors[0]


def test_validate_policy_rules_rejects_request_book_in_matrix():
    rules = [
        {"source": "direct_download", "content_type": "ebook", "mode": "request_book"},
    ]
    normalized, errors = validate_policy_rules(
        rules,
        source_capabilities={"direct_download": {"ebook"}},
    )

    assert normalized == []
    assert "not allowed in matrix rules" in errors[0]


def test_validate_policy_rules_accepts_supported_pairs_and_wildcards():
    rules = [
        {"source": "prowlarr", "content_type": "audiobook", "mode": "request_release"},
        {"source": "direct_download", "content_type": "*", "mode": "blocked"},
        {"source": "*", "content_type": "ebook", "mode": "download"},
    ]
    normalized, errors = validate_policy_rules(
        rules,
        source_capabilities={
            "direct_download": {"ebook"},
            "prowlarr": {"ebook", "audiobook"},
            "irc": {"ebook"},
        },
    )

    assert errors == []
    assert normalized == [
        {"source": "prowlarr", "content_type": "audiobook", "mode": "request_release"},
        {"source": "direct_download", "content_type": "*", "mode": "blocked"},
        {"source": "*", "content_type": "ebook", "mode": "download"},
    ]


def test_get_source_content_type_capabilities_reads_registry(monkeypatch):
    monkeypatch.setattr(
        "shelfmark.release_sources.list_available_sources",
        lambda: [
            {
                "name": "direct_download",
                "display_name": "Direct Download",
                "enabled": True,
                "supported_content_types": ["ebook"],
            },
            {
                "name": "prowlarr",
                "display_name": "Prowlarr",
                "enabled": True,
                "supported_content_types": ["ebook", "audiobook"],
            },
        ],
    )

    capabilities = get_source_content_type_capabilities()

    assert capabilities["direct_download"] == {"ebook"}
    assert capabilities["prowlarr"] == {"ebook", "audiobook"}

"""Tests for the {Language} template variable.

Different-language editions of one book resolve to the same title, so without a
language token they render to the same path and land in one folder. Audiobookshelf
treats a folder as exactly one library item, so the two editions become a single
book with both files as tracks (calibrain/shelfmark#1138).
"""

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.core.naming import (
    KNOWN_TOKENS,
    normalize_language_code,
    parse_naming_template,
)
from shelfmark.download.orchestrator import (
    _restore_task_from_retry_payload,
    serialize_task_for_retry,
)
from shelfmark.download.postprocess.transfer import build_metadata_dict


class TestLanguageInKnownTokens:
    def test_language_in_known_tokens(self):
        assert "language" in KNOWN_TOKENS

    def test_language_token_parsed(self):
        assert parse_naming_template("{Language}", {"Language": "sv"}) == "sv"

    def test_language_token_case_insensitive(self):
        assert parse_naming_template("{language}", {"Language": "sv"}) == "sv"


class TestKnownTokensOrdering:
    """find_placeholder() does a substring find over KNOWN_TOKENS in list order.

    Nothing else guards this contract, so a future token added in the wrong
    position would silently shadow an existing one.
    """

    def test_tokens_are_ordered_longest_first(self):
        lengths = [len(token) for token in KNOWN_TOKENS]
        assert lengths == sorted(lengths, reverse=True)

    def test_no_token_is_shadowed_by_an_earlier_substring(self):
        for shorter_index, shorter in enumerate(KNOWN_TOKENS):
            for longer_index, longer in enumerate(KNOWN_TOKENS):
                if shorter is longer or shorter not in longer:
                    continue
                assert shorter_index > longer_index, (
                    f"{shorter!r} precedes {longer!r} and would shadow it"
                )


class TestLanguageTemplateSubstitution:
    """The acceptance cases from the issue."""

    TEMPLATE = "{Author}/{Title}{ (Language)}/{Author} - {Title}"
    BASE = {"Author": "Andy Weir", "Title": "Project Hail Mary"}

    def test_translated_edition_gets_its_own_folder(self):
        result = parse_naming_template(
            self.TEMPLATE, {**self.BASE, "Language": "sv"}, allow_path_separators=True
        )
        assert result == "Andy Weir/Project Hail Mary (sv)/Andy Weir - Project Hail Mary"

    def test_untagged_edition_is_unchanged(self):
        result = parse_naming_template(
            self.TEMPLATE, {**self.BASE, "Language": None}, allow_path_separators=True
        )
        assert result == "Andy Weir/Project Hail Mary/Andy Weir - Project Hail Mary"

    def test_the_two_editions_do_not_collide(self):
        english = parse_naming_template(
            self.TEMPLATE, {**self.BASE, "Language": None}, allow_path_separators=True
        )
        swedish = parse_naming_template(
            self.TEMPLATE, {**self.BASE, "Language": "sv"}, allow_path_separators=True
        )
        assert english != swedish

    def test_language_as_a_leading_folder(self):
        result = parse_naming_template(
            "{Language/}{Author}/{Title}",
            {**self.BASE, "Language": "sv"},
            allow_path_separators=True,
        )
        assert result == "sv/Andy Weir/Project Hail Mary"

    def test_language_in_a_filename_template(self):
        result = parse_naming_template(
            "{Author} - {Title}{ (Language)}", {**self.BASE, "Language": "sv"}
        )
        assert result == "Andy Weir - Project Hail Mary (sv)"

    def test_language_is_sanitized(self):
        result = parse_naming_template("{Title}{ (Language)}", {"Title": "Book", "Language": "s/v"})
        assert "/" not in result


class TestNormalizeLanguageCode:
    def test_lowercases(self):
        assert normalize_language_code("EN") == "en"
        assert normalize_language_code("Sv") == "sv"

    def test_strips_whitespace(self):
        assert normalize_language_code("  sv  ") == "sv"

    def test_placeholders_render_empty(self):
        for placeholder in ("unknown", "unk", "n/a", "na", "-", "--", "none", "null", ""):
            assert normalize_language_code(placeholder) == "", placeholder

    def test_placeholders_are_matched_case_insensitively(self):
        assert normalize_language_code("Unknown") == ""

    def test_none_renders_empty(self):
        assert normalize_language_code(None) == ""


class TestBuildMetadataWithLanguage:
    def test_language_reaches_the_template_metadata(self):
        task = DownloadTask(task_id="t", source="prowlarr", title="Book", language="sv")
        assert build_metadata_dict(task)["Language"] == "sv"

    def test_language_is_normalized_on_the_way_out(self):
        task = DownloadTask(task_id="t", source="prowlarr", title="Book", language="SV")
        assert build_metadata_dict(task)["Language"] == "sv"

    def test_placeholder_language_does_not_reach_the_path(self):
        # Anna's Archive reports the literal string "unknown" when it cannot tell.
        task = DownloadTask(task_id="t", source="direct", title="Book", language="unknown")
        assert build_metadata_dict(task)["Language"] == ""

    def test_missing_language_renders_empty(self):
        task = DownloadTask(task_id="t", source="prowlarr", title="Book")
        assert build_metadata_dict(task)["Language"] == ""


class TestLanguageSurvivesRetry:
    """DownloadTask is not rebuilt from dataclasses.fields(), so each of the
    three orchestrator sites has to carry the field explicitly."""

    def test_roundtrip_preserves_language(self):
        task = DownloadTask(task_id="t", source="prowlarr", title="Book", language="sv")
        restored = _restore_task_from_retry_payload(serialize_task_for_retry(task))
        assert restored is not None
        assert restored.language == "sv"

    def test_legacy_payload_without_language_restores_cleanly(self):
        task = DownloadTask(task_id="t", source="prowlarr", title="Book", language="sv")
        payload = serialize_task_for_retry(task)
        del payload["language"]

        restored = _restore_task_from_retry_payload(payload)

        assert restored is not None
        assert restored.language is None


class TestEverySpellingCollapsesToOneFolder:
    """Sources report the same language differently; if the token rendered each
    spelling verbatim they would land in separate folders, which is the exact
    collision this token exists to prevent (reported on PR #1142)."""

    TEMPLATE = "{Author}/{Title}{ (Language)}/{Title}"

    def _folder(self, language):
        task = DownloadTask(
            task_id="t", source="prowlarr", title="Dune", author="Frank Herbert", language=language
        )
        return parse_naming_template(
            self.TEMPLATE, build_metadata_dict(task), allow_path_separators=True
        )

    @pytest.mark.parametrize(
        "spellings",
        [
            ("en", "eng", "English", "english", "ENG", "  Eng  "),
            ("sv", "swe", "Swedish"),
            ("de", "ger", "deu", "German"),
            ("ml", "mal", "Malayalam"),
            ("fa", "per", "fas", "Farsi", "Persian"),
        ],
    )
    def test_all_spellings_of_a_language_share_one_folder(self, spellings):
        rendered = {self._folder(spelling) for spelling in spellings}
        assert len(rendered) == 1, f"{spellings} produced {sorted(rendered)}"

    def test_a_language_we_cannot_resolve_is_kept_rather_than_dropped(self):
        # It still separates editions, and cannot collide with a resolved code
        # precisely because nothing resolves to it.
        assert "klingon" in self._folder("Klingon")

    def test_different_languages_still_get_different_folders(self):
        assert self._folder("English") != self._folder("Swedish")

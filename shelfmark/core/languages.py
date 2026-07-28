"""Canonical language resolution shared by every release source.

Release sources report a language in whatever shape their upstream uses: a
two-letter code, an ISO 639-2 three-letter code in either the bibliographic or
terminological form, or an English name. They all need the same ISO 639-1 code
out the other side, so the aliases live in one place (``data/book-languages.json``)
and adding a language means editing one file.
"""

import json
import threading
import unicodedata
from pathlib import Path

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

LANGUAGE_DATA_PATH = Path(__file__).resolve().parents[1].parent / "data" / "book-languages.json"

# Values a source uses to mean "we could not tell".
LANGUAGE_PLACEHOLDERS = frozenset({"", "-", "--", "unknown", "unk", "n/a", "na", "none", "null"})

_ALIAS_TO_CODE: dict[str, str] | None = None
_CODE_TO_NAME: dict[str, str] | None = None
_LOCK = threading.Lock()


# Separators that stand in for the hyphen in a subtag. The dashes turn up in
# codes copied from web pages -- "zh‑Hant" used U+2011, which renders close
# enough to both a hyphen and an underscore to go unnoticed -- and the
# underscore is the spelling Direct Download accepted before this module existed.
_SUBTAG_SEPARATORS = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－_"), "-")


def _fold(value: str) -> str:
    """Casefold, strip accents, and normalize subtag separators, so 'Español'
    and 'espanol', or 'zh-Hant', 'zh‑Hant' and 'zh_Hant', all match."""
    decomposed = unicodedata.normalize("NFKD", value).translate(_SUBTAG_SEPARATORS)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.split()).casefold()


def _load() -> tuple[dict[str, str], dict[str, str]]:
    global _ALIAS_TO_CODE, _CODE_TO_NAME

    if _ALIAS_TO_CODE is not None and _CODE_TO_NAME is not None:
        return _ALIAS_TO_CODE, _CODE_TO_NAME

    with _LOCK:
        if _ALIAS_TO_CODE is not None and _CODE_TO_NAME is not None:
            return _ALIAS_TO_CODE, _CODE_TO_NAME

        alias_to_code: dict[str, str] = {}
        code_to_name: dict[str, str] = {}

        try:
            raw = json.loads(LANGUAGE_DATA_PATH.read_text(encoding="utf-8"))
        except OSError, ValueError:
            logger.exception("Failed to load language data from %s", LANGUAGE_DATA_PATH)
            raw = []

        if not isinstance(raw, list):
            logger.warning("Language data at %s is not a list", LANGUAGE_DATA_PATH)
            raw = []

        for item in raw:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("language") or "").strip()
            if not code:
                continue

            code_to_name.setdefault(code, name or code)

            for candidate in (code, name, *(item.get("aliases") or [])):
                folded = _fold(str(candidate))
                if folded and folded not in LANGUAGE_PLACEHOLDERS:
                    alias_to_code.setdefault(folded, code)

        _ALIAS_TO_CODE = alias_to_code
        _CODE_TO_NAME = code_to_name
        return alias_to_code, code_to_name


def normalize_language(value: object) -> str | None:
    """Resolve any known spelling of a language to its ISO 639-1 code.

    Accepts a two-letter code, an ISO 639-2 three-letter code in either the
    bibliographic or terminological form, or an English name. Returns None for
    anything unrecognised or for the placeholders a source uses to say it does
    not know, so callers can treat "no language" uniformly.
    """
    if value is None:
        return None

    folded = _fold(str(value))
    if not folded or folded in LANGUAGE_PLACEHOLDERS:
        return None

    alias_to_code, _ = _load()
    return alias_to_code.get(folded)


def language_name(code: str | None) -> str | None:
    """Return the English name for a language code, or None if unknown."""
    if not code:
        return None

    _, code_to_name = _load()
    return code_to_name.get(str(code).strip())


def language_alias_map() -> dict[str, str]:
    """Every known alias mapped to its code, for callers doing their own matching.

    Direct Download scans free-text paths and needs the whole alias set up front
    to look for, rather than resolving one candidate at a time.
    """
    alias_to_code, _ = _load()
    return dict(alias_to_code)


def supported_book_languages() -> list[dict[str, str]]:
    """The selectable languages, as ``{"language": ..., "code": ...}``.

    Aliases are an implementation detail of resolution, so they are left out of
    what the settings dropdown and the API hand to clients.
    """
    _, code_to_name = _load()
    return [{"language": name, "code": code} for code, name in code_to_name.items()]


def known_language_codes() -> frozenset[str]:
    """Every ISO 639-1 code the bundled language data defines."""
    _, code_to_name = _load()
    return frozenset(code_to_name)

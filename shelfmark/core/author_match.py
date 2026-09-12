"""Comparing and trimming author names for release search.

Lives in core because more than one release source needs it: Prowlarr ranks
results on author agreement (#1293), and IRC both trims the name it searches
for and ranks what comes back.
"""

import re

_AUTHOR_TOKEN_PATTERN = re.compile(r"\w+", re.UNICODE)
_AUTHOR_NOISE_TOKENS = frozenset(
    {"jr", "sr", "ii", "iii", "iv", "phd", "md", "dr", "mr", "mrs", "ms", "et", "al", "and", "the"}
)

# Ordering tiers for author agreement between the requested book and what an
# indexer reported. Lower sorts first.
AUTHOR_MATCH = 0
AUTHOR_PARTIAL = 1
AUTHOR_UNKNOWN = 2
AUTHOR_MISMATCH = 3

# A mononym ("Homer") can only ever agree on one token; a longer name needs a
# given name and a surname to agree before it counts as the same person.
_AUTHOR_TOKENS_REQUIRED = 2


def _author_tokens(value: object) -> list[str]:
    """Split an author string into comparable lowercase name tokens."""
    if not isinstance(value, str):
        return []
    tokens = [token.lower() for token in _AUTHOR_TOKEN_PATTERN.findall(value)]
    return [token for token in tokens if token not in _AUTHOR_NOISE_TOKENS]


def _author_tokens_compatible(wanted: str, offered: str) -> bool:
    """Treat an abbreviated given name as the name it abbreviates."""
    return wanted == offered or wanted.startswith(offered) or offered.startswith(wanted)


def author_affinity(wanted: object, offered: object) -> int:
    """Rank how far an indexer's author field is from the requested author.

    Shelfmark ranks on this rather than filtering on it, so a wrong verdict only
    costs a release its position in the list, never its visibility. That is what
    makes the loose token comparison safe: "Tim"/"Timothy" and "T."/"Timothy"
    agree, while a transliteration ("Dostoevsky"/"Dostoyevsky") is merely sorted
    last instead of being hidden.

    Graded, not binary, because the ways of falling short are not equally bad.
    An indexer that reports no author at all must not sort below one that reports
    a wrong author, so "no metadata" ranks between agreement and disagreement. And
    a name that merely says *less* than the one asked for is not evidence of a
    different person: "Petrie" contradicts nothing about "David Petrie", while
    "Gordon Petrie" does. That gap matters most where a source is searched by
    surname alone (#1331) - the filenames such a search is meant to reach are
    exactly the ones filed under a bare surname, and ranking them as wrong put
    them below every result that named someone else entirely.
    """
    wanted_tokens = _author_tokens(wanted)
    offered_tokens = _author_tokens(offered)
    if not wanted_tokens or not offered_tokens:
        return AUTHOR_UNKNOWN

    matched = sum(
        1
        for wanted_token in wanted_tokens
        if any(
            _author_tokens_compatible(wanted_token, offered_token)
            for offered_token in offered_tokens
        )
    )
    required = min(_AUTHOR_TOKENS_REQUIRED, len(wanted_tokens))
    if matched >= required:
        return AUTHOR_MATCH

    # Too little agreement to call it the same person, so the question is whether
    # what was offered *disagrees*. A name every token of which fits the wanted
    # name is an abbreviation of it; one carrying a token that fits nothing is a
    # different name that happens to share a surname.
    if all(
        any(
            _author_tokens_compatible(wanted_token, offered_token) for wanted_token in wanted_tokens
        )
        for offered_token in offered_tokens
    ):
        return AUTHOR_PARTIAL

    return AUTHOR_MISMATCH


def search_surname(author: object) -> str:
    """The one name token worth sending to a source that matches conjunctively.

    Given names are where catalogues disagree - "David Petrie" is filed as
    "D. Petrie", "Timothy" as "Tim" - so a query carrying one matches nothing on
    a source that requires every term to appear. The surname is the token both
    spellings share.

    Keeps the author's own capitalisation, because the result is posted to a
    public channel, and returns "" when no usable token is left so the caller
    searches by title alone rather than by noise.
    """
    if not isinstance(author, str):
        return ""
    tokens = [
        token
        for token in _AUTHOR_TOKEN_PATTERN.findall(author)
        if token.lower() not in _AUTHOR_NOISE_TOKENS
    ]
    if not tokens:
        return ""
    return tokens[-1]

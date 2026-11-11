"""Known-answer tests for the Chomikuj mobile API request token.

The formula and vectors were verified against the mobile app (``scripts/live-spike.mjs``
and ``tests/token.test.mjs``). Hardcoding the digests keeps any regression in the
signing constant, separator, path normalization, or UTF-8 handling visible.
"""

import hashlib
import re

import pytest

from shelfmark.release_sources.chomikuj.token import CHOMIKUJ_SIGNING_CONSTANT, sign_token

MD5_HEX = re.compile(r"^[0-9a-f]{32}$")

VECTORS = [
    (
        "account/info with no query and no body",
        "api/v3/account/info",
        "",
        "",
        "22dbb80e516477793934fa0add1b8929",
    ),
    (
        "files/search with the exact query string",
        "api/v3/files/search",
        "Query=test&PageNumber=1&MediaType=Documents&Extension=pdf",
        "",
        "83b420ec92a611e58b884b89e68ac530",
    ),
    (
        "files/download with a FileId query",
        "api/v3/files/download",
        "FileId=123456",
        "",
        "bfdebdece4cef4a17ebe44808ab4cda4",
    ),
    (
        "account/login with a compact JSON body",
        "api/v3/account/login",
        "",
        '{"AccountName":"alice","Password":"p@ss"}',
        "9f78082445ecf973cb41180065a0fc50",
    ),
    (
        "account/login with a Unicode body",
        "api/v3/account/login",
        "",
        '{"AccountName":"żółć","Password":"päss"}',
        "801e16c63c58dede556adfa87c78476e",
    ),
]


def test_signing_constant_is_the_exact_mobile_app_value():
    """Test signing constant is the exact mobile app value."""
    assert CHOMIKUJ_SIGNING_CONSTANT == "wzrwYua$.DSe8suk!`'2"
    assert len(CHOMIKUJ_SIGNING_CONSTANT) == 20


def test_token_vectors():
    """Test token vectors."""
    for name, path, query, body, expected in VECTORS:
        actual = sign_token(path, query, body)
        assert actual == expected, f"sign_token: {name}"
        assert MD5_HEX.match(actual), f"sign_token output is lowercase MD5 hex: {name}"


def test_leading_slashes_are_stripped_not_signed():
    """Test leading slashes are stripped not signed."""
    expected = VECTORS[0][4]
    assert sign_token("/api/v3/account/info") == expected
    assert sign_token("//api/v3/account/info") == expected
    assert sign_token("///api/v3/account/info") == expected


def test_empty_query_and_body_match_defaults():
    """Test empty query and body match defaults."""
    expected = VECTORS[0][4]
    assert sign_token("api/v3/account/info") == expected
    assert sign_token("api/v3/account/info", "", "") == expected


def test_trailing_slash_changes_the_digest():
    """Test trailing slash changes the digest."""
    trailing = sign_token("api/v3/account/info/")
    assert trailing == "fd75d7d488c54390b51a5bc0a54700a0"
    assert trailing != VECTORS[0][4]


def test_query_is_signed_verbatim():
    """A reordered query must produce a different digest: no sorting/normalizing."""
    ordered = "Query=test&PageNumber=1&MediaType=Documents&Extension=pdf"
    reordered = "PageNumber=1&Query=test&MediaType=Documents&Extension=pdf"

    ordered_digest = sign_token("api/v3/files/search", ordered)
    reordered_digest = sign_token("api/v3/files/search", reordered)

    assert ordered_digest == "83b420ec92a611e58b884b89e68ac530"
    assert reordered_digest != ordered_digest
    assert reordered_digest != VECTORS[0][4]

    # Independent oracle: the digest is MD5 over the exact string, no re-encoding.
    raw_query = "Query=a%20b+c&PageNumber=2"
    expected = hashlib.md5(
        f"api/v3/files/search?{raw_query}{CHOMIKUJ_SIGNING_CONSTANT}".encode(),
        usedforsecurity=False,
    ).hexdigest()
    assert sign_token("api/v3/files/search", raw_query) == expected


@pytest.mark.parametrize(
    ("path", "query", "body", "expected"),
    [(path, query, body, expected) for _, path, query, body, expected in VECTORS],
)
def test_wrong_signing_constant_differs_from_every_vector(path, query, body, expected):
    """Test wrong signing constant differs from every vector."""
    raw = path.lstrip("/")
    if query:
        raw = f"{raw}?{query}"
    wrong = hashlib.md5(
        f"{raw}{body}wzrwYua$.DSe8suk!`'3".encode(), usedforsecurity=False
    ).hexdigest()
    assert wrong != expected
    assert sign_token(path, query, body) != wrong

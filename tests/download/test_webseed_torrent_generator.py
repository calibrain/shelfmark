"""Validate the e2e platform's webseed .torrent generator.

The ``full`` e2e profile relies on a tracker-less webseed torrent so a real
qBittorrent can complete a real download from the mock origin over HTTP. If the
generator emits malformed bencode or mismatched piece hashes, qBittorrent would
silently never complete — so we cross-check the generator against shelfmark's own
``bencode_decode`` / ``extract_info_hash_from_torrent`` here, in normal CI.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from shelfmark.download.clients.torrent_utils import (
    bencode_decode,
    extract_info_hash_from_torrent,
)

GEN_PATH = (
    Path(__file__).resolve().parents[1] / "e2e" / "platform" / "mocks" / "make_webseed_torrent.py"
)


def _load_generator():
    if not GEN_PATH.exists():
        pytest.skip(f"generator not found at {GEN_PATH}")
    spec = importlib.util.spec_from_file_location("make_webseed_torrent", GEN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PAYLOAD = b"E2E webseed payload \x00\x01\x02 " * 2000  # ~50 KiB -> multiple pieces
NAME = "sample-book.epub"
WEBSEED = "http://mock-aa/payload/sample-book.epub"


def test_generated_torrent_decodes_with_shelfmark_bencode() -> None:
    gen = _load_generator()
    raw = gen.build_webseed_torrent(NAME, PAYLOAD, WEBSEED, piece_length=16384)

    decoded, _ = bencode_decode(raw)
    assert isinstance(decoded, dict)
    info = decoded[b"info"]
    assert info[b"name"] == NAME.encode()
    assert info[b"length"] == len(PAYLOAD)
    # url-list (webseed) must point at the file the mock serves.
    assert decoded[b"url-list"] == [WEBSEED.encode()]
    # No tracker — the whole point is HTTP-only completion.
    assert b"announce" not in decoded


def test_piece_hashes_match_payload_bytes() -> None:
    gen = _load_generator()
    piece_len = 16384
    raw = gen.build_webseed_torrent(NAME, PAYLOAD, WEBSEED, piece_length=piece_len)
    decoded, _ = bencode_decode(raw)
    pieces = decoded[b"info"][b"pieces"]

    expected = b"".join(
        hashlib.sha1(PAYLOAD[i : i + piece_len]).digest() for i in range(0, len(PAYLOAD), piece_len)
    )
    assert pieces == expected, "piece hashes do not match payload — qbit would never complete"
    assert len(pieces) % 20 == 0


def test_info_hash_matches_shelfmark_extractor() -> None:
    """Our infohash helper must agree with shelfmark's torrent parser."""
    gen = _load_generator()
    raw = gen.build_webseed_torrent(NAME, PAYLOAD, WEBSEED)

    ours = gen.info_hash(raw)
    theirs = extract_info_hash_from_torrent(raw)
    assert theirs is not None
    assert ours.lower() == theirs.lower(), (ours, theirs)


def test_generator_is_deterministic() -> None:
    gen = _load_generator()
    a = gen.build_webseed_torrent(NAME, PAYLOAD, WEBSEED)
    b = gen.build_webseed_torrent(NAME, PAYLOAD, WEBSEED)
    assert a == b, "torrent generation must be byte-deterministic for stable infohash"

"""Generate a single-file BitTorrent metainfo (.torrent) with a BEP-19 webseed.

Used by the e2e platform's ``full`` profile so a *real* torrent client
(qBittorrent) can complete a *real* download hermetically: the torrent carries no
tracker and a single ``url-list`` webseed pointing at the mock origin's HTTP file
endpoint, so libtorrent fetches the payload over HTTP — no tracker, peer, or
seeder container required.

Stdlib-only (the mock image does not install shelfmark). The unit test cross-checks
this encoder against shelfmark's own ``bencode_decode`` /
``extract_info_hash_from_torrent`` so a divergence in either is caught.
"""

from __future__ import annotations

import hashlib

DEFAULT_PIECE_LENGTH = 16384  # 16 KiB — fine for the tiny e2e payload


def bencode(value: object) -> bytes:
    """Minimal bencode encoder (int / bytes / str / list / dict)."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise TypeError("bool is not bencodable")
    if isinstance(value, int):
        return b"i" + str(value).encode() + b"e"
    if isinstance(value, bytes):
        return str(len(value)).encode() + b":" + value
    if isinstance(value, str):
        return bencode(value.encode("utf-8"))
    if isinstance(value, list):
        return b"l" + b"".join(bencode(item) for item in value) + b"e"
    if isinstance(value, dict):
        out = b"d"
        for key in sorted(value):  # bencode dict keys must be sorted
            key_bytes = key.encode("utf-8") if isinstance(key, str) else key
            out += bencode(key_bytes) + bencode(value[key])
        return out + b"e"
    raise TypeError(f"Cannot bencode value of type {type(value).__name__}")


def _pieces(data: bytes, piece_length: int) -> bytes:
    return b"".join(
        hashlib.sha1(data[i : i + piece_length]).digest() for i in range(0, len(data), piece_length)
    )


def build_info_dict(name: str, data: bytes, piece_length: int = DEFAULT_PIECE_LENGTH) -> dict:
    return {
        "name": name,
        "piece length": piece_length,
        "length": len(data),
        "pieces": _pieces(data, piece_length),
    }


def build_webseed_torrent(
    name: str,
    data: bytes,
    webseed_url: str,
    *,
    piece_length: int = DEFAULT_PIECE_LENGTH,
) -> bytes:
    """Build a tracker-less single-file .torrent whose only source is a webseed.

    Args:
        name: file name inside the torrent (e.g. ``sample-book.epub``).
        data: the exact file bytes the webseed URL must serve.
        webseed_url: BEP-19 url-list entry — the direct HTTP URL for ``data``.
    """
    info = build_info_dict(name, data, piece_length)
    metainfo = {
        "info": info,
        # Single-entry webseed. For a single-file torrent the url-list entry is the
        # direct file URL, so it must serve exactly ``data``.
        "url-list": [webseed_url],
        "comment": "shelfmark e2e webseed torrent",
        "created by": "shelfmark-e2e",
    }
    return bencode(metainfo)


def info_hash(torrent_bytes: bytes) -> str:
    """The btih (SHA1 of the bencoded ``info`` dict) as a hex string.

    Re-encodes via a tiny scan so we don't need a full decoder here.
    """
    marker = b"4:infod"
    start = torrent_bytes.find(marker)
    if start < 0:
        raise ValueError("no info dict found in torrent")
    info_start = start + len(b"4:info")
    # The info value begins at 'd'; find its matching 'e' by bencode-aware scan.
    end = _scan_bencoded(torrent_bytes, info_start)
    return hashlib.sha1(torrent_bytes[info_start:end]).hexdigest()


def _scan_bencoded(buf: bytes, pos: int) -> int:
    """Return the index just past the bencoded value starting at ``pos``."""
    token = buf[pos : pos + 1]
    if token == b"i":
        return buf.index(b"e", pos) + 1
    if token in (b"l", b"d"):
        pos += 1
        while buf[pos : pos + 1] != b"e":
            if token == b"d":  # dicts: key then value
                pos = _scan_bencoded(buf, pos)
            pos = _scan_bencoded(buf, pos)
        return pos + 1
    # byte string: <len>:<bytes>
    colon = buf.index(b":", pos)
    length = int(buf[pos:colon])
    return colon + 1 + length

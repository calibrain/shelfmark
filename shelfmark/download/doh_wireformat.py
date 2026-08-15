"""RFC 8484 DNS wireformat encoding/decoding for DoH providers.

Providers split into two incompatible camps and the difference is not cosmetic:

* **JSON** (Cloudflare, Google) - ``?name=<host>&type=A`` returning a JSON body. A
  convention, not a standard, and the only one Shelfmark used to speak.
* **Wireformat** (Quad9, OpenDNS) - RFC 8484 proper: a base64url-encoded DNS message
  in ``?dns=``, answered with ``application/dns-message``. Quad9 additionally
  *requires HTTP/2* per RFC 8484 section 5.2 and answers HTTP/1.1 with 505.

This module carries the codec only; the transport choice lives in the resolver.
Encoding a query is a handful of bytes, and parsing an answer needs message
compression support (RFC 1035 section 4.1.4) because answer names are almost always
pointers back into the question.
"""

from __future__ import annotations

import base64
import secrets
import struct

# Record types we resolve.
TYPE_A = 1
TYPE_AAAA = 28

_CLASS_IN = 1
_HEADER = struct.Struct(">HHHHHH")
_RR_FIXED = struct.Struct(">HHIH")  # type, class, ttl, rdlength
_FLAG_RECURSION_DESIRED = 0x0100
_MAX_LABEL_JUMPS = 64  # cap pointer-following so a malicious answer cannot loop
_MAX_NAME_LENGTH = 255


class WireformatError(ValueError):
    """Raised when a DNS wireformat message cannot be parsed."""


def encode_query(hostname: str, record_type: int) -> bytes:
    """Build a DNS query message for ``hostname``.

    The ID is zero because RFC 8484 section 4.1 requires it for cacheability, but the
    caller may randomise it when not using a cache.
    """
    if not hostname:
        msg = "hostname must not be empty"
        raise WireformatError(msg)

    question = bytearray()
    for label in hostname.rstrip(".").split("."):
        encoded = label.encode("idna") if not label.isascii() else label.encode("ascii")
        if not encoded or len(encoded) > 63:
            msg = f"invalid DNS label in {hostname!r}"
            raise WireformatError(msg)
        question.append(len(encoded))
        question.extend(encoded)
    question.append(0)
    question.extend(struct.pack(">HH", record_type, _CLASS_IN))

    header = _HEADER.pack(0, _FLAG_RECURSION_DESIRED, 1, 0, 0, 0)
    return header + bytes(question)


def encode_query_param(hostname: str, record_type: int) -> str:
    """Return the base64url ``dns=`` parameter value for a query (padding stripped)."""
    return base64.urlsafe_b64encode(encode_query(hostname, record_type)).rstrip(b"=").decode()


def _read_name(message: bytes, offset: int) -> int:
    """Skip over a (possibly compressed) name, returning the offset after it."""
    jumps = 0
    length = 0
    while True:
        if offset >= len(message):
            msg = "truncated DNS name"
            raise WireformatError(msg)
        label_len = message[offset]
        if label_len == 0:
            return offset + 1
        if label_len & 0xC0 == 0xC0:
            # A pointer ends this name; the rest of the record follows the 2 bytes.
            if offset + 1 >= len(message):
                msg = "truncated DNS name pointer"
                raise WireformatError(msg)
            return offset + 2
        offset += 1 + label_len
        length += 1 + label_len
        jumps += 1
        if jumps > _MAX_LABEL_JUMPS or length > _MAX_NAME_LENGTH:
            msg = "malformed DNS name"
            raise WireformatError(msg)


def decode_answer(message: bytes, record_type: int) -> list[str]:
    """Extract the IP addresses of ``record_type`` from a DNS response message.

    Returns an empty list for a well-formed response that carries no matching record
    (NXDOMAIN, or only CNAMEs), and raises WireformatError for a malformed one - the
    caller treats those differently.
    """
    if len(message) < _HEADER.size:
        msg = "DNS response shorter than its header"
        raise WireformatError(msg)

    _id, _flags, qdcount, ancount, _ns, _ar = _HEADER.unpack_from(message, 0)
    offset = _HEADER.size

    for _ in range(qdcount):
        offset = _read_name(message, offset)
        offset += 4  # QTYPE + QCLASS

    results: list[str] = []
    for _ in range(ancount):
        offset = _read_name(message, offset)
        if offset + _RR_FIXED.size > len(message):
            msg = "truncated resource record"
            raise WireformatError(msg)
        rtype, rclass, _ttl, rdlength = _RR_FIXED.unpack_from(message, offset)
        offset += _RR_FIXED.size
        rdata = message[offset : offset + rdlength]
        if len(rdata) != rdlength:
            msg = "truncated record data"
            raise WireformatError(msg)
        offset += rdlength

        if rclass != _CLASS_IN or rtype != record_type:
            continue
        if rtype == TYPE_A and rdlength == 4:
            results.append(".".join(str(b) for b in rdata))
        elif rtype == TYPE_AAAA and rdlength == 16:
            groups = struct.unpack(">8H", rdata)
            results.append(_compress_ipv6(groups))

    return results


def _compress_ipv6(groups: tuple[int, ...]) -> str:
    """Render an IPv6 address with the longest zero run collapsed to '::'."""
    best_start = best_len = -1
    run_start = -1
    for i, group in enumerate([*list(groups), 1]):  # sentinel closes a trailing run
        if group == 0 and i < len(groups):
            if run_start < 0:
                run_start = i
        elif run_start >= 0:
            if i - run_start > best_len:
                best_start, best_len = run_start, i - run_start
            run_start = -1

    parts = [format(g, "x") for g in groups]
    if best_len > 1:
        return ":".join(parts[:best_start]) + "::" + ":".join(parts[best_start + best_len :])
    return ":".join(parts)


def random_query_id() -> int:
    """A random DNS message ID, for callers that do not want the RFC 8484 zero."""
    return secrets.randbelow(0x10000)

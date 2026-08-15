"""RFC 8484 wireformat codec.

Quad9 and OpenDNS reject the JSON API that Cloudflare and Google popularised, so
these providers only work through wireformat. Quad9 additionally requires HTTP/2
(section 5.2) and answers HTTP/1.1 with 505.
"""

import base64
import struct

import pytest

from shelfmark.download import doh_wireformat as wf


def _decode_param(param: str) -> bytes:
    padding = "=" * (-len(param) % 4)
    return base64.urlsafe_b64decode(param + padding)


def _build_response(
    *, qname: str = "example.com", answers: list[tuple[int, bytes]], qtype: int = wf.TYPE_A
) -> bytes:
    """Assemble a response whose answer names are compression pointers to the question."""
    question = b""
    for label in qname.split("."):
        question += bytes([len(label)]) + label.encode()
    question += b"\x00" + struct.pack(">HH", qtype, 1)

    body = b""
    for rtype, rdata in answers:
        body += b"\xc0\x0c"  # pointer to offset 12 (the question name)
        body += struct.pack(">HHIH", rtype, 1, 300, len(rdata)) + rdata

    header = struct.pack(">HHHHHH", 0, 0x8180, 1, len(answers), 0, 0)
    return header + question + body


def test_encode_query_is_a_well_formed_dns_message():
    raw = _decode_param(wf.encode_query_param("example.com", wf.TYPE_A))

    msg_id, flags, qdcount, ancount, _ns, _ar = struct.unpack_from(">HHHHHH", raw, 0)
    assert msg_id == 0  # RFC 8484 section 4.1: zero for cacheability
    assert flags == 0x0100  # recursion desired
    assert (qdcount, ancount) == (1, 0)
    assert raw[12:] == b"\x07example\x03com\x00" + struct.pack(">HH", wf.TYPE_A, 1)


def test_encode_query_param_is_unpadded_base64url():
    param = wf.encode_query_param("example.com", wf.TYPE_A)
    assert "=" not in param
    assert "+" not in param and "/" not in param


def test_encode_query_strips_trailing_dot():
    assert _decode_param(wf.encode_query_param("example.com.", wf.TYPE_A)) == _decode_param(
        wf.encode_query_param("example.com", wf.TYPE_A)
    )


def test_encode_query_rejects_empty_hostname():
    with pytest.raises(wf.WireformatError):
        wf.encode_query("", wf.TYPE_A)


def test_encode_query_rejects_oversized_label():
    with pytest.raises(wf.WireformatError):
        wf.encode_query("a" * 64 + ".com", wf.TYPE_A)


def test_decode_a_records():
    response = _build_response(answers=[(wf.TYPE_A, bytes([93, 184, 216, 34]))])
    assert wf.decode_answer(response, wf.TYPE_A) == ["93.184.216.34"]


def test_decode_multiple_a_records_preserves_order():
    response = _build_response(
        answers=[(wf.TYPE_A, bytes([1, 1, 1, 1])), (wf.TYPE_A, bytes([8, 8, 8, 8]))]
    )
    assert wf.decode_answer(response, wf.TYPE_A) == ["1.1.1.1", "8.8.8.8"]


def test_decode_skips_cname_records_in_the_chain():
    """Answers routinely lead with a CNAME; only the requested type is an address."""
    cname = b"\x03www\x07example\x03com\x00"
    response = _build_response(answers=[(5, cname), (wf.TYPE_A, bytes([93, 184, 216, 34]))])
    assert wf.decode_answer(response, wf.TYPE_A) == ["93.184.216.34"]


def test_decode_aaaa_compresses_zero_run():
    # 2606:4700:0:0:0:0:6810:84e5 -> the middle zero run collapses to "::"
    rdata = struct.pack(">8H", 0x2606, 0x4700, 0, 0, 0, 0, 0x6810, 0x84E5)
    response = _build_response(answers=[(wf.TYPE_AAAA, rdata)], qtype=wf.TYPE_AAAA)
    assert wf.decode_answer(response, wf.TYPE_AAAA) == ["2606:4700::6810:84e5"]


def test_decode_aaaa_collapses_only_the_longest_zero_run():
    # 2001:0:0:1:0:0:0:1 - the second, longer run is the one that collapses.
    rdata = struct.pack(">8H", 0x2001, 0, 0, 1, 0, 0, 0, 1)
    response = _build_response(answers=[(wf.TYPE_AAAA, rdata)], qtype=wf.TYPE_AAAA)
    assert wf.decode_answer(response, wf.TYPE_AAAA) == ["2001:0:0:1::1"]


def test_decode_aaaa_without_zero_run():
    rdata = struct.pack(">8H", 0x2001, 0x0DB8, 1, 2, 3, 4, 5, 6)
    response = _build_response(answers=[(wf.TYPE_AAAA, rdata)], qtype=wf.TYPE_AAAA)
    assert wf.decode_answer(response, wf.TYPE_AAAA) == ["2001:db8:1:2:3:4:5:6"]


def test_decode_nxdomain_returns_empty_not_an_error():
    """An empty answer is a valid response; the caller falls back rather than retrying."""
    response = _build_response(answers=[])
    assert wf.decode_answer(response, wf.TYPE_A) == []


def test_decode_rejects_truncated_header():
    with pytest.raises(wf.WireformatError):
        wf.decode_answer(b"\x00\x01", wf.TYPE_A)


def test_decode_rejects_truncated_record():
    response = _build_response(answers=[(wf.TYPE_A, bytes([1, 2, 3, 4]))])
    with pytest.raises(wf.WireformatError):
        wf.decode_answer(response[:-2], wf.TYPE_A)


def test_decode_does_not_hang_on_a_malicious_name():
    """A self-referential name must not loop forever."""
    header = struct.pack(">HHHHHH", 0, 0x8180, 1, 0, 0, 0)
    # A run of maximum-length labels that never terminates.
    body = (b"\x3f" + b"a" * 63) * 8
    with pytest.raises(wf.WireformatError):
        wf.decode_answer(header + body, wf.TYPE_A)


def test_wireformat_providers_are_flagged(monkeypatch):
    """The provider table and the resolver must agree on who needs wireformat."""
    import shelfmark.download.network as network

    for name, servers, url in network.DNS_PROVIDERS:
        resolver = network.DoHResolver(url, "x.invalid", servers[0])
        expected = name in ("quad9", "opendns")
        assert resolver.use_wireformat is expected, f"{name} wireformat flag wrong"

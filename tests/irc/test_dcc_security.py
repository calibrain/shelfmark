import socket

import pytest

from shelfmark.release_sources.irc.dcc import (
    DCCOffer,
    DCCParseError,
    DCCSecurityError,
    download_dcc,
    parse_dcc_send,
    safe_dcc_filename,
    validate_dcc_endpoint,
)


def test_safe_dcc_filename_allows_plain_filenames() -> None:
    assert safe_dcc_filename("results.txt") == "results.txt"
    assert safe_dcc_filename("Author - Title.epub") == "Author - Title.epub"


@pytest.mark.parametrize(
    "filename",
    [
        "",
        ".",
        "..",
        "../outside.txt",
        "/tmp/outside.txt",
        r"..\outside.txt",
        r"C:\temp\outside.txt",
    ],
)
def test_safe_dcc_filename_rejects_paths(filename: str) -> None:
    with pytest.raises(DCCSecurityError):
        safe_dcc_filename(filename)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "0.0.0.0",
    ],
)
def test_validate_dcc_endpoint_rejects_non_public_ips(ip: str) -> None:
    with pytest.raises(DCCSecurityError):
        validate_dcc_endpoint(DCCOffer(filename="book.epub", ip=ip, port=1234, size=1))


@pytest.mark.parametrize("port", [0, 65536])
def test_validate_dcc_endpoint_rejects_invalid_ports(port: int) -> None:
    with pytest.raises(DCCSecurityError):
        validate_dcc_endpoint(DCCOffer(filename="book.epub", ip="8.8.8.8", port=port, size=1))


def test_validate_dcc_endpoint_allows_public_endpoint() -> None:
    validate_dcc_endpoint(DCCOffer(filename="book.epub", ip="8.8.8.8", port=443, size=1))


def test_parse_dcc_send_rejects_out_of_range_ip_integer() -> None:
    with pytest.raises(DCCParseError):
        parse_dcc_send('DCC SEND "book.epub" 999999999999999999 443 1')


def test_download_dcc_rejects_private_endpoint_before_socket_connect(
    monkeypatch,
    tmp_path,
) -> None:
    def fail_socket(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("download should reject the endpoint before opening a socket")

    monkeypatch.setattr(socket, "socket", fail_socket)

    with pytest.raises(DCCSecurityError):
        download_dcc(
            DCCOffer(filename="book.epub", ip="127.0.0.1", port=1234, size=1),
            tmp_path / "book.epub",
        )

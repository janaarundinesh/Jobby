"""Fetching a posting: the checks that keep it pointed at public addresses."""

import socket

import pytest

import extract


def _answers(*values):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (value, 0))
        for value in values
    ]


def test_a_host_that_resolves_onto_the_local_network_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _answers("10.0.0.5"))
    with pytest.raises(extract.ExtractionError):
        extract.fetch_page("http://internal.example/jobs/1")


def test_one_private_answer_among_public_ones_is_refused(monkeypatch):
    # A name with both kinds of answer is still a way in, so none of them pass.
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _answers("93.184.216.34", "192.168.1.10"),
    )
    with pytest.raises(extract.ExtractionError):
        extract._public_addresses("mixed.example")


def test_the_socket_dials_the_address_that_was_checked(monkeypatch):
    # Rebinding works by answering the check with a public address and the
    # connection with a private one. There is only one lookup here, and the
    # sockets go to addresses from it.
    lookups = []

    def lookup(host, port, **kwargs):
        lookups.append(host)
        return _answers("93.184.216.34", "93.184.216.35")

    dialled = []

    def connect(address, timeout=None, source_address=None):
        dialled.append(address[0])
        raise OSError("no route to host")

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    monkeypatch.setattr(socket, "create_connection", connect)

    with pytest.raises(extract.ExtractionError):
        extract._connect_public("jobs.example", 443, 5)

    assert lookups == ["jobs.example"]
    assert dialled == ["93.184.216.34", "93.184.216.35"]

"""Test-only provider metadata and external-network isolation; no path rewriting."""
import ipaddress
import os
import socket

import pytest


# LiteLLM otherwise fetches its model-price catalog while importing the legacy
# detector. Use the package's bundled catalog even during test collection.
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"


@pytest.fixture(autouse=True)
def loopback_network_only(monkeypatch):
    """Permit fixture servers, fail even if a test catches an external attempt."""
    attempts = []

    def check_host(host):
        if host is None or host == "localhost":
            return
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError:
            pass
        attempts.append("external network attempt")
        raise AssertionError("Automated tests may only connect to loopback fixtures")

    native_connect = socket.socket.connect
    native_connect_ex = socket.socket.connect_ex
    native_getaddrinfo = socket.getaddrinfo

    def connect(sock, address):
        if isinstance(address, tuple):
            check_host(address[0])
        return native_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple):
            check_host(address[0])
        return native_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        check_host(host)
        return native_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    yield
    assert not attempts, "An external network attempt was blocked during this test"

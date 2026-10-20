"""Prefer IPv4 for outbound connections.

Some networks (including the development laptop's) have a broken IPv6 route to *.googleapis.com:
handshakes time out or are cut off, while browsers silently fall back to IPv4. Python does not fall
back reliably, so name resolution is asked for IPv4 first, falling back to the original call for
IPv6-only hosts.
"""
from __future__ import annotations

import socket

_installed = False


def prefer_ipv4() -> None:
    global _installed
    if _installed:
        return
    original = socket.getaddrinfo

    def ipv4_first(host, port, family=0, *args, **kwargs):
        if family in (0, socket.AF_UNSPEC):
            try:
                return original(host, port, socket.AF_INET, *args, **kwargs)
            except socket.gaierror:
                pass
        return original(host, port, family, *args, **kwargs)

    socket.getaddrinfo = ipv4_first
    _installed = True

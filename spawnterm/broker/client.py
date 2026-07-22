#!/usr/bin/env python3
"""Reusable synchronous client for the spawnTerm broker (#34).

Imports only stdlib ``socket`` (+ the pure :mod:`protocol` / :mod:`paths`); no
asyncio, no iTerm2. A blocking request/response client is the friendliest shape
for callers: the CLI, the Tier 1 daemon bridge (#37), and one-off scripts.

Each :meth:`BrokerClient.request` call opens a short-lived connection, writes one
newline-delimited JSON request line, reads exactly one response line, and closes.
This keeps the client stateless and safe to share; the broker server handles
many such connections concurrently over the unix socket.
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any, Optional

import paths
import protocol

DEFAULT_TIMEOUT = 5.0


class BrokerClient:
    """Connects to the broker unix socket and does request/response."""

    def __init__(
        self,
        sock_path: Optional[str | Path] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.sock_path = str(sock_path) if sock_path else str(paths.broker_sock_path())
        self.timeout = timeout

    def request(self, message: dict[str, Any]) -> dict[str, Any]:
        """Send one request object; return the decoded response object.

        Raises ``OSError`` if the socket cannot be reached (server down) and
        :class:`protocol.ProtocolError` if the reply is missing/malformed.
        """
        line = protocol.encode(message)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(self.sock_path)
            stream = sock.makefile("rwb")
            try:
                stream.write(line)
                stream.flush()
                reply = stream.readline()
            finally:
                stream.close()
        if not reply:
            raise protocol.ProtocolError("no response from broker (connection closed)")
        return protocol.decode(reply)

    def ping(self, echo: Any = None) -> dict[str, Any]:
        """Send ``{"op":"ping"}``; ``echo`` is round-tripped when not ``None``."""
        message: dict[str, Any] = {"op": "ping"}
        if echo is not None:
            message["echo"] = echo
        return self.request(message)

    def health(self) -> dict[str, Any]:
        """Send ``{"op":"health"}`` and return the broker's status."""
        return self.request({"op": "health"})

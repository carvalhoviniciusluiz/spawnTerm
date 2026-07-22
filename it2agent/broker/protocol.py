#!/usr/bin/env python3
"""Wire protocol for the it2agent broker (#34).

Pure — stdlib ``json`` only; no socket, no sqlite, no iTerm2.

**Framing: newline-delimited JSON.** One request object per line, one response
object per line. A "line" is UTF-8 text terminated by ``\\n``. Each message is a
single JSON **object** (never a bare array/scalar). This is the simplest frame
that is trivially testable, streamable, and language-agnostic.

Request shape (only ``op`` is required):

    {"op": "<name>", ...op-specific fields...}

Response shape — always an object with a boolean ``ok``:

    {"ok": true,  ...result fields...}
    {"ok": false, "error": {"code": "<code>", "message": "<human text>", ...}}

The op semantics (which ops exist, what fields they take) live in
:mod:`dispatch`; this module only encodes/decodes and defines the framing.
"""

from __future__ import annotations

import json
from typing import Any

# Encoder is compact + stable (sorted keys) so round-trips and tests are
# deterministic. Decoding tolerates any key order / whitespace.
_SEPARATORS = (",", ":")


class ProtocolError(ValueError):
    """A line could not be decoded into a valid request/response object."""


def encode(obj: dict[str, Any]) -> bytes:
    """Serialize one message object to a single newline-terminated UTF-8 line."""
    if not isinstance(obj, dict):
        raise ProtocolError("message must be a JSON object")
    text = json.dumps(obj, separators=_SEPARATORS, sort_keys=True)
    return (text + "\n").encode("utf-8")


def decode(line: bytes | bytearray | str) -> dict[str, Any]:
    """Parse one framed line into a message object.

    Raises :class:`ProtocolError` on empty input, invalid JSON, or a payload
    that is not a JSON object. Callers (the server) turn this into a structured
    error response rather than crashing.
    """
    if isinstance(line, (bytes, bytearray)):
        try:
            line = line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError(f"invalid UTF-8: {exc}") from exc
    line = line.strip()
    if not line:
        raise ProtocolError("empty line")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError("message must be a JSON object")
    return obj

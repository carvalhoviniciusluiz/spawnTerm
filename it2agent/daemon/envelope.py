#!/usr/bin/env python3
"""Agent→daemon message envelope parser (it2agent Tier 1.1, #26).

This module is **pure**: no ``iterm2`` import, no I/O. Agents signal the daemon
by writing an iTerm2 *custom control sequence*:

    OSC 1337 ; Custom=id=it2agent : <payload> ST

iTerm2 delivers the ``<payload>`` (the bytes after the identity) to the daemon's
custom-escape-sequence monitor. The payload is a small JSON envelope:

    {"v": 1, "type": "msg", "to": "<agent_id>", "from": "<agent_id>",
     "body": "..."}

Only ``v`` and ``type`` are required. ``to`` / ``from`` / ``body`` are optional
here (routing is #28; ingest just logs). Parsing is **defensive**: it never
raises — malformed input yields ``ParseResult(ok=False, error=...)`` so a bad
escape sequence can be logged and dropped without taking down the daemon.

Tier 1.1 only *ingests* (logs) envelopes. #28 reuses ``parse_envelope`` for
routing, which is why the parsed fields are exposed as a structured record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Envelope schema version this daemon understands. Unknown versions are logged
# and dropped, never crashed on.
ENVELOPE_VERSION = 1

# Recognized message types. Unknown types parse successfully (so #28 can extend
# the vocabulary without a daemon change) but are flagged via ``known_type``.
KNOWN_TYPES = frozenset({"msg", "ping", "status"})


@dataclass(frozen=True)
class Envelope:
    """A parsed, validated agent message envelope."""

    v: int
    type: str
    to: str | None = None
    sender: str | None = None  # the wire key is "from" (a Python keyword)
    body: Any = None
    raw: str = ""

    @property
    def known_type(self) -> bool:
        return self.type in KNOWN_TYPES


@dataclass(frozen=True)
class ParseResult:
    """Outcome of parsing one payload. ``ok`` implies ``envelope`` is set;
    otherwise ``error`` explains why it was dropped."""

    ok: bool
    envelope: Envelope | None = None
    error: str | None = None


def _fail(error: str) -> ParseResult:
    return ParseResult(ok=False, envelope=None, error=error)


def parse_envelope(payload: str) -> ParseResult:
    """Parse a custom-escape-sequence payload into an :class:`Envelope`.

    Never raises. Returns ``ParseResult(ok=False, error=...)`` for any of:
    empty payload, invalid JSON, non-object JSON, missing/invalid ``v``,
    unsupported version, or missing/empty ``type``.
    """
    if payload is None or not str(payload).strip():
        return _fail("empty payload")

    text = str(payload).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        return _fail(f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        return _fail(f"envelope must be a JSON object, got {type(data).__name__}")

    if "v" not in data:
        return _fail("missing required field: v")
    version = data.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        return _fail(f"field v must be an integer, got {type(version).__name__}")
    if version != ENVELOPE_VERSION:
        return _fail(f"unsupported envelope version: {version}")

    msg_type = data.get("type")
    if not isinstance(msg_type, str) or not msg_type.strip():
        return _fail("missing or empty required field: type")

    def _opt_str(key: str) -> str | None:
        value = data.get(key)
        return value if isinstance(value, str) else None

    envelope = Envelope(
        v=version,
        type=msg_type.strip(),
        to=_opt_str("to"),
        sender=_opt_str("from"),
        body=data.get("body"),
        raw=text,
    )
    return ParseResult(ok=True, envelope=envelope, error=None)

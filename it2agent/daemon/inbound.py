#!/usr/bin/env python3
"""Inbound native-state reader for the it2agent daemon (Tier 2, #115).

One-way, read-only cooperation. Today the flow is outbound (we publish INTO the
native surfaces — OSC 21337 tab status / user vars). This module adds the
**inbound** half: reflect what iTerm2 NATIVELY knows about each session — its
name, the dot-free ``user.agent_*`` vars, and its native cc-status / OSC 21337
tab-status state — INTO *our own* registry, so it2agent tools see what native
sees. We only READ the native side; we only WRITE our registry. We do **not**
duplicate the native Cockpit — we just populate the registry.

This module is **pure**: no ``iterm2`` import, no I/O, so it imports and
unit-tests without a running iTerm2 (matching ``registry`` / ``spawn`` /
``bridge``). The thin adapter reads a per-session snapshot dict off the Python
API and hands it here; the mapping (session-record dict -> registry op) lives
here so it is exhaustively testable with fixtures against a fake registry.

Status precedence (see WorkgroupIntrospection.state(forTabStatus:)): an explicit
dot-free ``agent_status`` user var — the value an it2agent-spawned agent stamps —
always wins. Absent that, we fall back to the native cc-status *statusText* the
cc-status hook / triggers write (``idle`` / ``working`` / ``waiting``) and
translate it into our lifecycle vocabulary (``busy`` / ``blocked`` / ``done`` /
``idle``) so a purely-native session still lands a meaningful status.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Reuse the canonical dot-free agent var names so the inbound reader and the
# registry agree on exactly which vars are recognized (#23).
from registry import AGENT_VAR_KEYS

# iTerm2 prefixes user-set variables with ``user.``; a snapshot may carry either
# the bare dot-free name or the reported ``user.``-prefixed one. We accept both.
USER_VAR_PREFIX = "user."

# Session-variable names the adapter should try, in order, to read the native
# cc-status / OSC 21337 tab-status text off the Python API (the tab-status text,
# NOT our own ``user.agent_status`` var — that is read separately and wins). The
# first non-empty read is used as ``cc_status``; none present -> the record
# carries no ``cc_status`` and the mapping falls back to the agent_status var (or
# to no status). Kept here (not in the adapter) so the set is part of the pure,
# reviewed contract.
NATIVE_STATUS_VAR_CANDIDATES = (
    "tabStatus",
    "tab.status",
)

# Native cc-status statusText (what the cc-status hook / set_status triggers
# write) -> our lifecycle status vocabulary. Values already in our vocabulary
# pass through unchanged so an it2agent-emit ``ccstatus`` value survives too.
CC_STATUS_TO_AGENT_STATUS = {
    "working": "busy",
    "waiting": "blocked",
    "idle": "idle",
    "busy": "busy",
    "blocked": "blocked",
    "done": "done",
}


@dataclass(frozen=True)
class RegistryOp:
    """A pure description of the registry mutation a native session implies.

    ``op`` is always ``"upsert"`` — the registry's :meth:`Registry.add` is an
    upsert that merges onto any existing record rather than clobbering it, which
    is exactly the semantics an inbound refresh wants. ``agent_vars`` holds only
    recognized, non-empty dot-free agent vars (including the resolved
    ``agent_status``). Kept as a plain dataclass so tests assert on equality.
    """

    session_id: str
    op: str = "upsert"
    title: str = ""
    cwd: str = ""
    agent_vars: dict = field(default_factory=dict)


def _strip_user_prefix(key: str) -> str:
    """Return ``key`` without a leading ``user.`` (tolerates either form)."""
    return key[len(USER_VAR_PREFIX):] if key.startswith(USER_VAR_PREFIX) else key


def normalize_agent_status(*, agent_status: str = "", cc_status: str = "") -> str:
    """Resolve the effective lifecycle status for a native session (pure).

    An explicit dot-free ``agent_status`` var wins (it is what an it2agent agent
    stamps). Absent that, translate the native cc-status ``statusText`` via
    :data:`CC_STATUS_TO_AGENT_STATUS`. An unrecognized / empty source yields
    ``""`` (caller then stamps no status rather than a bogus one).
    """
    explicit = (agent_status or "").strip()
    if explicit:
        return explicit
    native = (cc_status or "").strip().lower()
    return CC_STATUS_TO_AGENT_STATUS.get(native, "")


def map_native_session(record: dict) -> Optional[RegistryOp]:
    """Map one native session-record dict to a :class:`RegistryOp` (pure).

    ``record`` may carry any of: ``session_id`` (required — a record without a
    non-empty id is a no-op, returns ``None``), ``name``/``title``, ``cwd``/
    ``path``, the dot-free (or ``user.``-prefixed) ``agent_*`` vars, and
    ``cc_status`` (the native tab-status text). Only recognized, non-empty agent
    vars survive into the op; ``agent_status`` is the resolved value from
    :func:`normalize_agent_status` (explicit var, else translated cc-status).
    """
    session_id = str(record.get("session_id") or "").strip()
    if not session_id:
        return None

    title = str(record.get("name") or record.get("title") or "")
    cwd = str(record.get("cwd") or record.get("path") or "")

    # Collect recognized dot-free agent vars (tolerating the user. prefix),
    # skipping the raw agent_status here — it is resolved separately below so
    # the native cc-status fallback can fill it in.
    agent_vars: dict = {}
    raw_status = ""
    for raw_key, value in record.items():
        key = _strip_user_prefix(str(raw_key))
        if key not in AGENT_VAR_KEYS or value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if key == "agent_status":
            raw_status = text
            continue
        agent_vars[key] = text

    status = normalize_agent_status(agent_status=raw_status, cc_status=record.get("cc_status", ""))
    if status:
        agent_vars["agent_status"] = status

    return RegistryOp(session_id=session_id, title=title, cwd=cwd, agent_vars=agent_vars)


def apply_op(registry, op: RegistryOp):
    """Apply one :class:`RegistryOp` to the in-memory registry (pure I/O-wise).

    Uses :meth:`Registry.add` (an upsert that merges, never clobbering populated
    fields with blanks). Returns the resulting ``SessionRecord``.
    """
    return registry.add(op.session_id, title=op.title, cwd=op.cwd, **op.agent_vars)


def reflect_native_sessions(registry, records) -> list:
    """Map + apply a batch of native session records into ``registry`` (pure).

    Records without a usable ``session_id`` are skipped. An empty batch is a
    clean no-op (returns ``[]``) — this is the shape the adapter produces when
    the Python API is off / unreachable. Returns the applied ``SessionRecord``s.
    """
    applied = []
    for record in records:
        op = map_native_session(record)
        if op is None:
            continue
        applied.append(apply_op(registry, op))
    return applied

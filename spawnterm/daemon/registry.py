#!/usr/bin/env python3
"""In-memory session registry for the spawnTerm daemon (Tier 1.1, #26).

This module is **pure**: it has no ``iterm2`` import and performs no I/O, so it
imports and unit-tests without a running iTerm2. The daemon's thin iTerm2
adapter (``adapter.py``) feeds it events; all the add/remove/update/query and
idle-state logic lives here.

The registry is **ephemeral by design** — it is rebuilt from live iTerm2 state
every time the daemon starts. The durable queue/source-of-truth is Tier 2 (#4),
explicitly out of scope here.

Agent identity is carried in iTerm2 user variables. iTerm2 forbids ``.`` in a
user-var key, so the emitter (#7) writes the **dot-free** names ``agent_status``
/ ``agent_role`` / ``agent_task`` / ``agent_id`` (surfaced by the API as
``user.agent_status`` etc.). This module keys on those dot-free names.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Canonical dot-free agent user-var names (see module docstring / #23).
AGENT_VAR_KEYS = ("agent_status", "agent_role", "agent_task", "agent_id")


@dataclass(frozen=True)
class SessionRecord:
    """A single tracked iTerm2 session. Immutable; updates return a new copy."""

    session_id: str
    title: str = ""
    cwd: str = ""
    agent_status: str = ""
    agent_role: str = ""
    agent_task: str = ""
    agent_id: str = ""
    # True when the session is at a shell prompt / awaiting input (see #26 idle).
    idle: bool = False


def _clean_agent_vars(agent_vars: dict) -> dict:
    """Keep only recognized dot-free agent_* keys with non-None values.

    Tolerates callers passing the ``user.``-prefixed names iTerm2 reports by
    stripping a leading ``user.`` before matching.
    """
    cleaned = {}
    for raw_key, value in agent_vars.items():
        key = raw_key[len("user."):] if raw_key.startswith("user.") else raw_key
        if key in AGENT_VAR_KEYS and value is not None:
            cleaned[key] = value
    return cleaned


class Registry:
    """Ephemeral map of ``session_id -> SessionRecord``.

    All mutators are idempotent and defensive: unknown session ids on update
    return ``None`` rather than raising, so a stray event never crashes the
    daemon.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}

    # -- lifecycle (new_session / terminate_session) ----------------------

    def add(
        self,
        session_id: str,
        *,
        title: str = "",
        cwd: str = "",
        **agent_vars,
    ) -> SessionRecord:
        """Add or replace a session. Called on ``new_session``.

        Re-adding an existing id merges the new title/cwd/agent vars onto the
        existing record rather than blindly clobbering populated fields.
        """
        existing = self._sessions.get(session_id)
        clean = _clean_agent_vars(agent_vars)
        if existing is None:
            record = SessionRecord(
                session_id=session_id,
                title=title,
                cwd=cwd,
                **clean,
            )
        else:
            updates = dict(clean)
            if title:
                updates["title"] = title
            if cwd:
                updates["cwd"] = cwd
            record = replace(existing, **updates)
        self._sessions[session_id] = record
        return record

    def remove(self, session_id: str) -> bool:
        """Remove a session. Called on ``terminate_session``.

        Returns True if a session was removed, False if it was unknown.
        """
        return self._sessions.pop(session_id, None) is not None

    # -- updates (variable / prompt monitors) -----------------------------

    def update(
        self,
        session_id: str,
        *,
        title: str | None = None,
        cwd: str | None = None,
        **agent_vars,
    ) -> SessionRecord | None:
        """Update fields of a known session. Returns the new record, or None
        if the session is unknown. Only supplied (non-None) fields change."""
        existing = self._sessions.get(session_id)
        if existing is None:
            return None
        updates: dict = _clean_agent_vars(agent_vars)
        if title is not None:
            updates["title"] = title
        if cwd is not None:
            updates["cwd"] = cwd
        if not updates:
            return existing
        record = replace(existing, **updates)
        self._sessions[session_id] = record
        return record

    def set_idle(self, session_id: str, idle: bool = True) -> SessionRecord | None:
        """Mark a session idle (awaiting input) or busy. Called from the prompt
        monitor: reaching a shell prompt means the agent is idle. Returns the
        new record, or None if the session is unknown."""
        existing = self._sessions.get(session_id)
        if existing is None:
            return None
        if existing.idle == idle:
            return existing
        record = replace(existing, idle=idle)
        self._sessions[session_id] = record
        return record

    # -- queries ----------------------------------------------------------

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    def all(self) -> list[SessionRecord]:
        return list(self._sessions.values())

    def ids(self) -> list[str]:
        return list(self._sessions.keys())

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)

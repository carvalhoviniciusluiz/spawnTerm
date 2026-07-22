#!/usr/bin/env python3
"""Thin iTerm2 I/O adapter for the spawnTerm daemon (Tier 1.1, #26).

This is the ONLY daemon module that talks to iTerm2. It imports ``iterm2``
**lazily** (inside methods, never at module top-level) so the pure logic
modules (``registry``, ``envelope``) — and this file — import fine in an
environment without the ``iterm2`` package. All state lives in the pure
:class:`registry.Registry`; the adapter only translates iTerm2 events into
registry calls and logs ingested envelopes.

Wiring:
  * ``NewSessionMonitor`` / ``SessionTerminationMonitor`` → registry add/remove.
  * ``CustomControlSequenceMonitor(identity="spawnterm")`` → envelope ingest
    (parse + structured log, then best-effort route via ``router``, #28).
  * one ``PromptMonitor`` per session → mark the session idle (awaiting input).

Nothing here is exercised by CI (iTerm2 is unavailable); the testable logic is
intentionally pushed down into the pure modules.
"""

from __future__ import annotations

import asyncio
import logging

from envelope import parse_envelope
from registry import AGENT_VAR_KEYS, Registry
from router import messaging_enabled, route_if_enabled

# Identity string agents use in their custom control sequence:
#   OSC 1337 ; Custom=id=spawnterm : <json payload> ST
CUSTOM_SEQUENCE_IDENTITY = "spawnterm"
# Capture the whole payload; parse_envelope validates it.
CUSTOM_SEQUENCE_REGEX = r"(.*)"


class DaemonAdapter:
    """Bridges iTerm2 monitors to the pure registry. Owns no logic of its own
    beyond translation and logging."""

    def __init__(
        self,
        connection,
        registry: Registry,
        logger: logging.Logger | None = None,
        identity: str = CUSTOM_SEQUENCE_IDENTITY,
    ) -> None:
        self.connection = connection
        self.registry = registry
        self.log = logger or logging.getLogger("spawnterm.daemon")
        self.identity = identity
        # session_id -> asyncio.Task running that session's prompt monitor.
        self._prompt_tasks: dict[str, asyncio.Task] = {}

    # -- entry point ------------------------------------------------------

    async def run(self) -> None:
        """Seed the registry from live state, then run all monitors forever."""
        import iterm2  # lazy: keep the top-level import iterm2-free.

        app = await iterm2.async_get_app(self.connection)
        await self._seed_from_app(app)
        self.log.info("seeded registry with %d live session(s)", len(self.registry))

        await asyncio.gather(
            self._watch_new_sessions(),
            self._watch_terminated_sessions(),
            self._watch_custom_sequences(),
        )

    # -- startup seeding --------------------------------------------------

    async def _seed_from_app(self, app) -> None:
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    await self._register_session(session)

    async def _register_session(self, session) -> None:
        title, cwd, agent_vars = await self._snapshot(session)
        self.registry.add(session.session_id, title=title, cwd=cwd, **agent_vars)
        self._start_prompt_monitor(session.session_id)

    async def _snapshot(self, session) -> tuple[str, str, dict]:
        """Read title, cwd, and the dot-free agent_* user vars for a session.

        Defensive: any read failure degrades to an empty value rather than
        raising, so one odd session never aborts seeding.
        """
        title = await self._safe_var(session, "name")
        cwd = await self._safe_var(session, "path")
        agent_vars = {}
        for key in AGENT_VAR_KEYS:
            value = await self._safe_var(session, f"user.{key}")
            if value:
                agent_vars[key] = value
        return title or "", cwd or "", agent_vars

    async def _safe_var(self, session, name: str):
        try:
            return await session.async_get_variable(name)
        except Exception as exc:  # noqa: BLE001 - never let a bad read crash us
            self.log.debug("variable read failed (%s): %s", name, exc)
            return None

    # -- lifecycle monitors ----------------------------------------------

    async def _watch_new_sessions(self) -> None:
        import iterm2

        async with iterm2.NewSessionMonitor(self.connection) as monitor:
            while True:
                session_id = await monitor.async_get()
                await self._on_new_session(session_id)

    async def _on_new_session(self, session_id: str) -> None:
        import iterm2

        app = await iterm2.async_get_app(self.connection)
        session = app.get_session_by_id(session_id)
        if session is None:
            # Register the bare id; details fill in on later events.
            self.registry.add(session_id)
        else:
            await self._register_session(session)
        self.log.info("new_session %s (registry size %d)", session_id, len(self.registry))

    async def _watch_terminated_sessions(self) -> None:
        import iterm2

        async with iterm2.SessionTerminationMonitor(self.connection) as monitor:
            while True:
                session_id = await monitor.async_get()
                self._on_terminate_session(session_id)

    def _on_terminate_session(self, session_id: str) -> None:
        removed = self.registry.remove(session_id)
        self._stop_prompt_monitor(session_id)
        self.log.info(
            "terminate_session %s (removed=%s, registry size %d)",
            session_id,
            removed,
            len(self.registry),
        )

    # -- custom escape sequence ingest -----------------------------------

    async def _watch_custom_sequences(self) -> None:
        import iterm2

        async with iterm2.CustomControlSequenceMonitor(
            self.connection, self.identity, CUSTOM_SEQUENCE_REGEX
        ) as monitor:
            while True:
                match = await monitor.async_get()
                payload = match.group(1) if match and match.groups() else ""
                await self._ingest(payload)

    async def _ingest(self, payload: str) -> None:
        """Parse, structured-log, then best-effort route one agent envelope (#28)."""
        result = parse_envelope(payload)
        if not result.ok:
            self.log.warning("ingest: dropped malformed envelope: %s", result.error)
            return
        env = result.envelope
        self.log.info(
            "ingest: v=%d type=%s from=%s to=%s known_type=%s body=%r",
            env.v,
            env.type,
            env.sender,
            env.to,
            env.known_type,
            env.body,
        )
        # Routing (#28): gated on spawnterm.messaging (default OFF). When the
        # flag is OFF we still parsed/logged above but do not route. Best-effort
        # only — no durability/replay/ack/ordering (that is Tier 2 / #4).
        decision = route_if_enabled(env, self.registry, enabled=messaging_enabled())
        if not decision.deliverable:
            self.log.info("route: undeliverable (%s)", decision.reason)
            return
        await self._deliver(decision)

    async def _deliver(self, decision) -> None:
        """Inject a deliverable decision's text into each target session."""
        import iterm2

        app = await iterm2.async_get_app(self.connection)
        for session_id in decision.target_session_ids:
            session = app.get_session_by_id(session_id)
            if session is None:
                self.log.info("route: target %s gone; message lost", session_id)
                continue
            await session.async_send_text(decision.text)
            self.log.info(
                "route: delivered to %s (matched_by=%s)",
                session_id,
                decision.matched_by,
            )

    # -- prompt (idle) monitors ------------------------------------------

    def _start_prompt_monitor(self, session_id: str) -> None:
        if session_id in self._prompt_tasks:
            return
        task = asyncio.ensure_future(self._prompt_loop(session_id))
        self._prompt_tasks[session_id] = task

    def _stop_prompt_monitor(self, session_id: str) -> None:
        task = self._prompt_tasks.pop(session_id, None)
        if task is not None:
            task.cancel()

    async def _prompt_loop(self, session_id: str) -> None:
        import iterm2

        try:
            async with iterm2.PromptMonitor(self.connection, session_id) as monitor:
                while True:
                    await monitor.async_get()
                    # Reaching a shell prompt => the agent is idle / awaiting input.
                    self.registry.set_idle(session_id, True)
                    self.log.info("prompt: session %s idle (awaiting input)", session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a dead session shouldn't crash us
            self.log.debug("prompt monitor ended for %s: %s", session_id, exc)

    # -- spawn (Tier 1.2, #27) -------------------------------------------
    # Self-contained block: executes a pure spawn.SpawnPlan by opening a tagged
    # agent tab. iterm2 is imported lazily here too. The plan (cwd + ordered
    # dot-free user.agent_* assignments) is computed by the pure spawn module;
    # this method only does the iTerm2 I/O.

    async def spawn_agent(self, plan, command: str):
        """Open a new tab running ``command`` in ``plan.cwd`` and stamp identity.

        Creates the tab in the current terminal window (or a fresh window if
        none is open), inheriting/overriding cwd via a write-only profile
        customization, then applies each ``(name, value)`` in ``plan.variables``
        with ``async_set_variable``. The names are dot-free ``user.agent_*``
        keys; when the ``spawnterm.status_board`` gate was OFF the caller built
        an empty variable list, so the tab spawns untagged. Returns the new
        session.
        """
        import iterm2  # lazy: keep the top-level import iterm2-free.

        app = await iterm2.async_get_app(self.connection)
        customizations = iterm2.LocalWriteOnlyProfile()
        if plan.cwd:
            customizations.set_working_directory(plan.cwd)

        window = app.current_terminal_window
        if window is None:
            window = await iterm2.Window.async_create(
                self.connection,
                command=command,
                profile_customizations=customizations,
            )
            session = window.current_tab.current_session
        else:
            tab = await window.async_create_tab(
                command=command,
                profile_customizations=customizations,
            )
            session = tab.current_session

        for name, value in plan.variables:
            await session.async_set_variable(name, value)

        self.log.info(
            "spawned agent session %s in %s (tagged=%s, %d var(s))",
            getattr(session, "session_id", "?"),
            plan.cwd,
            plan.tagged,
            len(plan.variables),
        )
        return session

#!/usr/bin/env python3
"""Thin iTerm2 I/O adapter for the it2agent daemon (Tier 1.1, #26).

This is the ONLY daemon module that talks to iTerm2. It imports ``iterm2``
**lazily** (inside methods, never at module top-level) so the pure logic
modules (``registry``, ``envelope``) — and this file — import fine in an
environment without the ``iterm2`` package. All state lives in the pure
:class:`registry.Registry`; the adapter only translates iTerm2 events into
registry calls and logs ingested envelopes.

Wiring:
  * ``NewSessionMonitor`` / ``SessionTerminationMonitor`` → registry add/remove
    **and** the durable broker registry (``register`` / ``touch``, #36) via the
    bridge.
  * ``CustomControlSequenceMonitor(identity="it2agent")`` → envelope ingest,
    handed to the #37 :mod:`bridge`: durable ``broker send`` when the broker is
    reachable, else the #28 in-memory best-effort route.
  * one ``PromptMonitor`` per session → mark the session idle (awaiting input).
  * a delivery poll loop → drain the durable mailbox into live sessions with
    ack-by-observation (bridge ``deliver_once``), when a broker is present.

Nothing here is exercised by CI (iTerm2 is unavailable); the testable logic is
intentionally pushed down into the pure modules (``bridge`` owns the routing /
mode / ack decisions, unit-tested with a fake broker client and fake screens).
"""

from __future__ import annotations

import asyncio
import logging

from bridge import Bridge, connect_broker
from envelope import parse_envelope
from registry import AGENT_VAR_KEYS, Registry

# How often the delivery loop polls the durable mailbox for live recipients.
DELIVERY_POLL_INTERVAL_SECONDS = 1.0

# Identity string agents use in their custom control sequence:
#   OSC 1337 ; Custom=id=it2agent : <json payload> ST
CUSTOM_SEQUENCE_IDENTITY = "it2agent"
# Capture the whole payload; parse_envelope validates it.
CUSTOM_SEQUENCE_REGEX = r"(.*)"


def build_spawn_customizations(plan):
    """Build the write-only profile customization for a spawned agent tab (#27).

    When ``plan.cwd`` is set, the new session must open in that directory. A
    :class:`iterm2.LocalWriteOnlyProfile` has no ``set_working_directory``; the
    correct API is ``set_custom_directory`` (records the path) paired with
    ``set_initial_directory_mode(..._CUSTOM)`` (makes the new session actually
    open there). iterm2 is imported lazily so the module stays import-clean
    without a running iTerm2 (see the purity test).
    """
    import iterm2  # lazy: keep the top-level import iterm2-free.

    customizations = iterm2.LocalWriteOnlyProfile()
    if plan.cwd:
        customizations.set_custom_directory(plan.cwd)
        customizations.set_initial_directory_mode(
            iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM
        )
    return customizations


def build_launch_command(cwd: str, command: str) -> str:
    """The program string to run as the spawned session (#85).

    iTerm2 IGNORES a profile's custom directory when a ``command`` override is
    supplied to ``async_create_tab`` / ``Window.async_create`` — the custom dir
    only applies to the profile's own login command (proven live: the same
    customization opens in the custom dir WITHOUT a command, but in ``$HOME``
    WITH one). ``build_spawn_customizations`` still sets the custom dir (correct
    for the no-command path and harmless here), but to actually land the agent in
    ``cwd`` we bake the ``cd`` into the command and ``exec`` the agent in it,
    mirroring the AppleScript ``it2agent-spawn`` path. ``exec`` keeps the agent as
    the session's foreground process (no wrapper shell lingering). When ``cwd`` is
    empty the command is returned unchanged. Pure: no iterm2 import.
    """
    if not cwd:
        return command
    import shlex

    inner = f"cd {shlex.quote(cwd)} && exec {command}"
    return f"/bin/sh -lc {shlex.quote(inner)}"


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
        self.log = logger or logging.getLogger("agent.daemon")
        self.identity = identity
        # session_id -> asyncio.Task running that session's prompt monitor.
        self._prompt_tasks: dict[str, asyncio.Task] = {}
        # The #37 daemon↔broker bridge. Constructed in run() (which does the
        # broker connect I/O); None until then, so the spawn path never connects.
        self.bridge: Bridge | None = None

    # -- entry point ------------------------------------------------------

    async def run(self) -> None:
        """Seed the registry from live state, then run all monitors forever."""
        import iterm2  # lazy: keep the top-level import iterm2-free.

        self._setup_bridge()

        app = await iterm2.async_get_app(self.connection)
        await self._seed_from_app(app)
        self.log.info("seeded registry with %d live session(s)", len(self.registry))

        coros = [
            self._watch_new_sessions(),
            self._watch_terminated_sessions(),
            self._watch_custom_sequences(),
        ]
        # Only drive the delivery poll loop when a broker is present; otherwise
        # there is no durable mailbox to drain (in-memory delivery is inline).
        if self.bridge is not None and self.bridge.broker is not None:
            coros.append(self._delivery_loop())
        await asyncio.gather(*coros)

    def _setup_bridge(self) -> None:
        """Best-effort connect to the broker and build the bridge (#37).

        A missing/down broker yields a bridge with no client — the daemon then
        runs the #28 in-memory best-effort relay. Never raises.
        """
        broker = connect_broker(self.log)
        self.bridge = Bridge(
            broker,
            self.registry,
            send_text=self._send_text,
            read_screen=self._read_screen,
            logger=self.log,
        )

    # -- startup seeding --------------------------------------------------

    async def _seed_from_app(self, app) -> None:
        for window in app.terminal_windows:
            for tab in window.tabs:
                for session in tab.sessions:
                    await self._register_session(session)

    async def _register_session(self, session) -> None:
        title, cwd, agent_vars = await self._snapshot(session)
        record = self.registry.add(session.session_id, title=title, cwd=cwd, **agent_vars)
        self._start_prompt_monitor(session.session_id)
        # Reflect the live session (role/task/liveness) into the durable broker
        # registry (#36). No-op unless the broker flag is on and reachable.
        if self.bridge is not None:
            self.bridge.note_session(record)

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
            record = self.registry.add(session_id)
            if self.bridge is not None:
                self.bridge.note_session(record)
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
        # Mark it not-alive in the durable broker registry (#36). No-op when the
        # broker is off/unreachable.
        if self.bridge is not None:
            self.bridge.note_terminated(session_id)
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
        """Parse, structured-log, then hand the envelope to the #37 bridge.

        The bridge decides durable (broker ``send``) vs in-memory (#28 route),
        gated on ``agent.messaging`` + ``agent.broker`` (see bridge). We
        still parse/log every envelope here regardless of the flags.
        """
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
        if self.bridge is None:  # defensive: run() always builds it before ingest.
            self.log.warning("ingest: bridge not initialized; dropping")
            return
        outcome = await self.bridge.handle_ingest(env)
        self.log.info(
            "ingest: mode=%s action=%s reason=%s msg_id=%s targets=%s degraded=%s",
            outcome.mode,
            outcome.action,
            outcome.reason,
            outcome.msg_id,
            outcome.targets,
            outcome.degraded,
        )

    # -- delivery poll loop (durable mailbox → live sessions) ------------

    async def _delivery_loop(self) -> None:
        """Periodically drain the durable mailbox into live sessions (#37).

        Each tick is a bridge ``deliver_once`` sweep (poll → inject → observe →
        ack). Runs only when a broker is present; a per-tick failure is logged
        and the loop continues (the daemon must never crash because the broker
        is down). The sleep is a poll cadence, not a concurrency workaround.
        """
        while True:
            try:
                outcome = await self.bridge.deliver_once()
                if outcome.delivered or outcome.acked:
                    self.log.info(
                        "delivery: polled=%d delivered=%d acked=%d degraded=%s",
                        outcome.polled,
                        outcome.delivered,
                        outcome.acked,
                        outcome.degraded,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - a sweep bug must not kill the loop
                self.log.debug("delivery: sweep failed: %s", exc)
            await asyncio.sleep(DELIVERY_POLL_INTERVAL_SECONDS)

    # -- injected iTerm2 I/O for the bridge (the only iTerm2 touch here) --

    async def _send_text(self, session_id: str, text: str) -> None:
        """Inject ``text`` into ``session_id`` (bridge send callable)."""
        import iterm2

        app = await iterm2.async_get_app(self.connection)
        session = app.get_session_by_id(session_id)
        if session is None:
            self.log.info("deliver: target %s gone; message not sent", session_id)
            return
        await session.async_send_text(text)
        self.log.info("deliver: sent to %s", session_id)

    async def _read_screen(self, session_id: str) -> str:
        """Return ``session_id``'s visible screen text (bridge observe callable).

        Defensive: a gone session or a read failure yields ``""`` (not observed)
        rather than raising, so ack-by-observation simply declines to ack.
        """
        import iterm2

        try:
            app = await iterm2.async_get_app(self.connection)
            session = app.get_session_by_id(session_id)
            if session is None:
                return ""
            contents = await session.async_get_screen_contents()
            lines = []
            for i in range(contents.number_of_lines):
                lines.append(contents.line(i).string)
            return "\n".join(lines)
        except Exception as exc:  # noqa: BLE001 - a bad read => not observed, never crash
            self.log.debug("deliver: screen read failed for %s: %s", session_id, exc)
            return ""

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
        keys; when the ``agent.status_board`` gate was OFF the caller built
        an empty variable list, so the tab spawns untagged. Returns the new
        session.
        """
        import iterm2  # lazy: keep the top-level import iterm2-free.

        app = await iterm2.async_get_app(self.connection)
        customizations = build_spawn_customizations(plan)
        # #85: a `command` override defeats the profile's custom dir, so bake the
        # cd into the command to actually land the agent in plan.cwd.
        launch_command = build_launch_command(plan.cwd, command)

        window = app.current_terminal_window
        if window is None:
            window = await iterm2.Window.async_create(
                self.connection,
                command=launch_command,
                profile_customizations=customizations,
            )
            session = window.current_tab.current_session
        else:
            tab = await window.async_create_tab(
                command=launch_command,
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

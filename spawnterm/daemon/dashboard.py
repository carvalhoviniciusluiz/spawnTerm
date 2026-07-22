#!/usr/bin/env python3
"""Custom iTerm2 status-bar component: per-session agent dashboard (Tier 1.4, #29).

Split, like the rest of the daemon, into a **pure** formatting core and a thin
iTerm2 wiring layer:

  * The pure core (:func:`style_for_status`, :func:`format_component`, and the
    :data:`PALETTE` mapping) has **no** ``iterm2`` import and no I/O, so it
    imports and unit-tests without the ``iterm2`` package. Given a session's
    ``agent_role`` / ``agent_status`` / ``agent_task`` (and the daemon's idle
    flag) it returns the compact string shown in the status bar plus a
    status→glyph/color choice.
  * The wiring (:func:`build_component`, :func:`register_dashboard`,
    :func:`maybe_register_dashboard`) imports ``iterm2`` **lazily**, inside the
    functions that need it — never at module top level.

Palette: the four lifecycle colors are the colorblind-safe Okabe-Ito values
defined once for the emitter in ``spawnterm/emit/docs/colors.md`` (#8) and reused
verbatim here for visual consistency. Each status also gets a shape-distinct
glyph so the state survives grayscale / color-vision deficiency without relying
on hue alone (triangle / warning / check / circle).

Gate: the component is registered only when the ``spawnterm.status_board``
feature flag is ON (default OFF). This reuses the shared ``spawnterm-flag``
helper (#11) — the same mechanism the daemon skeleton uses — rather than forking
a new flag. When OFF (or the helper is unreachable) the component is not
registered and nothing is shown. Bypass for local testing with
``SPAWNTERM_FORCE=1``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Pure formatting core (NO iterm2 import below this line — keep it importable
# in plain CI).
# ---------------------------------------------------------------------------

# The four lifecycle statuses. `idle` doubles as the neutral fallback for a
# missing or unrecognized status.
BUSY = "busy"
BLOCKED = "blocked"
DONE = "done"
IDLE = "idle"


@dataclass(frozen=True)
class StatusStyle:
    """The visual treatment for one lifecycle status.

    ``color`` is a 6-digit hex string with no leading ``#`` (matching the
    emitter's ``SetColors`` bytes). ``glyph`` is a single shape-distinct
    indicator so the status reads without color.
    """

    status: str
    color: str
    glyph: str


# status -> (hex color, glyph). Colors are the Okabe-Ito lifecycle palette from
# spawnterm/emit/docs/colors.md (#8): busy blue, blocked orange, done bluish
# green, idle neutral gray. Glyphs are shape-distinct for CVD/grayscale safety.
PALETTE: dict[str, StatusStyle] = {
    BUSY: StatusStyle(BUSY, "0072B2", "▶"),
    BLOCKED: StatusStyle(BLOCKED, "E69F00", "⚠"),
    DONE: StatusStyle(DONE, "009E73", "✓"),
    IDLE: StatusStyle(IDLE, "999999", "○"),
}

# Neutral fallback used when the status is missing or unrecognized.
FALLBACK_STATUS = IDLE

# Role shown when the session has no agent_role user var yet.
DEFAULT_ROLE = "agent"

# Compact by default: an iTerm2 status-bar cell is narrow. The task is appended
# only if it fits, and truncated with an ellipsis otherwise.
DEFAULT_MAX_LENGTH = 40
_TASK_SEP = " — "
_ELLIPSIS = "…"
# Don't bother appending a task we can only show a sliver of.
_MIN_TASK_CHARS = 4


def resolve_status(agent_status: str | None, idle: bool = False) -> str:
    """Normalize a raw ``agent_status`` (with the daemon idle flag) to one of
    the four known lifecycle keys.

    An explicitly reported busy/blocked/done wins — the agent said so. A missing
    or unrecognized status falls back to ``idle`` (the neutral state), which is
    also what the idle flag reinforces when the agent has not reported anything.
    """
    normalized = (agent_status or "").strip().lower()
    if normalized in PALETTE:
        return normalized
    # Missing/unknown status: neutral. The idle flag doesn't change the visible
    # result here (fallback is already idle) but is accepted so callers can pass
    # the registry's idle bool through uniformly.
    return FALLBACK_STATUS


def style_for_status(agent_status: str | None, idle: bool = False) -> StatusStyle:
    """Return the :class:`StatusStyle` (status/color/glyph) for a raw status."""
    return PALETTE[resolve_status(agent_status, idle)]


def _truncate_task(task: str, budget: int) -> str | None:
    """Fit ``task`` into ``budget`` characters, using an ellipsis if trimmed.

    Returns None when the budget is too small to show a meaningful sliver.
    """
    if budget < _MIN_TASK_CHARS:
        return None
    if len(task) <= budget:
        return task
    return task[: budget - len(_ELLIPSIS)].rstrip() + _ELLIPSIS


def format_component(
    agent_role: str | None,
    agent_status: str | None,
    agent_task: str | None = None,
    *,
    idle: bool = False,
    max_length: int = DEFAULT_MAX_LENGTH,
) -> str:
    """Build the compact status-bar string for one session.

    Layout: ``{glyph} {role}: {status}`` with ``{sep}{task}`` appended when the
    task both exists and fits within ``max_length`` (truncated with ``…`` if it
    would nearly fit, omitted entirely if there is no room). Missing role
    degrades to ``agent``; missing/unknown status degrades to ``idle``.
    """
    style = style_for_status(agent_status, idle)
    role = (agent_role or "").strip() or DEFAULT_ROLE
    base = f"{style.glyph} {role}: {style.status}"

    task = (agent_task or "").strip()
    if not task:
        return base

    budget = max_length - len(base) - len(_TASK_SEP)
    fitted = _truncate_task(task, budget)
    if fitted is None:
        return base
    return base + _TASK_SEP + fitted


# ---------------------------------------------------------------------------
# Feature-flag gate (reuses the shared spawnterm-flag helper, #11 — same
# mechanism the daemon skeleton uses; no new flag system).
# ---------------------------------------------------------------------------

STATUS_BOARD_FLAG = "spawnterm.status_board"

# Sibling flags helper: spawnterm/flags/spawnterm_flag.py.
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def status_board_enabled() -> bool:
    """Return True iff the ``spawnterm.status_board`` flag is ON.

    Reuses the #11 ``spawnterm_flag.is_enabled`` helper. Fail-safe: if the
    helper is unreachable, treat the flag as OFF (capabilities default off).
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import spawnterm_flag  # type: ignore

        return spawnterm_flag.is_enabled(STATUS_BOARD_FLAG)
    except Exception:  # noqa: BLE001 - default off when the helper is missing
        return False


def dashboard_gate_open() -> bool:
    """Whether the dashboard component should be registered.

    ON when the ``spawnterm.status_board`` flag is set, or bypassed locally with
    ``SPAWNTERM_FORCE=1`` (matching the daemon's gate bypass).
    """
    if os.environ.get("SPAWNTERM_FORCE") == "1":
        return True
    return status_board_enabled()


# ---------------------------------------------------------------------------
# iTerm2 wiring (lazy `import iterm2` inside every function below).
# ---------------------------------------------------------------------------

COMPONENT_IDENTIFIER = "com.spawnterm.agent-dashboard"
COMPONENT_SHORT_DESCRIPTION = "spawnTerm Agent"
COMPONENT_DETAILED_DESCRIPTION = (
    "Shows this session's agent role and lifecycle status "
    "(and task if it fits), colored with the spawnTerm lifecycle palette."
)
COMPONENT_EXEMPLAR = "▶ backend: busy — build #29"

# The user variables the component depends on (dot-free names surfaced by the
# API under the `user.` namespace). The component re-renders whenever any of
# these change — this is the variable-knob dependency set.
DEPENDENT_VARIABLES = ("user.agent_role", "user.agent_status", "user.agent_task")


def build_component(iterm2):
    """Construct (but do not register) the ``iterm2.StatusBarComponent``.

    ``iterm2`` is passed in so the caller owns the lazy import; this keeps the
    module top level free of ``iterm2`` while remaining trivially testable via a
    stub. Returns ``(component, callback)``.
    """

    @iterm2.StatusBarRPC
    async def dashboard_callback(
        knobs,
        role=iterm2.Reference("user.agent_role?"),
        status=iterm2.Reference("user.agent_status?"),
        task=iterm2.Reference("user.agent_task?"),
    ):
        # The `?` suffix makes each reference optional (None when undefined) and
        # registers it as a dependency, so iTerm2 re-invokes this on any change.
        return format_component(role, status, task)

    component = iterm2.StatusBarComponent(
        short_description=COMPONENT_SHORT_DESCRIPTION,
        detailed_description=COMPONENT_DETAILED_DESCRIPTION,
        knobs=[],
        exemplar=COMPONENT_EXEMPLAR,
        update_cadence=None,
        identifier=COMPONENT_IDENTIFIER,
    )
    return component, dashboard_callback


async def register_dashboard(connection, logger=None) -> bool:
    """Register the dashboard status-bar component on ``connection``.

    Lazy-imports ``iterm2``. Returns True on success. Defensive: a registration
    failure is logged and swallowed (the daemon must keep running).
    """
    import iterm2  # lazy: keep the module top-level import iterm2-free.

    component, callback = build_component(iterm2)
    try:
        await component.async_register(connection, callback)
    except Exception as exc:  # noqa: BLE001 - never let this crash the daemon
        if logger is not None:
            logger.warning("dashboard: component registration failed: %s", exc)
        return False
    if logger is not None:
        logger.info("dashboard: registered status-bar component %s", COMPONENT_IDENTIFIER)
    return True


async def maybe_register_dashboard(connection, logger=None) -> bool:
    """Register the dashboard **iff** the ``spawnterm.status_board`` gate is open.

    This is the single hook the daemon calls. When the gate is OFF it logs a
    debug line and does nothing (the component is never registered / shows
    nothing). Returns True iff the component was registered.
    """
    if not dashboard_gate_open():
        if logger is not None:
            logger.debug(
                "dashboard: '%s' is OFF; not registering the status-bar component.",
                STATUS_BOARD_FLAG,
            )
        return False
    return await register_dashboard(connection, logger)

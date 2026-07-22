#!/usr/bin/env python3
"""spawnTerm cost dashboard — status-bar surface (spawnTerm #16).

Mirrors the #29 daemon status-bar pattern (``spawnterm/daemon/dashboard.py``):
a **pure** formatting core with no ``iterm2`` import, plus a thin wiring layer
that lazy-imports ``iterm2`` inside the functions that need it. This is the
"show it on the status board" surface for the cost dashboard; the CLI
(``spawnterm-cost``) is the full-table surface.

Pure core: :func:`format_status_line` turns an :class:`~costlib.Aggregation`
(plus optional idle-burn/cap-breach findings) into one compact status-bar
string, e.g. ``Σ $12.34 · 5 agents · ⚠ 1 idle · ! 1 over-cap``.

Gate: registered only when ``spawnterm.cost_dashboard`` is ON (default OFF),
reusing the shared #11 flag helper — the same mechanism the #29 dashboard uses.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Pure formatting core (NO iterm2 import below this line).
# ---------------------------------------------------------------------------

_SEP = " · "
IDLE_GLYPH = "⚠"  # idle-burn present
CAP_GLYPH = "!"  # a soft cap is breached


def format_money(amount: float) -> str:
    """Format a USD estimate compactly (``$12.34``; ``$1.2k`` past a thousand)."""
    if amount >= 1000:
        return f"${amount / 1000:.1f}k"
    return f"${amount:.2f}"


def format_status_line(
    aggregation,
    *,
    idle_burn: dict | None = None,
    breaches: list | None = None,
) -> str:
    """Build the compact status-bar string summarizing fleet cost.

    Layout: ``Σ {total_cost} · {n} agents`` with ``· ⚠ {k} idle`` appended when
    any agent is idle-burning and ``· ! {m} over-cap`` when any soft cap is
    breached. Cost is the estimate from the configured price table.
    """
    total = format_money(aggregation.total.cost_usd)
    n_agents = len(aggregation.agents)
    parts = [f"Σ {total}", f"{n_agents} agent" + ("" if n_agents == 1 else "s")]

    if idle_burn:
        parts.append(f"{IDLE_GLYPH} {len(idle_burn)} idle")
    if breaches:
        parts.append(f"{CAP_GLYPH} {len(breaches)} over-cap")

    return _SEP.join(parts)


# ---------------------------------------------------------------------------
# Feature-flag gate (reuses the shared spawnterm-flag helper, #11).
# ---------------------------------------------------------------------------

COST_FLAG = "spawnterm.cost_dashboard"

_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"


def cost_dashboard_enabled() -> bool:
    """Return True iff ``spawnterm.cost_dashboard`` is ON (fail-safe: OFF)."""
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import spawnterm_flag  # type: ignore

        return spawnterm_flag.is_enabled(COST_FLAG)
    except Exception:  # noqa: BLE001 - default off when the helper is missing
        return False


def board_gate_open() -> bool:
    """Whether the cost status-bar component should register (SPAWNTERM_FORCE=1 bypass)."""
    if os.environ.get("SPAWNTERM_FORCE") == "1":
        return True
    return cost_dashboard_enabled()


# ---------------------------------------------------------------------------
# iTerm2 wiring (lazy `import iterm2` inside every function below).
# ---------------------------------------------------------------------------

COMPONENT_IDENTIFIER = "com.spawnterm.cost-dashboard"
COMPONENT_SHORT_DESCRIPTION = "spawnTerm Cost"
COMPONENT_DETAILED_DESCRIPTION = (
    "Shows the estimated total spend across the agent fleet (from Claude Code "
    "token logs and the configured price table), the agent count, and idle-burn "
    "/ soft-cap flags."
)
COMPONENT_EXEMPLAR = "Σ $12.34 · 5 agents · ⚠ 1 idle"


def compute_summary_line(source: str | None = None, group_by: str | None = None) -> str:
    """Recompute the status line from the log source (thin I/O over the CLI core).

    Imports the CLI's collection helpers lazily so this module's top level stays
    iterm2-free *and* costlib-only. Defensive: any failure yields a short
    fallback string rather than raising into the status bar.
    """
    try:
        here = Path(__file__).resolve().parent
        if str(here) not in sys.path:
            sys.path.insert(0, str(here))
        import costlib  # type: ignore
        import cost_cli  # type: ignore

        src = source or cost_cli.default_source()
        gb = group_by or costlib.DEFAULT_GROUP_BY
        entries = list(cost_cli.collect_entries(src))
        prices = costlib.DEFAULT_PRICES
        aggregation = costlib.aggregate(entries, prices, group_by=gb)
        idle = costlib.detect_idle_burn(entries, prices, group_by=gb)
        return format_status_line(aggregation, idle_burn=idle)
    except Exception:  # noqa: BLE001 - never crash the status bar
        return "Σ $— · cost"


def build_component(iterm2, source: str | None = None, group_by: str | None = None):
    """Construct (but do not register) the cost ``iterm2.StatusBarComponent``.

    A cadence-driven component: iTerm2 calls the callback on ``update_cadence``
    and we recompute from the logs. Returns ``(component, callback)``.
    """

    @iterm2.StatusBarRPC
    async def cost_callback(knobs):
        return compute_summary_line(source, group_by)

    component = iterm2.StatusBarComponent(
        short_description=COMPONENT_SHORT_DESCRIPTION,
        detailed_description=COMPONENT_DETAILED_DESCRIPTION,
        knobs=[],
        exemplar=COMPONENT_EXEMPLAR,
        update_cadence=30,  # seconds; logs change slowly
        identifier=COMPONENT_IDENTIFIER,
    )
    return component, cost_callback


async def register_cost_board(connection, logger=None, source=None, group_by=None) -> bool:
    """Register the cost status-bar component. Failures are logged and swallowed."""
    import iterm2  # lazy: keep this module's top level iterm2-free.

    component, callback = build_component(iterm2, source=source, group_by=group_by)
    try:
        await component.async_register(connection, callback)
    except Exception as exc:  # noqa: BLE001 - never crash the daemon
        if logger is not None:
            logger.warning("cost_board: registration failed: %s", exc)
        return False
    if logger is not None:
        logger.info("cost_board: registered status-bar component %s", COMPONENT_IDENTIFIER)
    return True


async def maybe_register_cost_board(connection, logger=None, source=None, group_by=None) -> bool:
    """Register the cost board **iff** ``spawnterm.cost_dashboard`` is ON."""
    if not board_gate_open():
        if logger is not None:
            logger.debug("cost_board: '%s' is OFF; not registering.", COST_FLAG)
        return False
    return await register_cost_board(connection, logger, source=source, group_by=group_by)

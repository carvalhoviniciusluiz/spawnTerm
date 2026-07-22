#!/usr/bin/env python3
"""spawnTerm cost dashboard — CLI + I/O layer (spawnTerm #16).

This is the thin I/O shell around the pure :mod:`costlib` core. It:

* discovers Claude Code transcript JSONL under a configurable source
  (default ``~/.claude/projects/``; override via ``--source`` or
  ``$SPAWNTERM_COST_SOURCE``);
* reads the price table (default table in :mod:`costlib`, override via
  ``--prices`` / ``$SPAWNTERM_COST_PRICES`` pointing at a JSON file);
* aggregates per agent + total, estimates cost, detects idle-burn, evaluates
  soft caps — all by calling pure functions in :mod:`costlib`;
* renders a table (or ``--json``), flags idle-burn, and — on a soft-cap breach
  or idle-burn — prints a warning and optionally fires ``spawnterm-emit
  attention`` (``--notify``).

Gating: no-op (exit 0) unless ``spawnterm.cost_dashboard`` is ON, matching the
``spawnterm-emit`` convention (``--no-gate`` / ``SPAWNTERM_FORCE=1`` bypass).

The executable entry point is the sibling ``spawnterm-cost`` script.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import costlib  # noqa: E402

PROG = "spawnterm-cost"
FLAG_KEY = "spawnterm.cost_dashboard"
DEFAULT_SOURCE = "~/.claude/projects"

_FLAGS_DIR = _HERE.parent / "flags"


# ---------------------------------------------------------------------------
# Feature-flag gate (reuses the shared #11 helper).
# ---------------------------------------------------------------------------


def gate_open(no_gate: bool) -> bool:
    """Return True iff the capability is enabled (or gating is bypassed)."""
    if os.environ.get("SPAWNTERM_FORCE") == "1":
        return True
    if no_gate:
        return True
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import spawnterm_flag  # type: ignore

        return spawnterm_flag.is_enabled(FLAG_KEY)
    except Exception:  # noqa: BLE001 - fail-safe: OFF when the helper is missing
        return False


# ---------------------------------------------------------------------------
# Source discovery + entry collection (the only file I/O in the whole feature).
# ---------------------------------------------------------------------------


def default_source() -> str:
    """The log source: ``$SPAWNTERM_COST_SOURCE`` or the Claude default dir."""
    return os.environ.get("SPAWNTERM_COST_SOURCE") or DEFAULT_SOURCE


def _resolve(path: str) -> Path:
    return Path(path).expanduser()


def iter_log_files(source: str):
    """Yield ``(path, project)`` for each ``*.jsonl`` under ``source``.

    ``project`` is the top-level directory name under a projects-root source
    (the ``~/.claude/projects/<project>`` bucket), or None when ``source`` is a
    single file. Missing sources yield nothing (not an error — an empty fleet).
    """
    root = _resolve(source)
    if root.is_file():
        yield root, None
        return
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*.jsonl")):
        try:
            rel = path.relative_to(root)
            project = rel.parts[0] if len(rel.parts) > 1 else None
        except ValueError:
            project = None
        yield path, project


def collect_entries(source: str):
    """Read and parse every usage-bearing line under ``source``.

    Unreadable files are skipped defensively (a locked/half-written transcript
    must not abort the run). Bad lines within a file are skipped by
    :func:`costlib.iter_entries`.
    """
    for path, project in iter_log_files(source):
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                yield from costlib.iter_entries(handle, project=project)
        except OSError:
            continue


def load_prices(prices_path: str | None):
    """Load the price table, merging a JSON override file if given/available.

    Precedence: explicit ``--prices`` > ``$SPAWNTERM_COST_PRICES`` > defaults.
    A missing or malformed file falls back to the defaults (with a stderr note).
    """
    path = prices_path or os.environ.get("SPAWNTERM_COST_PRICES")
    if not path:
        return costlib.DEFAULT_PRICES
    resolved = _resolve(path)
    try:
        with resolved.open("r", encoding="utf-8") as handle:
            overrides = json.load(handle)
    except (OSError, ValueError) as exc:
        print(f"{PROG}: cannot read price file {resolved} ({exc}); using defaults", file=sys.stderr)
        return costlib.DEFAULT_PRICES
    return costlib.load_price_table(overrides)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def human_tokens(n: int) -> str:
    """Compact token count: ``1.2M`` / ``27.0k`` / ``940``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def money(amount: float) -> str:
    return f"${amount:.2f}"


def render_table(aggregation, idle_burn, breaches) -> str:
    """Render the per-agent + total table as plain text (no color, pipe-safe)."""
    idle_burn = idle_burn or {}
    over_cap = {
        b.scope.split("agent:", 1)[1] for b in (breaches or []) if b.scope.startswith("agent:")
    }

    header = ["", "AGENT", "TURNS", "INPUT", "OUTPUT", "CACHE-W", "CACHE-R", "TOTAL", "COST(est)"]
    rows = [header]

    for row in aggregation.sorted_agents():
        flag = ""
        if row.agent in idle_burn:
            flag += "⚠"
        if row.agent in over_cap:
            flag += "!"
        rows.append(
            [
                flag,
                row.agent,
                str(row.entries),
                human_tokens(row.tokens.input),
                human_tokens(row.tokens.output),
                human_tokens(row.tokens.cache_creation),
                human_tokens(row.tokens.cache_read),
                human_tokens(row.tokens.total),
                money(row.cost_usd),
            ]
        )

    total = aggregation.total
    total_flag = "!" if any(b.scope == "total" for b in (breaches or [])) else ""
    rows.append(
        [
            total_flag,
            "TOTAL",
            str(total.entries),
            human_tokens(total.tokens.input),
            human_tokens(total.tokens.output),
            human_tokens(total.tokens.cache_creation),
            human_tokens(total.tokens.cache_read),
            human_tokens(total.tokens.total),
            money(total.cost_usd),
        ]
    )

    widths = [max(len(r[i]) for r in rows) for i in range(len(header))]
    lines = []
    for idx, row in enumerate(rows):
        cells = [row[0].ljust(widths[0])] + [
            row[i].ljust(widths[i]) if i == 1 else row[i].rjust(widths[i])
            for i in range(1, len(header))
        ]
        lines.append("  ".join(cells).rstrip())
        if idx == 0:
            lines.append("-" * len(lines[-1]))
        if idx == len(rows) - 2:  # separator before the TOTAL row
            lines.append("-" * len(lines[-1]))

    lines.append("")
    lines.append(f"grouped by: {aggregation.group_by}   (cost is an ESTIMATE from the configured price table)")
    if idle_burn:
        detail = ", ".join(
            f"{a} ({human_tokens(v.idle_tokens)} tok / {money(v.idle_cost_usd)})"
            for a, v in sorted(idle_burn.items())
        )
        lines.append(f"⚠ idle-burn: {detail}")
    if breaches:
        for b in breaches:
            lines.append(f"! soft cap: {b.scope} at {money(b.actual_usd)} exceeds {money(b.limit_usd)}")
    return "\n".join(lines)


def render_json(aggregation, idle_burn, breaches) -> str:
    """Render the full result as JSON (numbers as-parsed, cost as estimate)."""

    def agent_obj(row):
        return {
            "agent": row.agent,
            "turns": row.entries,
            "models": sorted(row.models),
            "tokens": {
                "input": row.tokens.input,
                "output": row.tokens.output,
                "cache_creation": row.tokens.cache_creation,
                "cache_read": row.tokens.cache_read,
                "total": row.tokens.total,
            },
            "cost_usd_estimate": round(row.cost_usd, 6),
        }

    payload = {
        "group_by": aggregation.group_by,
        "cost_is_estimate": True,
        "agents": [agent_obj(r) for r in aggregation.sorted_agents()],
        "total": agent_obj(aggregation.total),
        "idle_burn": [
            {
                "agent": v.agent,
                "idle_tokens": v.idle_tokens,
                "idle_cost_usd_estimate": round(v.idle_cost_usd, 6),
                "idle_bursts": v.idle_bursts,
                "status_idle": v.status_idle,
            }
            for v in idle_burn.values()
        ],
        "soft_cap_breaches": [
            {"scope": b.scope, "limit_usd": b.limit_usd, "actual_usd": round(b.actual_usd, 6)}
            for b in (breaches or [])
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Notification (best-effort; never fatal)
# ---------------------------------------------------------------------------


def notify(messages: list[str]) -> None:
    """Fire ``spawnterm-emit attention`` per message if the emitter is on PATH.

    Best-effort: the guaranteed channel is the stderr warning printed by the
    caller. The emitter honors its own ``spawnterm.status_board`` gate.
    """
    emitter = shutil.which("spawnterm-emit")
    if emitter is None:
        return
    for msg in messages:
        try:
            subprocess.run([emitter, "attention", msg], check=False)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Per-agent + total token/cost dashboard for a spawnTerm agent fleet, "
            "consuming Claude Code transcript JSONL. Cost is an ESTIMATE derived "
            "from the configured price table."
        ),
    )
    parser.add_argument(
        "--source",
        default=None,
        help=f"log source dir or file (default: $SPAWNTERM_COST_SOURCE or {DEFAULT_SOURCE}).",
    )
    parser.add_argument(
        "--group-by",
        choices=costlib.GROUP_KEYS,
        default=costlib.DEFAULT_GROUP_BY,
        help=f"agent-association key (default: {costlib.DEFAULT_GROUP_BY}).",
    )
    parser.add_argument(
        "--prices",
        default=None,
        help="path to a JSON price-table override (default: $SPAWNTERM_COST_PRICES or built-in).",
    )
    parser.add_argument(
        "--idle-gap",
        type=float,
        default=costlib.DEFAULT_IDLE_GAP_SECONDS,
        help=f"seconds of quiet after which resumed spend counts as idle-burn (default: {int(costlib.DEFAULT_IDLE_GAP_SECONDS)}).",
    )
    parser.add_argument("--cap-agent", type=float, default=None, help="soft per-agent USD cap.")
    parser.add_argument("--cap-total", type=float, default=None, help="soft total USD cap.")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="on a breach/idle-burn, also fire spawnterm-emit attention (best-effort).",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table.")
    parser.add_argument(
        "--no-gate", action="store_true", help="bypass the feature-flag gate (local testing)."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))

    if not gate_open(args.no_gate):
        # Gated OFF: no-op. One stderr breadcrumb; nothing on stdout.
        print(f"{PROG}: {FLAG_KEY} is OFF; nothing to show (enable it or pass --no-gate).", file=sys.stderr)
        return 0

    source = args.source or default_source()
    prices = load_prices(args.prices)

    entries = list(collect_entries(source))
    aggregation = costlib.aggregate(entries, prices, group_by=args.group_by)
    idle_burn = costlib.detect_idle_burn(
        entries, prices, group_by=args.group_by, idle_gap_seconds=args.idle_gap
    )
    breaches = costlib.evaluate_soft_caps(
        aggregation, per_agent=args.cap_agent, total=args.cap_total
    )

    if args.json:
        print(render_json(aggregation, idle_burn, breaches))
    else:
        print(render_table(aggregation, idle_burn, breaches))

    # Warnings (guaranteed channel) + optional emitter notification.
    warnings: list[str] = []
    for b in breaches:
        warnings.append(
            f"soft cap breached: {b.scope} at {money(b.actual_usd)} exceeds {money(b.limit_usd)}"
        )
    for a, v in sorted(idle_burn.items()):
        warnings.append(
            f"idle-burn: {a} accrued {human_tokens(v.idle_tokens)} tokens ({money(v.idle_cost_usd)}) while idle"
        )
    for w in warnings:
        print(f"{PROG}: {w}", file=sys.stderr)
    if args.notify and warnings:
        notify([f"spawnterm-cost: {w}" for w in warnings])

    return 0


if __name__ == "__main__":
    sys.exit(main())

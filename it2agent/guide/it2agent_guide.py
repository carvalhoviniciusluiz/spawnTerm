#!/usr/bin/env python3
"""Generate the it2agent capability guide + brief from the live schema (#113).

Single source of truth, always current. Before #113 the guide (``AGENT_GUIDE.md``)
was hand-maintained prose that could drift from the actual feature-flag schema
and MCP tool registry. This module makes the guide **generated** from two live
registries so adding or removing a capability updates the doc automatically:

  * :data:`KNOWN_FLAGS` — the feature-flag schema in ``it2agent/flags/it2agent_flag.py``
    (capability → one-line description). This is what ``it2agent-flag`` toggles.
  * :data:`TOOLS` — the MCP tool registry in ``it2agent/mcp/tools.py`` (tool name,
    description, JSON-Schema). This is what ``it2agent-mcp`` serves.

Two rendered surfaces:

  * ``render_guide()`` → the full ``AGENT_GUIDE.md`` text. ``it2agent guide`` writes
    it; ``it2agent guide --check`` compares the committed file to a fresh render and
    exits non-zero on drift (so CI/tests catch a stale guide). Every reader of the
    guide (``it2agent help``, the MCP ``help`` tool, the spawn header pointer) keeps
    reading the one committed file, which this generator owns.
  * ``render_brief()`` → a short capabilities summary (what is active → how to use →
    pointer to ``it2agent help`` + the MCP tools). ``it2agent brief`` prints it and
    the SessionStart autobrief hook injects it into a fresh Claude's context.

Pure and dependency-light: it reads the two in-repo registries and its own flag
state via the ``it2agent_flag`` helper. No broker, no network, no iterm2.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent  # it2agent/
_FLAGS_DIR = _ROOT / "flags"
_MCP_DIR = _ROOT / "mcp"

for _d in (_FLAGS_DIR, _MCP_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import it2agent_flag  # type: ignore  # noqa: E402

# The committed guide file every reader loads (help CLI, MCP help tool, spawn
# header). This generator is the authority for its contents.
GUIDE_PATH = _ROOT / "AGENT_GUIDE.md"

TITLE = "# it2agent — agent capability guide"


def _load_tools():
    """Return the ordered MCP tool registry (list of ToolSpec), or [] on failure.

    Importing ``tools`` pulls the daemon spawn-plan builder onto sys.path (the
    module does this itself); it imports no socket/iterm2, so it is safe here.
    Degrades to an empty registry only if the package is unreadable — callers
    that must never fail (the autobrief hook) still get a valid guide/brief.
    """
    try:
        import tools  # type: ignore

        return list(tools.TOOLS.values())
    except Exception:  # noqa: BLE001 - a broken import must not break the guide
        return []


# --------------------------------------------------------------------------- #
# Full guide
# --------------------------------------------------------------------------- #


def _flag_rows() -> str:
    rows = []
    for cap, desc in it2agent_flag.KNOWN_FLAGS.items():
        rows.append(f"| `{it2agent_flag.PREFIX}{cap}` | {desc} |")
    return "\n".join(rows)


def _required_args(spec) -> str:
    required = spec.input_schema.get("required") or []
    if not required:
        return "—"
    return ", ".join(f"`{name}`" for name in required)


def _tool_rows(tools) -> str:
    rows = []
    for spec in tools:
        # Collapse the multi-line description to one line for the table cell.
        desc = " ".join(spec.description.split())
        rows.append(f"| `{spec.name}` | {_required_args(spec)} | {desc} |")
    return "\n".join(rows)


def _known_flags_inline() -> str:
    return ", ".join(f"`{cap}`" for cap in it2agent_flag.KNOWN_FLAGS)


def render_guide() -> str:
    """Render the full ``AGENT_GUIDE.md`` text from the live schema + tool registry."""
    tools = _load_tools()
    parts = [
        TITLE,
        "",
        "it2agent turns iTerm2 into a control plane for orchestrating AI coding",
        "agents: spawn agents with identity, a live status board, cross-tab",
        "messaging, durable handoffs, review, cost, and more.",
        "",
        "**Everything is a feature flag and every flag defaults OFF.** A capability",
        "does nothing until you turn it on: `it2agent-flag enable agent.<key>`.",
        "Query one with `it2agent-flag <key>` (prints 1, exit 0, when ON), toggle",
        "with `it2agent-flag enable|disable <key>`, and see every flag and its",
        "state with `it2agent-flag list`.",
        "",
        "> This guide is GENERATED from the it2agent feature-flag schema"
        " (`KNOWN_FLAGS`)",
        "> and the MCP tool registry, so it is always current — adding or removing a",
        "> capability or an MCP tool updates this document automatically. Do not edit",
        "> it by hand: run `it2agent guide` to regenerate (`it2agent guide --check`"
        " fails",
        "> on drift). For a short, live summary of what is turned on right now, run",
        "> `it2agent brief`.",
        "",
        "## Capabilities (feature flags)",
        "",
        "Each row is one capability: its flag and what it does. Enable a row with",
        "`it2agent-flag enable agent.<key>`; most tools also accept `--no-gate` or",
        "honor `IT2AGENT_FORCE=1` for local testing. See the per-feature READMEs",
        "under `it2agent/<feature>/` for full command reference and examples.",
        "",
        "| Flag | What it does |",
        "| --- | --- |",
        _flag_rows(),
        "",
        "## MCP tools (`agent.mcp`)",
        "",
        "Enable `agent.mcp` to start `it2agent-mcp`, which exposes it2agent",
        "orchestration to MCP-capable agents over stdio (JSON-RPC 2.0), backed by",
        "the daemon + broker. Once the server is connected, these tools are live",
        "(the `help` tool returns this very guide, so an agent can rediscover every",
        "capability at any time):",
        "",
        "| Tool | Required args | Purpose |",
        "| --- | --- | --- |",
        _tool_rows(tools) if tools else "| _(tool registry unavailable)_ | — | — |",
        "",
        "## Toggle any capability",
        "",
        "```",
        "it2agent-flag list                      # every flag + on/off",
        "it2agent-flag enable agent.status_board # turn one ON",
        "it2agent-flag disable agent.mcp         # turn one OFF",
        "it2agent-flag agent.broker              # query (prints 1, exit 0, if ON)",
        "```",
        "",
        f"Known flags: {_known_flags_inline()}.",
        "",
    ]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Brief (short, live capabilities summary)
# --------------------------------------------------------------------------- #


def _enabled_caps() -> list[str]:
    """The capability names currently turned ON, in canonical schema order."""
    return [
        cap
        for cap in it2agent_flag.KNOWN_FLAGS
        if it2agent_flag.is_enabled(it2agent_flag.PREFIX + cap)
    ]


def render_brief() -> str:
    """Render a short, live capabilities summary of what is active right now.

    Structure (per #113): active capabilities → how to use → pointer to the full
    guide and the MCP tools. Reads live flag state via ``it2agent_flag``.
    """
    enabled = _enabled_caps()
    tools = _load_tools()
    lines = ["it2agent — agentic capabilities available in this terminal", ""]

    if enabled:
        lines.append(f"Active capabilities ({len(enabled)} enabled):")
        width = max(len(cap) for cap in enabled)
        for cap in enabled:
            desc = it2agent_flag.KNOWN_FLAGS.get(cap, "")
            lines.append(f"  agent.{cap.ljust(width)}  {desc}")
    else:
        lines.append("Active capabilities: none enabled yet.")
        lines.append(
            "  Turn one on with: it2agent-flag enable agent.<key> "
            "(e.g. agent.mcp, agent.broker)."
        )
    lines.append("")

    lines.append("How to use:")
    lines.append("  - Full, always-current guide: run `it2agent help`.")
    lines.append(
        "  - Toggle capabilities: `it2agent-flag enable|disable agent.<key>`; "
        "`it2agent-flag list` shows all."
    )
    if tools:
        names = ", ".join(spec.name for spec in tools)
        mcp_on = "mcp" in enabled
        state = "connected" if mcp_on else "available once `agent.mcp` is ON"
        lines.append(
            f"  - MCP tools ({state}): {names}. Point your MCP client at the "
            "it2agent server to call these live."
        )
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

USAGE = """usage: it2agent_guide.py <command>

Commands:
  guide            Regenerate AGENT_GUIDE.md from the flag schema + MCP tools.
  guide --check    Exit 0 if the committed guide matches a fresh render; 1 on drift.
  render           Print the freshly rendered guide to stdout (no file write).
  brief            Print a short, live summary of active capabilities.
  -h, --help       Show this help.

The guide is GENERATED — edit the schema (KNOWN_FLAGS) or the MCP tool registry,
then run `it2agent guide` to update AGENT_GUIDE.md."""


def _cmd_guide(args: list[str]) -> int:
    check = "--check" in args
    fresh = render_guide()
    if check:
        try:
            current = GUIDE_PATH.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current == fresh:
            print(f"AGENT_GUIDE.md is up to date ({GUIDE_PATH}).")
            return 0
        print(
            "DRIFT: AGENT_GUIDE.md is out of date with the flag schema / MCP tool "
            "registry. Run `it2agent guide` to regenerate.",
            file=sys.stderr,
        )
        return 1
    GUIDE_PATH.write_text(fresh, encoding="utf-8")
    print(f"Wrote {GUIDE_PATH}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    command = argv[0]
    rest = argv[1:]
    if command == "guide":
        return _cmd_guide(rest)
    if command == "render":
        sys.stdout.write(render_guide())
        return 0
    if command == "brief":
        sys.stdout.write(render_brief())
        return 0
    print(f"it2agent_guide: unknown command: {command}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""it2agent cost/token dashboard — pure core (it2agent #16).

This module is the *pure*, testable heart of the cost dashboard. It has **no**
I/O of its own (the CLI in ``it2agent-cost`` reads files and hands lines here)
and **no** ``iterm2`` import, so it unit-tests in plain CI with fixture strings.

What it does
------------
* Parse **Claude Code transcript JSONL** entries (``~/.claude/projects/**/*.jsonl``)
  into :class:`UsageEntry` records, tolerating a schema that varies across
  Claude Code versions: missing keys, extra keys, non-integer token fields, and
  outright malformed JSON lines are all skipped rather than crashing.
* Aggregate token counts **per agent** (a configurable grouping key) and an
  **overall total**.
* Map tokens to a **cost estimate** using a configurable price table. Cost is an
  ESTIMATE derived from the configured per-model rates — it is never read from
  the logs (Claude transcripts record tokens, not dollars).
* Detect **idle-burn**: an agent still accruing tokens after it went quiet.
* Evaluate **soft caps**: per-agent and/or total dollar thresholds.

Everything here is a pure function over in-memory data. Numbers only ever come
from the parsed logs; dollars only ever come from ``tokens × configured rate``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Token counts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenCounts:
    """The four token buckets Claude Code records per assistant turn.

    ``cache_creation`` (aka cache-write) and ``cache_read`` are billed at rates
    distinct from plain input, so they are tracked separately rather than folded
    into ``input``.
    """

    input: int = 0
    output: int = 0
    cache_creation: int = 0
    cache_read: int = 0

    @property
    def total(self) -> int:
        return self.input + self.output + self.cache_creation + self.cache_read

    def __add__(self, other: "TokenCounts") -> "TokenCounts":
        return TokenCounts(
            input=self.input + other.input,
            output=self.output + other.output,
            cache_creation=self.cache_creation + other.cache_creation,
            cache_read=self.cache_read + other.cache_read,
        )


ZERO_TOKENS = TokenCounts()


def _coerce_int(value) -> int:
    """Return ``value`` as a non-negative int, or 0 for anything non-numeric.

    Defensive: the JSONL sometimes carries ``null``, a string, or omits a field
    entirely. None of those may crash the parser.
    """
    if isinstance(value, bool):  # bool is an int subclass — treat as absent
        return 0
    if isinstance(value, int):
        return value if value > 0 else 0
    if isinstance(value, float):
        return int(value) if value > 0 else 0
    return 0


def parse_usage(usage: dict) -> TokenCounts:
    """Extract a :class:`TokenCounts` from a Claude ``usage`` object.

    Reads the canonical field names
    (``input_tokens``/``output_tokens``/``cache_creation_input_tokens``/
    ``cache_read_input_tokens``). Unknown/missing fields read as 0.
    """
    if not isinstance(usage, dict):
        return ZERO_TOKENS
    return TokenCounts(
        input=_coerce_int(usage.get("input_tokens")),
        output=_coerce_int(usage.get("output_tokens")),
        cache_creation=_coerce_int(usage.get("cache_creation_input_tokens")),
        cache_read=_coerce_int(usage.get("cache_read_input_tokens")),
    )


# ---------------------------------------------------------------------------
# Usage entries (parsed transcript lines)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageEntry:
    """One assistant turn's token usage plus the context we associate it with."""

    tokens: TokenCounts
    model: str | None = None
    session_id: str | None = None
    cwd: str | None = None
    git_branch: str | None = None
    project: str | None = None  # the ~/.claude/projects/<dir> name, if known
    timestamp: str | None = None  # raw ISO-8601 string as logged
    epoch: float | None = None  # parsed seconds since epoch, or None


def _first_str(*values) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def parse_epoch(timestamp) -> float | None:
    """Parse an ISO-8601 timestamp (``...Z`` accepted) to epoch seconds, or None."""
    if not isinstance(timestamp, str) or not timestamp:
        return None
    text = timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_entry(obj, project: str | None = None) -> UsageEntry | None:
    """Turn one decoded JSONL object into a :class:`UsageEntry`, or None.

    Returns None when the object carries no usage (user turns, tool results,
    attachment deltas, summaries, …) — those are legitimately skipped, not
    errors. Never raises on a well-formed-but-unexpected object.
    """
    if not isinstance(obj, dict):
        return None

    message = obj.get("message")
    message = message if isinstance(message, dict) else {}

    # `usage` lives under message.usage in current Claude Code; tolerate a
    # top-level `usage` too.
    usage = message.get("usage")
    if not isinstance(usage, dict):
        usage = obj.get("usage")
    if not isinstance(usage, dict):
        return None

    tokens = parse_usage(usage)

    timestamp = obj.get("timestamp")
    return UsageEntry(
        tokens=tokens,
        model=_first_str(message.get("model"), obj.get("model")),
        session_id=_first_str(obj.get("sessionId"), obj.get("session_id")),
        cwd=_first_str(obj.get("cwd")),
        git_branch=_first_str(obj.get("gitBranch"), obj.get("git_branch")),
        project=project,
        timestamp=timestamp if isinstance(timestamp, str) else None,
        epoch=parse_epoch(timestamp),
    )


def parse_line(line: str, project: str | None = None) -> UsageEntry | None:
    """Parse a single raw JSONL line. Malformed JSON yields None (never raises)."""
    line = (line or "").strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    return parse_entry(obj, project=project)


def iter_entries(lines, project: str | None = None):
    """Yield a :class:`UsageEntry` for every parseable, usage-bearing line.

    Bad lines and non-usage lines are silently skipped, so a single corrupt row
    never aborts a run.
    """
    for line in lines:
        entry = parse_line(line, project=project)
        if entry is not None:
            yield entry


# ---------------------------------------------------------------------------
# Price table + cost estimation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Price:
    """Per-million-token USD rates for one model family. Estimate inputs only."""

    input: float = 0.0
    output: float = 0.0
    cache_write: float = 0.0  # billed for cache_creation_input_tokens
    cache_read: float = 0.0


# Default price table: USD per 1,000,000 tokens, keyed by a model-name substring
# (longest match wins). These are ESTIMATE defaults meant to be overridden via
# a JSON price file; they are not authoritative and Anthropic's list prices
# change. `default` is the fallback for an unrecognized model.
DEFAULT_PRICES: dict[str, Price] = {
    "claude-opus": Price(input=15.0, output=75.0, cache_write=18.75, cache_read=1.50),
    "claude-sonnet": Price(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
    "claude-haiku": Price(input=0.80, output=4.0, cache_write=1.0, cache_read=0.08),
    "default": Price(input=3.0, output=15.0, cache_write=3.75, cache_read=0.30),
}

_PER_MILLION = 1_000_000.0


def resolve_price(model: str | None, prices: dict[str, Price]) -> Price:
    """Pick the price for ``model`` by longest matching key substring.

    Falls back to the ``default`` key (or an all-zero price if even that is
    absent, so an unpriced model contributes 0 rather than crashing).
    """
    name = (model or "").lower()
    best_key = None
    best_len = -1
    for key in prices:
        if key == "default":
            continue
        if key.lower() in name and len(key) > best_len:
            best_key, best_len = key, len(key)
    if best_key is not None:
        return prices[best_key]
    return prices.get("default", Price())


def compute_cost(tokens: TokenCounts, price: Price) -> float:
    """Estimate USD for ``tokens`` at ``price`` (rates are per 1M tokens)."""
    return (
        tokens.input * price.input
        + tokens.output * price.output
        + tokens.cache_creation * price.cache_write
        + tokens.cache_read * price.cache_read
    ) / _PER_MILLION


def load_price_table(overrides) -> dict[str, Price]:
    """Merge a raw override mapping onto :data:`DEFAULT_PRICES`.

    ``overrides`` is a dict of ``{model_key: {input, output, cache_write,
    cache_read}}`` (any subset of rate fields; missing ones default to 0 for
    that key). A falsy/invalid ``overrides`` leaves the defaults untouched.
    """
    table = dict(DEFAULT_PRICES)
    if not isinstance(overrides, dict):
        return table
    for key, raw in overrides.items():
        if not isinstance(raw, dict):
            continue

        def _rate(name):
            value = raw.get(name)
            return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

        table[str(key)] = Price(
            input=_rate("input"),
            output=_rate("output"),
            cache_write=_rate("cache_write"),
            cache_read=_rate("cache_read"),
        )
    return table


# ---------------------------------------------------------------------------
# Agent association + aggregation
# ---------------------------------------------------------------------------

# The supported grouping keys and how each derives an agent label from an entry.
GROUP_KEYS = ("cwd", "project", "branch", "session")
DEFAULT_GROUP_BY = "cwd"
UNKNOWN_AGENT = "unknown"


def agent_key(entry: UsageEntry, group_by: str = DEFAULT_GROUP_BY) -> str:
    """Derive the agent label for ``entry`` under the chosen grouping heuristic.

    Association heuristic (documented in the README): a raw Claude transcript
    does not carry the it2agent agent id, so we group usage by a proxy:

    * ``cwd`` (default): the basename of the working directory. it2agent gives
      each agent an isolated worktree (#13), so the leaf dir is a good agent
      proxy.
    * ``project``: the ``~/.claude/projects/<dir>`` bucket name.
    * ``branch``: the git branch recorded in the transcript.
    * ``session``: the Claude session id (finest grain; one chat = one agent).

    Limits: agents sharing a cwd collapse into one row; one agent that hops
    directories splits across rows. Pick the grouping that matches how the fleet
    was spawned.
    """
    if group_by == "session":
        return entry.session_id or UNKNOWN_AGENT
    if group_by == "branch":
        return entry.git_branch or UNKNOWN_AGENT
    if group_by == "project":
        return entry.project or UNKNOWN_AGENT
    # default: cwd basename
    if entry.cwd:
        return os.path.basename(entry.cwd.rstrip("/")) or entry.cwd
    return UNKNOWN_AGENT


@dataclass
class AgentUsage:
    """Aggregated usage + estimated cost for one agent (or the grand total)."""

    agent: str
    tokens: TokenCounts = ZERO_TOKENS
    cost_usd: float = 0.0
    entries: int = 0
    models: set = field(default_factory=set)
    first_epoch: float | None = None
    last_epoch: float | None = None


@dataclass
class Aggregation:
    """The full result: per-agent rows plus a synthesized total row."""

    agents: dict  # agent label -> AgentUsage
    total: AgentUsage
    group_by: str

    def sorted_agents(self):
        """Agents ordered by estimated cost, descending (ties broken by name)."""
        return sorted(self.agents.values(), key=lambda a: (-a.cost_usd, a.agent))


def _observe_epochs(usage: AgentUsage, epoch: float | None) -> None:
    if epoch is None:
        return
    if usage.first_epoch is None or epoch < usage.first_epoch:
        usage.first_epoch = epoch
    if usage.last_epoch is None or epoch > usage.last_epoch:
        usage.last_epoch = epoch


def aggregate(entries, prices: dict[str, Price], group_by: str = DEFAULT_GROUP_BY) -> Aggregation:
    """Aggregate ``entries`` per agent and overall, estimating cost per entry.

    Cost is summed per entry using that entry's own model rate, so a mixed-model
    agent is priced correctly. The total row is the sum of all agents.
    """
    agents: dict[str, AgentUsage] = {}
    total = AgentUsage(agent="TOTAL")

    for entry in entries:
        label = agent_key(entry, group_by)
        row = agents.get(label)
        if row is None:
            row = AgentUsage(agent=label)
            agents[label] = row

        cost = compute_cost(entry.tokens, resolve_price(entry.model, prices))

        row.tokens = row.tokens + entry.tokens
        row.cost_usd += cost
        row.entries += 1
        if entry.model:
            row.models.add(entry.model)
        _observe_epochs(row, entry.epoch)

        total.tokens = total.tokens + entry.tokens
        total.cost_usd += cost
        total.entries += 1
        if entry.model:
            total.models.add(entry.model)
        _observe_epochs(total, entry.epoch)

    return Aggregation(agents=agents, total=total, group_by=group_by)


# ---------------------------------------------------------------------------
# Idle-burn detection
# ---------------------------------------------------------------------------

DEFAULT_IDLE_GAP_SECONDS = 300.0


@dataclass(frozen=True)
class IdleBurn:
    """An agent flagged as accruing tokens/cost while (apparently) idle."""

    agent: str
    idle_tokens: int
    idle_cost_usd: float
    idle_bursts: int  # number of turns that followed an idle gap
    status_idle: bool  # flagged because an external status map said "idle"


def detect_idle_burn(
    entries,
    prices: dict[str, Price],
    *,
    group_by: str = DEFAULT_GROUP_BY,
    idle_gap_seconds: float = DEFAULT_IDLE_GAP_SECONDS,
    status_by_agent: dict | None = None,
) -> dict[str, IdleBurn]:
    """Flag agents that keep spending after they appear idle.

    Heuristic (documented in the README): for each agent, sort its turns by
    time; a turn whose gap from the agent's previous turn exceeds
    ``idle_gap_seconds`` is treated as spend that resumed after an idle stretch
    (background compaction, a polling loop, or a human who walked away). The
    tokens/cost of such turns are summed as *idle-burn*. Additionally, if
    ``status_by_agent`` maps an agent to ``"idle"`` and that agent has any usage
    at all, it is flagged regardless of gaps.

    Returns only the flagged agents. Limits: a single genuinely long-running
    request looks identical to an idle gap here, so this is a *highlight*, not an
    enforcement — it never blocks or kills anything.
    """
    status_by_agent = status_by_agent or {}
    # Bucket entries by agent, remembering each turn's epoch + tokens + model.
    buckets: dict[str, list] = {}
    for entry in entries:
        buckets.setdefault(agent_key(entry, group_by), []).append(entry)

    flagged: dict[str, IdleBurn] = {}
    for label, rows in buckets.items():
        timed = [r for r in rows if r.epoch is not None]
        timed.sort(key=lambda r: r.epoch)

        idle_tokens = 0
        idle_cost = 0.0
        bursts = 0
        prev_epoch = None
        for row in timed:
            if prev_epoch is not None and (row.epoch - prev_epoch) > idle_gap_seconds:
                idle_tokens += row.tokens.total
                idle_cost += compute_cost(row.tokens, resolve_price(row.model, prices))
                bursts += 1
            prev_epoch = row.epoch

        status_idle = str(status_by_agent.get(label, "")).strip().lower() == "idle"
        has_usage = any(r.tokens.total > 0 for r in rows)

        if bursts > 0 or (status_idle and has_usage):
            flagged[label] = IdleBurn(
                agent=label,
                idle_tokens=idle_tokens,
                idle_cost_usd=idle_cost,
                idle_bursts=bursts,
                status_idle=status_idle,
            )
    return flagged


# ---------------------------------------------------------------------------
# Soft caps
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapBreach:
    """A crossed soft threshold. Soft: reporting only, never enforcement."""

    scope: str  # "total" or "agent:<label>"
    limit_usd: float
    actual_usd: float

    @property
    def overage_usd(self) -> float:
        return self.actual_usd - self.limit_usd


def evaluate_soft_caps(
    aggregation: Aggregation,
    *,
    per_agent: float | None = None,
    total: float | None = None,
) -> list[CapBreach]:
    """Return the soft-cap breaches for ``aggregation`` (pure; no side effects).

    ``per_agent`` applies the same USD ceiling to every agent; ``total`` applies
    one ceiling to the grand total. A None threshold is not evaluated. Breaches
    are sorted total-first, then by descending overage.
    """
    breaches: list[CapBreach] = []
    if total is not None and aggregation.total.cost_usd > total:
        breaches.append(CapBreach("total", total, aggregation.total.cost_usd))
    if per_agent is not None:
        for row in aggregation.agents.values():
            if row.cost_usd > per_agent:
                breaches.append(CapBreach(f"agent:{row.agent}", per_agent, row.cost_usd))

    def _sort_key(b: CapBreach):
        return (0 if b.scope == "total" else 1, -b.overage_usd, b.scope)

    breaches.sort(key=_sort_key)
    return breaches

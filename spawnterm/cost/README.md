# spawnterm/cost — per-agent token/cost dashboard (#16)

Multi-agent fleets burn ~7× the tokens of a single session, and idle agents keep
spending (background compaction, polling, a human who walked away). spawnTerm's
other tools are cost-blind. This dashboard **consumes logs that already exist** —
it does **not** build a meter and does **not** invent numbers. Every token count
comes from the parsed logs; every dollar figure is an **estimate** = `tokens ×
configured rate`.

```
$ spawnterm-cost --group-by cwd --cap-agent 5 --cap-total 20
    AGENT  TURNS  INPUT  OUTPUT  CACHE-W  CACHE-R  TOTAL  COST(est)
-------------------------------------------------------------------
⚠!  alpha      3   1.3k     650     2.0k     5.0k   8.9k      $0.11
    beta       1   5.0k    2.0k        0    10.0k  17.0k      $0.05
-------------------------------------------------------------------
!   TOTAL      4   6.3k    2.7k     2.0k    15.0k  25.9k      $0.16

grouped by: cwd   (cost is an ESTIMATE from the configured price table)
⚠ idle-burn: alpha (300 tok / $0.01)
! soft cap: total at $0.16 exceeds $0.10
```

## Log sources (configurable)

Primary source is **Claude Code transcript JSONL** under `~/.claude/projects/`.
Each `*.jsonl` transcript has one JSON object per line; assistant turns carry a
`message.usage` object (`input_tokens`, `output_tokens`,
`cache_creation_input_tokens`, `cache_read_input_tokens`) plus context
(`message.model`, `cwd`, `gitBranch`, `sessionId`, `timestamp`). We read those
fields and skip everything else (user turns, tool results, summaries).

Configure the source with `--source <dir-or-file>` or `$SPAWNTERM_COST_SOURCE`
(default `~/.claude/projects`). A single file is accepted too. An **OTel** export
is supported by pointing `--source` at a directory/file of JSON lines carrying
the same `usage`/token fields — the parser reads token fields wherever a `usage`
object is present and tolerates the schema differences.

**Defensive parsing:** the JSONL schema varies across Claude Code versions.
Malformed JSON lines, empty lines, non-dict rows, `null`s, non-integer token
values, and unparseable timestamps are all tolerated — a bad line is skipped,
never fatal. (Smoke-tested against a real ~117k-turn `~/.claude/projects`.)

## Per-agent + total, and the cost model

`aggregate()` sums the four token buckets **per agent** and an **overall total**,
counting turns per agent. Cost is estimated **per turn using that turn's own
model rate** (so a mixed-model agent is priced correctly) and summed.

### Price table (configurable; cost is an estimate)

Rates are **USD per 1,000,000 tokens**, per model family, with separate
input / output / cache-write / cache-read rates. The built-in default table
(`costlib.DEFAULT_PRICES`) ships small entries for `claude-opus`,
`claude-sonnet`, `claude-haiku`, and a `default` fallback. **These are estimate
inputs, not authoritative prices** — override them:

```
$ spawnterm-cost --prices my-prices.json      # or $SPAWNTERM_COST_PRICES
```

```json
{
  "claude-opus":  {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
  "claude-sonnet":{"input": 3.0,  "output": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
  "custom-model": {"input": 1.0,  "output": 2.0}
}
```

A model name matches a table key by **longest substring** (e.g. `claude-opus-4-8`
→ `claude-opus`); an unrecognized model uses `default`. Overrides merge onto the
defaults. Every surface labels cost as an estimate (`COST(est)`, the footer note,
and `cost_is_estimate: true` in `--json`).

## Agent-association heuristic (and its limits)

A raw Claude transcript does **not** carry the spawnTerm agent id, so usage is
associated with an agent by a configurable proxy (`--group-by`):

| key       | agent label                                   | rationale |
|-----------|-----------------------------------------------|-----------|
| `cwd` (default) | basename of the working directory       | spawnTerm gives each agent an isolated worktree (#13), so the leaf dir is a good agent proxy |
| `project` | the `~/.claude/projects/<dir>` bucket name    | coarse, one row per project |
| `branch`  | the git branch in the transcript              | matches branch-per-agent spawns |
| `session` | the Claude session id                         | finest grain: one chat = one agent |

**Limits:** agents sharing a cwd collapse into one row; one agent that hops
directories splits across rows. Pick the grouping that matches how the fleet was
spawned. (When #13/#27 identity metadata is wired through the daemon, a direct
agent-id association can replace the proxy.)

## Idle-burn detection

`detect_idle_burn()` flags agents still accruing tokens after they appear idle.
Heuristic: for each agent, sort its turns by time; a turn whose gap from the
agent's previous turn exceeds `--idle-gap` seconds (default **300**) is counted
as spend that resumed after an idle stretch, and its tokens/cost are summed as
idle-burn. If a caller supplies a live status map (`status_by_agent`, e.g. from
the #29 board), an agent marked `idle` with any usage is flagged too.

**Limit:** a single genuinely long-running request looks like an idle gap here,
so this is a **highlight, not enforcement** — it never blocks or kills anything.

## Soft caps (optional)

`--cap-agent <usd>` and/or `--cap-total <usd>` set soft thresholds. When exceeded
the breach is printed (in the table footer and as a stderr warning), and with
`--notify` a best-effort `spawnterm-emit attention` notification fires (OSC 9).
**Soft only — never kills anything.** With no cap flags, nothing is evaluated.

## Surfacing

* **CLI** — `spawnterm-cost` prints the per-agent + total table (or `--json`),
  with idle-burn (`⚠`) and over-cap (`!`) flags. This is the primary surface.
* **Status board** — `cost_board.py` mirrors the #29 daemon status-bar pattern:
  a pure `format_status_line()` core (`Σ $12.34 · 5 agents · ⚠ 1 idle`) plus a
  lazy-`import iterm2` status-bar component (`maybe_register_cost_board`) the
  daemon can register. Gated the same way; recomputes from the logs on a cadence.
* **Emitter** — soft-cap/idle-burn notifications route through `spawnterm-emit`.

## Feature flag

Everything gates on **`spawnterm.cost_dashboard`** (seeded in #11, default OFF),
consumed via the shared `spawnterm-flag` helper. When OFF the CLI is a **no-op**
(exit 0, nothing on stdout, one stderr breadcrumb) and the status-bar component
is not registered. Same fail-safe convention as the other tools: if the flag
helper is unreachable the flag reads OFF; bypass locally with `--no-gate` or
`SPAWNTERM_FORCE=1`.

## Files

| file            | role |
|-----------------|------|
| `costlib.py`    | pure core: JSONL/usage parsing, price table + cost estimation, per-agent/total aggregation, idle-burn, soft caps. No I/O, no `iterm2`. |
| `cost_cli.py`   | I/O + CLI: source discovery, price-file loading, gate, rendering (table/JSON), notify. |
| `spawnterm-cost`| thin executable entry point → `cost_cli.main`. |
| `cost_board.py` | status-bar surface: pure formatter + lazy-`iterm2` component (#29 pattern). |
| `tests/`        | unit tests + fixture JSONL (`agent_alpha`, `agent_beta`, `malformed`) and a `prices_override.json`. Run: `bash spawnterm/cost/tests/run_tests.sh`. |

## Testing

```
bash spawnterm/cost/tests/run_tests.sh
```

Pure stdlib, no network, no live iTerm2. Covers parsing + malformed-line
tolerance, per-agent + total aggregation, cost math against a known price table
(+ overrides), the grouping heuristic, idle-burn detection, soft-cap triggers,
the pure status-line formatter, and the gate-off no-op.

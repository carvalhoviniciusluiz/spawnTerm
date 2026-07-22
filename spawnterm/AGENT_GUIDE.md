# spawnTerm — agent capability guide

spawnTerm turns iTerm2 into a control plane for orchestrating AI coding agents:
spawn agents with identity, a live status board, cross-tab messaging, durable
handoffs, review, cost, and more.
**Everything is a feature flag and every flag defaults OFF.** A capability does
nothing until you turn it on: `spawnterm-flag enable spawnterm.<key>`. Toggle and
inspect with `spawnterm-flag enable|disable <key>`, `spawnterm-flag <key>` (query,
exit 0 = ON), and `spawnterm-flag list` (every flag + on/off state).

Below, each capability is one row: **flag** → **command / MCP tool** → **example**.
This file is the single source of truth; `spawnterm help`, the MCP `help` tool,
and the spawn header all read it.

## Status board — agents paint their own state (`spawnterm.status_board`)

Emit iTerm2 escape codes from an agent's own stdout via `spawnterm-emit`. When the
flag is OFF every emit is a silent no-op (exit 0). Writes the dot-free user vars
`agent_role` / `agent_task` / `agent_status`.

| Emit | Command | Example |
| --- | --- | --- |
| status | `spawnterm-emit status <busy\|blocked\|done\|idle>` | `spawnterm-emit status blocked` |
| role | `spawnterm-emit role <role>` | `spawnterm-emit role reviewer` |
| task | `spawnterm-emit task <task>` | `spawnterm-emit task "build #56"` |
| attention | `spawnterm-emit attention [message]` | `spawnterm-emit attention "need input"` |
| mark | `spawnterm-emit mark` | `spawnterm-emit mark` |
| progress | `spawnterm-emit progress <state 0-4> <pct 0-100>` | `spawnterm-emit progress 1 50` |
| color | `spawnterm-emit color <status\|RRGGBB>` | `spawnterm-emit color done` |
| badge | `spawnterm-emit badge [format]` | `spawnterm-emit badge` |

Triggers: `spawnterm/emit/triggers/spawnterm-agent-status.triggers.json` — import
into an iTerm2 profile so the terminal reacts to emitted state (colorblind-safe
Okabe-Ito palette). Bypass the gate for local testing with `--no-gate` or
`SPAWNTERM_FORCE=1`.

## Spawn — open a new agent tab with identity (`spawnterm.status_board`, `spawnterm.worktree_isolation`)

`spawnterm-spawn [options] [--] <command>` opens an iTerm2 tab and stamps identity
via `spawnterm-emit`. Spawning itself is core (never gated); identity emits gate on
`spawnterm.status_board`.

| Concern | Flag / option | Example |
| --- | --- | --- |
| cwd (default) | inherits spawner `$PWD` | `spawnterm-spawn --role worker -- claude` |
| cwd override | `--dir <path>` / `--home` | `spawnterm-spawn --dir ~/proj/api -- $SHELL -l` |
| identity | `--role` / `--task` / `--status` | `spawnterm-spawn --role impl --task "build #56" --status busy -- claude` |
| worktree isolation | `spawnterm.worktree_isolation` (per-agent git worktree + branch, exports `$SPAWNTERM_PORT` / `$SPAWNTERM_NS` / `$SPAWNTERM_WORKTREE` / `$SPAWNTERM_BRANCH`) | `spawnterm-spawn --id 56 --role worker -- claude` |
| preview | `--dry-run` | `spawnterm-spawn --role x --dry-run -- true` |
| guide header | injected by default; `--no-guide` opts out | `spawnterm-spawn --no-guide --role x -- claude` |

`spawnterm-worktree plan|create|cleanup --id <id> [--role R]` is the deterministic
allocator (branch `spawnterm/<slug>-<hash6>` + per-agent port); `spawnterm-spawn`
delegates to it when isolation is ON.

## Broker — durable mailbox, registry, handoff (`spawnterm.broker`)

The broker is durable state iTerm2 lacks: a sqlite mailbox/registry/handoff store
over a unix socket. Start it with `spawnterm-broker serve` (gated). Talk to a
running broker with the un-gated client subcommands or `BrokerClient.request(op)`;
messaging/registry/handoff are protocol **ops** (also surfaced as MCP tools).

| Op group | Ops | Example |
| --- | --- | --- |
| liveness | `spawnterm-broker ping` / `health` / `paths` | `spawnterm-broker ping` |
| messaging | `send` / `poll` / `ack` (durable, FIFO, at-least-once until acked) | `c.request({"op":"send","to":"a1","from":"boss","body":"go"})` |
| registry | `register` / `query` / `touch` | `c.request({"op":"query","role":"impl","alive":True})` |
| handoff | `handoff_put` / `handoff_get` / `handoff_history` (append-only) | `c.request({"op":"handoff_put","agent_id":"a1","goal":"ship #56"})` |

(`c = BrokerClient()` from `spawnterm/broker/client.py`.) Cross-tab message routing
into live sessions is done by the daemon and gates additionally on
`spawnterm.messaging`.

## Daemon — iTerm2 Python-API orchestration (`spawnterm.daemon`, `spawnterm.messaging`)

`python3 spawnterm/daemon/spawnterm_daemon.py spawn [--dir|--home] [--role|--task] -- <command>`
spawns with identity via the iTerm2 Python API; the daemon also ingests emitted
envelopes and best-effort routes messages into live tabs (routing gates on
`spawnterm.messaging`) and can paint a status-bar dashboard.

```
python3 spawnterm/daemon/spawnterm_daemon.py spawn --role reviewer -- "$SHELL" -l
```

## tmux — crash-survivable agents (`spawnterm.tmux`)

`spawnterm-tmux` runs the agent under `tmux -CC` so it survives disconnect; reuses
the same identity + worktree helpers.

| Command | Example |
| --- | --- |
| `spawnterm-tmux spawn [opts] -- <command>` (start-or-reattach) | `spawnterm-tmux spawn --role worker --task "build #5" -- claude` |
| `spawnterm-tmux attach [--id\|--role/--task\|--session]` (recovery) | `spawnterm-tmux attach --id 5` |
| `spawnterm-tmux name [...]` (derived session name, pure) | `spawnterm-tmux name --id 5` |

## Review — one command per agent to merge or send back (`spawnterm.review`)

`spawnterm-review <cmd> <agent> [--role R]` resolves the agent's branch/worktree
deterministically (reuses the worktree allocator) and reviews it.

| Command | Example |
| --- | --- |
| `resolve` (pure: branch/worktree/base) | `spawnterm-review resolve 56 --role worker` |
| `show` (diff vs base) | `spawnterm-review show 56` |
| `approve [--cleanup]` (safe merge) | `spawnterm-review approve 56 --cleanup` |
| `request-changes "<note>"` | `spawnterm-review request-changes 56 "add tests"` |
| `pane` (open show in an iTerm2 split) | `spawnterm-review pane 56` |

## Janitor — pre-handoff verification gate (`spawnterm.janitor`)

`spawnterm-janitor check` runs the project's configured gate (build/tests/lint)
before a handoff; `owns` reports file ownership for an agent.

```
spawnterm-janitor check
spawnterm-janitor owns backend --repo /repo --role worker
```

## Cost — per-agent token/cost dashboard (`spawnterm.cost_dashboard`)

`spawnterm-cost` reads existing Claude Code transcript logs and prints a per-agent
token/cost table (cost is an estimate = tokens × configured rate).

```
spawnterm-cost --group-by cwd --cap-agent 5 --cap-total 20
spawnterm-cost --prices my-prices.json
```

## Inbox — approval queue for guarded actions (`spawnterm.agent_inbox`)

`spawnterm-inbox` queues actions (e.g. `git.push`) for human approve/reject.

| Command | Example |
| --- | --- |
| `submit --action <a> --scope <s> --session <id>` | `spawnterm-inbox submit --action git.push --scope repo --session a1` |
| `list` / `show <id>` | `spawnterm-inbox list` |
| `approve <id>` / `reject <id>` / `edit <id>` | `spawnterm-inbox approve 1` |
| `config-path` | `spawnterm-inbox config-path` |

## MCP — self-orchestration for MCP-capable agents (`spawnterm.mcp`)

`spawnterm-mcp` exposes orchestration as MCP tools over stdio (JSON-RPC 2.0),
backed by the daemon + broker. Enable `spawnterm.mcp` to start the server.

| Tool | Purpose |
| --- | --- |
| `spawn` | create/launch an agent in a tab (+ register when `id` given) |
| `assign` | upsert an agent's role/task/capabilities (broker `register`) |
| `handoff` | write a durable handoff record (broker `handoff_put`) |
| `send_message` | durable, ack'd message to another agent (broker `send`) |
| `status` | read an agent's latest handoff (broker `handoff_get`) |
| `list_agents` | list the registry, filter by role/alive/capability |
| `help` | return this guide's text (also via `resources/read` + `initialize` instructions) |

## Toggle any capability

```
spawnterm-flag list                          # every flag + on/off
spawnterm-flag enable spawnterm.status_board # turn one ON
spawnterm-flag disable spawnterm.mcp         # turn one OFF
spawnterm-flag spawnterm.broker              # query (prints 1, exit 0, if ON)
```

Known flags: `status_board`, `worktree_isolation`, `messaging`, `agent_inbox`,
`cost_dashboard`, `janitor`, `mcp`, `daemon`, `broker`, `review`, `tmux`.
For local testing, most tools accept `--no-gate` or honor `SPAWNTERM_FORCE=1`.

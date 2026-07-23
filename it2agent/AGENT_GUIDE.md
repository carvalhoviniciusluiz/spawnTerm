# it2agent — agent capability guide

it2agent turns iTerm2 into a control plane for orchestrating AI coding agents:
spawn agents with identity, a live status board, cross-tab messaging, durable
handoffs, review, cost, and more.
**Everything is a feature flag and every flag defaults OFF.** A capability does
nothing until you turn it on: `it2agent-flag enable agent.<key>`. Toggle and
inspect with `it2agent-flag enable|disable <key>`, `it2agent-flag <key>` (query,
exit 0 = ON), and `it2agent-flag list` (every flag + on/off state).

Below, each capability is one row: **flag** → **command / MCP tool** → **example**.
This file is the single source of truth; `it2agent help`, the MCP `help` tool,
and the spawn header all read it.

## Status board — agents paint their own state (`agent.status_board`)

Emit iTerm2 escape codes from an agent's own stdout via `it2agent-emit`. When the
flag is OFF every emit is a silent no-op (exit 0). Writes the dot-free user vars
`agent_role` / `agent_task` / `agent_status`.

| Emit | Command | Example |
| --- | --- | --- |
| status | `it2agent-emit status <busy\|blocked\|done\|idle>` | `it2agent-emit status blocked` |
| role | `it2agent-emit role <role>` | `it2agent-emit role reviewer` |
| task | `it2agent-emit task <task>` | `it2agent-emit task "build #56"` |
| attention | `it2agent-emit attention [message]` | `it2agent-emit attention "need input"` |
| mark | `it2agent-emit mark` | `it2agent-emit mark` |
| progress | `it2agent-emit progress <state 0-4> <pct 0-100>` | `it2agent-emit progress 1 50` |
| color | `it2agent-emit color <status\|RRGGBB>` | `it2agent-emit color done` |
| badge | `it2agent-emit badge [format]` | `it2agent-emit badge` |

Triggers: `it2agent/emit/triggers/it2agent-agent-status.triggers.json` — import
into an iTerm2 profile so the terminal reacts to emitted state (colorblind-safe
Okabe-Ito palette). Bypass the gate for local testing with `--no-gate` or
`IT2AGENT_FORCE=1`.

## Spawn — open a new agent tab with identity (`agent.status_board`, `agent.worktree_isolation`)

`it2agent-spawn [options] [--] <command>` opens an iTerm2 tab and stamps identity
via `it2agent-emit`. Spawning itself is core (never gated); identity emits gate on
`agent.status_board`.

| Concern | Flag / option | Example |
| --- | --- | --- |
| cwd (default) | inherits spawner `$PWD` | `it2agent-spawn --role worker -- claude` |
| cwd override | `--dir <path>` / `--home` | `it2agent-spawn --dir ~/proj/api -- $SHELL -l` |
| identity | `--role` / `--task` / `--status` | `it2agent-spawn --role impl --task "build #56" --status busy -- claude` |
| worktree isolation | `agent.worktree_isolation` (per-agent git worktree + branch, exports `$IT2AGENT_PORT` / `$IT2AGENT_NS` / `$IT2AGENT_WORKTREE` / `$IT2AGENT_BRANCH`) | `it2agent-spawn --id 56 --role worker -- claude` |
| preview | `--dry-run` | `it2agent-spawn --role x --dry-run -- true` |
| guide header | injected by default; `--no-guide` opts out | `it2agent-spawn --no-guide --role x -- claude` |

`it2agent-worktree plan|create|cleanup --id <id> [--role R]` is the deterministic
allocator (branch `it2agent/<slug>-<hash6>` + per-agent port); `it2agent-spawn`
delegates to it when isolation is ON.

## Broker — durable mailbox, registry, handoff (`agent.broker`)

The broker is durable state iTerm2 lacks: a sqlite mailbox/registry/handoff store
over a unix socket. Start it with `it2agent-broker serve` (gated). Talk to a
running broker with the un-gated client subcommands or `BrokerClient.request(op)`;
messaging/registry/handoff are protocol **ops** (also surfaced as MCP tools).

| Op group | Ops | Example |
| --- | --- | --- |
| liveness | `it2agent-broker ping` / `health` / `paths` | `it2agent-broker ping` |
| messaging | `send` / `poll` / `ack` (durable, FIFO, at-least-once until acked) | `c.request({"op":"send","to":"a1","from":"boss","body":"go"})` |
| registry | `register` / `query` / `touch` | `c.request({"op":"query","role":"impl","alive":True})` |
| handoff | `handoff_put` / `handoff_get` / `handoff_history` (append-only) | `c.request({"op":"handoff_put","agent_id":"a1","goal":"ship #56"})` |

(`c = BrokerClient()` from `it2agent/broker/client.py`.) Cross-tab message routing
into live sessions is done by the daemon and gates additionally on
`agent.messaging`.

## Daemon — iTerm2 Python-API orchestration (`agent.daemon`, `agent.messaging`)

`python3 it2agent/daemon/it2agent_daemon.py spawn [--dir|--home] [--role|--task] -- <command>`
spawns with identity via the iTerm2 Python API; the daemon also ingests emitted
envelopes and best-effort routes messages into live tabs (routing gates on
`agent.messaging`) and can paint a status-bar dashboard.

```
python3 it2agent/daemon/it2agent_daemon.py spawn --role reviewer -- "$SHELL" -l
```

## tmux — crash-survivable agents (`agent.tmux`)

`it2agent-tmux` runs the agent under `tmux -CC` so it survives disconnect; reuses
the same identity + worktree helpers.

| Command | Example |
| --- | --- |
| `it2agent-tmux spawn [opts] -- <command>` (start-or-reattach) | `it2agent-tmux spawn --role worker --task "build #5" -- claude` |
| `it2agent-tmux attach [--id\|--role/--task\|--session]` (recovery) | `it2agent-tmux attach --id 5` |
| `it2agent-tmux name [...]` (derived session name, pure) | `it2agent-tmux name --id 5` |

## Review — one command per agent to merge or send back (`agent.review`)

`it2agent-review <cmd> <agent> [--role R]` resolves the agent's branch/worktree
deterministically (reuses the worktree allocator) and reviews it.

| Command | Example |
| --- | --- |
| `resolve` (pure: branch/worktree/base) | `it2agent-review resolve 56 --role worker` |
| `show` (diff vs base) | `it2agent-review show 56` |
| `approve [--cleanup]` (safe merge) | `it2agent-review approve 56 --cleanup` |
| `request-changes "<note>"` | `it2agent-review request-changes 56 "add tests"` |
| `pane` (open show in an iTerm2 split) | `it2agent-review pane 56` |

## Janitor — pre-handoff verification gate (`agent.janitor`)

`it2agent-janitor check` runs the project's configured gate (build/tests/lint)
before a handoff; `owns` reports file ownership for an agent.

```
it2agent-janitor check
it2agent-janitor owns backend --repo /repo --role worker
```

## Cost — per-agent token/cost dashboard (`agent.cost_dashboard`)

`it2agent-cost` reads existing Claude Code transcript logs and prints a per-agent
token/cost table (cost is an estimate = tokens × configured rate).

```
it2agent-cost --group-by cwd --cap-agent 5 --cap-total 20
it2agent-cost --prices my-prices.json
```

## Inbox — approval queue for guarded actions (`agent.inbox`)

`it2agent-inbox` queues actions (e.g. `git.push`) for human approve/reject.

| Command | Example |
| --- | --- |
| `submit --action <a> --scope <s> --session <id>` | `it2agent-inbox submit --action git.push --scope repo --session a1` |
| `list` / `show <id>` | `it2agent-inbox list` |
| `approve <id>` / `reject <id>` / `edit <id>` | `it2agent-inbox approve 1` |
| `config-path` | `it2agent-inbox config-path` |

## MCP — self-orchestration for MCP-capable agents (`agent.mcp`)

`it2agent-mcp` exposes orchestration as MCP tools over stdio (JSON-RPC 2.0),
backed by the daemon + broker. Enable `agent.mcp` to start the server.

| Tool | Purpose |
| --- | --- |
| `spawn` | create/launch an agent in a tab (+ register when `id` given) |
| `assign` | upsert an agent's role/task/capabilities (broker `register`) |
| `handoff` | write a durable handoff record (broker `handoff_put`) |
| `send_message` | durable, ack'd message to another agent (broker `send`) |
| `status` | read an agent's latest handoff (broker `handoff_get`) |
| `list_agents` | list the registry, filter by role/alive/capability |
| `team_tasks` | read a mirrored team's task lifecycle — `handoff_history` grouped by `task:` goal, per-task pending→completed (survives lead death) |
| `read_messages` | non-destructive, offset-based inbox read — `poll` filtered to `id > since`, never acks (cursor intact) |
| `help` | return this guide's text (also via `resources/read` + `initialize` instructions) |

## Team bridge — mirror Claude Code agent teams into the durable broker (`agent.team_bridge`)

`it2agent-team-hook` is a durable OBSERVER of Claude Code's experimental agent
teams. Registered as three hooks, it shadows team state into the broker so it
survives lead-session death; the mirror is then visible through the MCP tools
above — `list_agents` filtered by `capability:"team:session-<sid8>"`, `status`,
and (the read surface, #94) `team_tasks {team}` for the per-task lifecycle plus
`read_messages {agent, since}` to drain the durable inbox non-destructively. It
is an observer: it never steers the team and always exits 0. The team key is
derived from `session_id` as `team:session-<first 8 chars>`.

| Event | Broker op |
| --- | --- |
| `TeammateIdle` | `register` the teammate (role = `agent_type`, capability `team:session-<sid8>`) |
| `TaskCreated` | `handoff_put` `goal:task:<id>`, `verification_status:pending` |
| `TaskCompleted` | `handoff_put` `verification_status:completed` + `send` to `lead` |

Opt-in install (edits `~/.claude/settings.json`; deep-merge, never overwrites):

```
it2agent-flag enable agent.team_bridge      # gate (default OFF)
it2agent-team-hook install                  # add the 3 hooks
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1   # you enable this yourself
it2agent-team-hook uninstall                # remove only our entries
```

## Toggle any capability

```
it2agent-flag list                          # every flag + on/off
it2agent-flag enable agent.status_board # turn one ON
it2agent-flag disable agent.mcp         # turn one OFF
it2agent-flag agent.broker              # query (prints 1, exit 0, if ON)
```

Known flags: `status_board`, `worktree_isolation`, `messaging`, `inbox`,
`cost_dashboard`, `janitor`, `mcp`, `daemon`, `broker`, `review`, `tmux`.
For local testing, most tools accept `--no-gate` or honor `IT2AGENT_FORCE=1`.

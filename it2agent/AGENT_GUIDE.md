# it2agent — agent capability guide

it2agent turns iTerm2 into a control plane for orchestrating AI coding
agents: spawn agents with identity, a live status board, cross-tab
messaging, durable handoffs, review, cost, and more.

**Everything is a feature flag and every flag defaults OFF.** A capability
does nothing until you turn it on: `it2agent-flag enable agent.<key>`.
Query one with `it2agent-flag <key>` (prints 1, exit 0, when ON), toggle
with `it2agent-flag enable|disable <key>`, and see every flag and its
state with `it2agent-flag list`.

> This guide is GENERATED from the it2agent feature-flag schema (`KNOWN_FLAGS`)
> and the MCP tool registry, so it is always current — adding or removing a
> capability or an MCP tool updates this document automatically. Do not edit
> it by hand: run `it2agent guide` to regenerate (`it2agent guide --check` fails
> on drift). For a short, live summary of what is turned on right now, run
> `it2agent brief`.

## Capabilities (feature flags)

Each row is one capability: its flag and what it does. Enable a row with
`it2agent-flag enable agent.<key>`; most tools also accept `--no-gate` or
honor `IT2AGENT_FORCE=1` for local testing. See the per-feature READMEs
under `it2agent/<feature>/` for full command reference and examples.

| Flag | What it does |
| --- | --- |
| `agent.status_board` | Legacy: colors the tab and sets a status variable to show agent state. Prefer Native Tab Status. |
| `agent.worktree_isolation` | Gives each agent its own git worktree and a dedicated port so they never collide. |
| `agent.messaging` | Lets agents send messages to each other across tabs through the broker. |
| `agent.inbox` | Keeps a durable per-agent inbox so messages survive restarts. |
| `agent.cost_dashboard` | Shows a running dashboard of token usage and cost. |
| `agent.janitor` | Cleans up stale worktrees and sessions in the background. |
| `agent.mcp` | Exposes it2agent to your agents as an MCP server. |
| `agent.daemon` | Runs the orchestration daemon that tracks agents and their idle/busy state. |
| `agent.broker` | Runs the durable broker - mailbox, registry, and state over a local socket. |
| `agent.review` | Adds a per-agent diff view to approve-and-merge or request changes on a worktree. |
| `agent.tmux` | Runs agents inside a tmux -CC session so they survive a quit or crash and can reattach. |
| `agent.claude_statusbar` | Adds a status-bar item summarizing Claude Code sessions (Waiting, Working, Idle). |
| `agent.menubar` | Adds a menu-bar item with a live count of busy AI agents. |
| `agent.codex_status` | Shows Codex CLI working/idle activity in the tab status. |
| `agent.native_status` | Publishes agent state to iTerm2's native tab status and Cockpit via OSC 21337. |
| `agent.team_bridge` | Mirrors Claude Code agent-teams state into the durable broker so it survives the lead session's death. |
| `agent.canonical_port` | The focused agent also answers on the normal localhost port (e.g. 3000), not just its dynamic one. |
| `agent.isolate_docker` | Sets COMPOSE_PROJECT_NAME per agent so Docker Compose stacks don't collide. |
| `agent.isolate_db` | Exports a per-agent Postgres schema/search_path so agents don't share DB state. |
| `agent.autobrief` | On each Claude Code session start, injects a short it2agent capabilities brief into the agent's context so it discovers the tooling automatically. |

## MCP tools (`agent.mcp`)

Enable `agent.mcp` to start `it2agent-mcp`, which exposes it2agent
orchestration to MCP-capable agents over stdio (JSON-RPC 2.0), backed by
the daemon + broker. Once the server is connected, these tools are live
(the `help` tool returns this very guide, so an agent can rediscover every
capability at any time):

| Tool | Required args | Purpose |
| --- | --- | --- |
| `spawn` | `command` | Create and launch a new agent in an iTerm2 tab. Builds the Tier 1 spawn plan (working directory + dot-free user.agent_* identity vars) and invokes the spawn path; when 'id' is given the agent is also registered in the broker so status/list_agents see it. |
| `assign` | `agent_id` | Assign a role/task (and optional capabilities) to an agent by upserting its broker registry entry. Idempotent per agent_id. |
| `handoff` | `agent_id`, `goal` | Write a durable handoff/state record for an agent (append-only history in the broker): goal, context pointer, owned files, verification status. |
| `send_message` | `to`, `from`, `body` | Send a durable, acknowledged message to another agent via the broker mailbox (survives restarts; re-delivered until acked). |
| `status` | `agent_id` | Get an agent's latest handoff/state record from the broker (optionally scoped to a goal). Returns null when the agent has no handoff yet. |
| `list_agents` | — | List agents from the broker registry, optionally filtered by role, liveness, or a capability tag. |
| `team_tasks` | `team` | Read the durable task lifecycle of a Claude Code agent team mirrored into the broker by the team bridge (#92). Returns each task's append-only pending→completed history, grouped by its 'task:' goal — queryable even after the team lead dies. 'team' is the session id or the derived 'team:session-<sid8>' key. |
| `read_messages` | `agent` | Non-destructively read an agent's durable inbox from the broker mailbox: returns messages with id > 'since' (default 0) WITHOUT acking, so the ack cursor is untouched and a later poll still replays everything. An idempotent, offset-based read (not a consume). |
| `help` | — | Return the it2agent agent capability guide (AGENT_GUIDE.md) — the single source of truth for every capability, its feature flag, its command/MCP tool, and a one-line example. Takes no arguments. |

## Toggle any capability

```
it2agent-flag list                      # every flag + on/off
it2agent-flag enable agent.status_board # turn one ON
it2agent-flag disable agent.mcp         # turn one OFF
it2agent-flag agent.broker              # query (prints 1, exit 0, if ON)
```

Known flags: `status_board`, `worktree_isolation`, `messaging`, `inbox`, `cost_dashboard`, `janitor`, `mcp`, `daemon`, `broker`, `review`, `tmux`, `claude_statusbar`, `menubar`, `codex_status`, `native_status`, `team_bridge`, `canonical_port`, `isolate_docker`, `isolate_db`, `autobrief`.

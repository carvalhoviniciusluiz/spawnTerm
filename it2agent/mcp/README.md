# it2agent-mcp — MCP orchestration surface (#18)

Exposes it2agent's orchestration as **MCP tools** over stdio (JSON-RPC 2.0), so
any MCP-capable agent — Claude Code, Codex, etc. — can **self-orchestrate**:
spawn other agents, assign them roles/tasks, hand off state, and message them
durably.

It is a thin surface. Every tool is backed by the existing **Tier 1 daemon**
(iTerm2 spawn plan) and **Tier 2 broker** (durable sqlite mailbox / registry /
handoff store over a unix socket). It reimplements no transport and no state —
see [`it2agent/docs/design.md`](../docs/design.md) ("What iTerm2 CANNOT do").

`scope:external-tooling` — never imports or modifies iTerm2 source. The `spawn`
tool reaches iTerm2 only indirectly by shelling out to the daemon spawn CLI, so
the MCP process itself has **no `iterm2` dependency** and **no pip dependencies**
at all (stdlib-only).

## Architecture

```
MCP client (Claude Code / Codex)
        │  JSON-RPC 2.0 over stdio (newline-framed)
        ▼
it2agent_mcp.py  ── gate (agent.mcp) + stdio read/write loop   [impure I/O]
        │
        ├─ rpc.py     initialize / tools/list / tools/call dispatch  [pure]
        └─ tools.py   TOOLS registry: schema + handler per tool      [pure]
                 │
                 ├─ Tier 1: daemon build_spawn_plan (+ subprocess launcher)
                 └─ Tier 2: BrokerClient.request(op)  → unix socket → broker
```

The split is deliberate: **`tools.py` and `rpc.py` are pure** (no socket, no
asyncio, no iTerm2, no stdin/stdout) and fully unit-tested; the broker client and
spawn launcher are **injected** via `tools.Deps`, so tests drive every handler
with a mock broker / mock launcher. Only `it2agent_mcp.py` touches the outside
world (the flag gate + the stdio loop).

## Feature flag

Like every it2agent capability, the MCP surface is **off by default**. The
server starts only when `agent.mcp` is ON:

```sh
it2agent-flag enable agent.mcp
```

When the flag is OFF/absent the server prints a message and **exits 0** (refuses
to start) — never opening a socket or importing iTerm2. Bypass for local testing
with `--no-gate` or `IT2AGENT_FORCE=1`, exactly mirroring the daemon/broker gate.

## Tool catalog

Each tool maps to exactly one Tier 1 spawn plan or Tier 2 broker op:

| tool | backing op | purpose |
|------|-----------|---------|
| `spawn` | daemon `build_spawn_plan` + spawn launcher, then broker `register` (when `id` given) | create/launch an agent tab and register it |
| `assign` | broker `register` | assign role/task/capabilities to an agent (idempotent upsert) |
| `handoff` | broker `handoff_put` | append a durable handoff/state record |
| `send_message` | broker `send` | durable, acknowledged message to another agent |
| `status` | broker `handoff_get` | latest handoff/state for an agent |
| `list_agents` | broker `query` | list registry agents (role/alive/capability filters) |
| `team_tasks` | broker `handoff_history` (grouped by `task:` goal) | read a mirrored team's per-task pending→completed lifecycle (#94; survives lead death) |
| `read_messages` | broker `poll` + client-side `id > since` filter | non-destructive, offset-based inbox read — never acks, cursor intact (#94) |
| `help` | reads `../AGENT_GUIDE.md` | return the consolidated capability guide (#56); no broker/spawn dependency |

The guide (`it2agent/AGENT_GUIDE.md`) is the single source of truth; the `help`
tool returns its text without duplicating it. It is also exposed as an MCP
resource (`resources/list` + `resources/read` at `it2agent://guide`) and pointed
at by the `instructions` field in the `initialize` handshake.

A `tools/call` returns a standard MCP result: a `content` text block with the
JSON payload, a `structuredContent` copy for machine use, and `isError`
reflecting the outcome. If the broker is unreachable the call returns a
structured `backend_unavailable` tool error — it never crashes the server.

### Schemas

**`spawn`** — `command` (string or argv list, required); `id`, `role`, `task`,
`status` (`busy`|`blocked`|`done`|`idle`), `cwd`, `home` (bool, excludes `cwd`),
`capabilities` (string list).

```json
{"name": "spawn", "arguments": {"command": "claude", "id": "impl-1", "role": "implementer", "task": "build #18"}}
```

**`assign`** — `agent_id` (required); `role`, `task`, `capabilities` (string
list), `alive` (bool).

```json
{"name": "assign", "arguments": {"agent_id": "impl-1", "role": "reviewer", "capabilities": ["git", "python"]}}
```

**`handoff`** — `agent_id`, `goal` (both required); `context_ptr`, `owned_files`
(string list), `verification_status`.

```json
{"name": "handoff", "arguments": {"agent_id": "impl-1", "goal": "ship #18", "verification_status": "passing"}}
```

**`send_message`** — `to`, `from`, `body` (all required).

```json
{"name": "send_message", "arguments": {"to": "reviewer", "from": "impl-1", "body": "PR is up"}}
```

**`status`** — `agent_id` (required); `goal` (optional scope).

```json
{"name": "status", "arguments": {"agent_id": "impl-1"}}
```

**`list_agents`** — all optional: `role`, `alive` (bool), `capability`.

```json
{"name": "list_agents", "arguments": {"role": "implementer", "alive": true}}
```

**`team_tasks`** — `team` (required): a session id, or the derived
`team:session-<sid8>` key. Returns each `task:`-goal's append-only history.

```json
{"name": "team_tasks", "arguments": {"team": "team:session-ab12cd34"}}
```

**`read_messages`** — `agent` (required); `since` (optional non-negative int,
default 0). Returns inbox messages with `id > since`; never acks.

```json
{"name": "read_messages", "arguments": {"agent": "impl-1", "since": 5}}
```

## MCP protocol

A minimal, correct JSON-RPC 2.0 subset implemented with the stdlib only (`json`,
`sys`) — **no pip dependency**, consistent with the rest of the repo. One request
per line on stdin, one response line per non-notification on stdout. Implemented:

- `initialize` — handshake; advertises `tools` + `resources` capability +
  `serverInfo` + an `instructions` pointer to the guide (echoes the client's
  `protocolVersion`, else `2024-11-05`).
- `tools/list` — the nine tool descriptors with their JSON schemas.
- `tools/call` — dispatch to a handler; result wrapped as an MCP tool result.
- `resources/list` / `resources/read` — the one guide resource (`AGENT_GUIDE.md`).
- `ping` — utility ping (empty result).
- notifications (no `id`, e.g. `notifications/initialized`) — accepted, no reply.

A malformed request never crashes the loop: bad JSON → `-32700`, bad envelope →
`-32600`, unknown method → `-32601`.

## Run it

```sh
# One-time: enable the flag and make sure the broker is running.
it2agent-flag enable agent.mcp
it2agent-flag enable agent.broker
python3 it2agent/broker/it2agent_broker.py serve &

# Start the MCP server (speaks JSON-RPC on stdin/stdout).
python3 it2agent/mcp/it2agent_mcp.py
```

## MCP client config

Register it2agent as an MCP server so any MCP-capable agent can drive
orchestration. Point `command`/`args` at the absolute path to
`it2agent_mcp.py`.

Claude Code (`claude mcp add`):

```sh
claude mcp add it2agent -- python3 /ABS/PATH/it2agent/mcp/it2agent_mcp.py
```

Or an `mcp.json` / `.mcp.json` (Claude Code, Codex, and other MCP clients):

```json
{
  "mcpServers": {
    "it2agent": {
      "command": "python3",
      "args": ["/ABS/PATH/it2agent/mcp/it2agent_mcp.py"],
      "env": {
        "IT2AGENT_FORCE": "1"
      }
    }
  }
}
```

`IT2AGENT_FORCE=1` (or adding `--no-gate` to `args`) bypasses the flag gate;
omit it once you have enabled `agent.mcp` in your config and prefer the flag
to govern availability. The broker socket/db locations honor the standard
`IT2AGENT_BROKER_SOCK` / `IT2AGENT_BROKER_DB` overrides (see
[`it2agent/broker/README.md`](../broker/README.md)).

### Scope: why registration is operator-run, not auto-installed (#113)

The it2agent convention writes Claude-config to the project's **gitignored**
`.claude/settings.local.json` (see
[`docs/claude-config-convention.md`](../docs/claude-config-convention.md)).
**MCP servers cannot be registered there.** Per the official Claude Code docs
([settings](https://code.claude.com/docs/en/settings.md),
[mcp](https://code.claude.com/docs/en/mcp.md)), `settings.json` /
`settings.local.json` do **not** support an `mcpServers` key; MCP registration
lives in different files, keyed by `claude mcp add --scope`:

| `--scope` | Written to | Committed? | Machine-local? |
| --- | --- | --- | --- |
| `local` (default) | `~/.claude.json` (per-project section) | no | yes, but in the operator's **real** `~/.claude.json` |
| `project` | `<repo>/.mcp.json` | **yes** (shared with everyone who clones) | no |
| `user` | `~/.claude.json` (global) | no | yes |

So there is **no** file that is both project-local *and* not-committed *and*
outside the operator's real home dir. `project` scope commits `.mcp.json` into
the user's repo (which we must not do), and `local`/`user` scope both write the
operator's real `~/.claude.json` (which our tooling must never touch). We
therefore **do not auto-register the MCP server** — it is documented-manual. Run
whichever the operator prefers, themselves:

```sh
# Not committed; lives in YOUR ~/.claude.json, only for this project:
claude mcp add --scope local it2agent -- python3 /ABS/PATH/it2agent/mcp/it2agent_mcp.py

# Committed & shared with the repo (only if the whole team wants it):
claude mcp add --scope project it2agent -- python3 /ABS/PATH/it2agent/mcp/it2agent_mcp.py
```

Discovery still works without the MCP connection: `it2agent brief` / the
SessionStart **autobrief** hook tell a fresh Claude the tools exist and how to
enable `agent.mcp`, and the `help` tool returns the full guide once connected.

## Tests

```sh
bash it2agent/mcp/tests/run_tests.sh
```

Pure Python, stdlib only — no services, no sockets, no sleeps, no network.
Covers the nine handlers (arguments → broker/spawn op, via a mock broker + mock
launcher; the `help` tool returns the guide text; `team_tasks`/`read_messages`
compose the #94 read surface over `handoff_history`/`poll`), the JSON-RPC/MCP dispatch
(initialize, `tools/list` schemas, `tools/call`, `resources/list` +
`resources/read`, malformed → JSON-RPC error, notifications), the `agent.mcp`
gate + purity, and a framed stdio round-trip.

# Native orchestration × it2agent — code-grounded overlap map & positioning (v2)

**TL;DR (honest).** There are now **three** overlapping orchestration systems, and two of them are
native (not ours). The base we forked ships a **mature, native iTerm2 Claude orchestration**
(`sources/ClaudeCode/**`, by George Nachman), and Claude Code itself now ships **agent teams**
(lead + teammates, shared task list, a per-agent JSON mailbox). Both are more capable than what
it2agent built for session-driving and status. it2agent's defensible value is a **narrow, durable
substrate** that neither native system provides: a **durable, crash-surviving broker** (mailbox +
ack + registry + handoff history) and **runtime isolation** (per-agent `$PORT`/`$NS`, not just files),
usable by *any* agent through an agent-agnostic CLI/MCP. Everything else we should retire, re-scope,
or feed *into* native.

This v2 corrects three stale claims in the previous version:

1. **emit already cooperates.** `it2agent-emit ccstatus` now writes the native OSC 21337 tab-status
   channel (the one feeding native tab status + Cockpit), gated on its own flag `agent.native_status`
   (#88). The old "parallel `SetUserVar` board only" framing is obsolete —
   `it2agent/emit/it2agent_emit.py:203-244`, `build_ccstatus`.
2. **Native watchers are durable; provenance and clippings are not.** The prior "provenance/watchers
   in-memory, lost on restart" was too broad. `WorkgroupWatcher` is `Codable` and **persists across
   iTerm2 restarts** (`sources/ClaudeCode/Orchestration/WorkgroupWatcher.swift:36-46`, and the
   `register_watch` tool doc: "Watchers persist across iTerm2 restarts"). What is genuinely ephemeral
   is **session provenance** (`SessionProvenanceRegistry.swift:20-38`, "In-memory only… a restart
   both empties this registry and tears down the sessions it described") and **workgroup clippings**
   (per-session, `sources/PTYSessionPeerPort.swift:34-54`).
3. **The broker is real and shipped**, not aspirational: sqlite WAL mailbox + ack-cursor + agent
   registry + append-only handoff store, with a daemon bridge and a 7-tool MCP surface. Cited below.

## The native surfaces, audited (READ ONLY — never edit `sources/ClaudeCode/**`)

**iTerm2 native orchestrator** — an in-app AI chat in "orchestration mode" drives sessions through a
tool set defined in `sources/ClaudeCode/Orchestration/OrchestratorToolDefinitions.swift:90-236`:
- Discovery: `list_workgroups`, `get_state`, `get_screen_contents`, `scroll_wheel`,
  `list_workgroup_clippings`.
- Action (claim-gated): `send_text`, `interrupt`, `add_workgroup_clipping`.
- Convenience: `start_code_review`. Spawn (always prompts): `start_session`.
- Async watchers: `register_watch` / `unregister_watch` / `list_watches`.
- Companion phone: `notify`, `request_notification_permission`.
- **Safety gate**: `OrchestratorSafetyGate.swift` classifies every code-execution path (shell lines,
  submitted `send_text`, `create_file`) and **fails closed to human approval** — a genuinely hard,
  well-tested surface (`OrchestratorSafetyGate.swift:60-192`, `planTypedGate` 412-623).
- **Completion detection**: `ScreenWatchPoller.swift` drives a headless model to judge doneness from
  the rendered screen when a session reports no machine-readable status; tab-status transitions
  (OSC 21337 / cc-status) are the free/exact path (`WorkgroupWatcher.swift:12-23`).
- **Cockpit**: a panel listing all CC sessions by window/status, reading the OSC 21337 detail
  (`sources/ClaudeCode/CockpitWindowController.swift:1601`).
- **cc-status hook**: native installs/uninstalls a hook in `~/.claude/settings.json` that emits the
  OSC 21337 tab status (`sources/ClaudeCode/ClaudeCodeOnboarding.swift:109-204`, 400-453) — this is
  the same file our agent-teams bridge will add a hook to.

**Claude Code agent teams** (Anthropic, experimental; `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) — a
lead session spawns teammates, each in its own context window, coordinating through a **shared task
list** and a **per-agent JSON mailbox**, with hooks `TeammateIdle` / `TaskCreated` / `TaskCompleted`.
Storage and durability (authoritative, `code.claude.com/docs/en/agent-teams`, v2.1.178+):
- Team config `~/.claude/teams/{team}/config.json` — runtime state; **removed when the session ends**.
- Mailbox `~/.claude/teams/{team}/inboxes/{agent}.json` — per-agent JSON.
- Task list `~/.claude/tasks/{team}/` — **persists locally** (never uploaded; resumed sessions keep tasks).
- Team name = `session-` + first 8 chars of the session id.
- **Documented durability gap**: "No session resumption with in-process teammates. `/resume` and
  `/rewind` do not restore in-process teammates… the lead may attempt to message teammates that no
  longer exist"; "Task status can lag"; orphaned tmux sessions. So the task *list* survives but the
  *team* (members, liveness, mailbox) does not, and a resumed lead cannot reconstitute teammates.

## it2agent, audited (our code)

- **Broker (the moat)** — `it2agent/broker/**`, sqlite WAL, unix-socket, newline-JSON protocol:
  - Mailbox: `send` / `poll`(=`fetch`) / `ack` — `it2agent/broker/mailbox.py:184-224`. Strict
    per-recipient FIFO by monotonic id, `pending→delivered→acked`, **at-least-once replay until acked**,
    monotonic **up-to-cursor ack** (`ack_messages` 139-166). Durable across restart (WAL, nothing
    in-memory — module docstring 18-40).
  - Registry: `register` / `query` / `touch` — `it2agent/broker/store.py:297-345`. sqlite-backed,
    **survives broker restart** (unlike the daemon's ephemeral registry, `store.py:5-12`).
  - Handoff store: `handoff_put` / `handoff_get` / `handoff_history` — `store.py:347-389`,
    **append-only history** per `(agent_id, goal)` (`put_handoff` 178-210).
  - Schema/migrations: `it2agent/broker/schema.py:48-106` (v2 messages, v3 agents+handoffs).
  - Sync client: `it2agent/broker/client.py:37-56`. Socket/db paths: `it2agent/broker/paths.py`.
- **Daemon** — `it2agent/daemon/**`, Tier 1 iTerm2 Python-API glue. Ephemeral registry (#26,
  `registry.py`), in-memory best-effort router (#28, `router.py`), and the **broker bridge** (#37,
  `bridge.py`) that maps daemon events → durable broker ops with graceful degrade (`bridge.py:98-114`
  `select_mode`; ack-by-observation `was_observed` 166-178).
- **emit** — `it2agent/emit/it2agent_emit.py`. `ccstatus` cooperates via native OSC 21337 (#88,
  gate `agent.native_status`); the older status/role/task/color/badge commands still write
  `SetUserVar`/tab color (gate `agent.status_board`).
- **Worktree + `$PORT`/`$NS` isolation** — `it2agent/spawn/it2agent-worktree` (#13): deterministic
  branch + worktree + `$IT2AGENT_PORT` + `$IT2AGENT_NS` per agent (header 12-38).
- **MCP** — `it2agent/mcp/**` (#18): **9 tools** — `spawn`, `assign`, `handoff`, `send_message`,
  `status`, `list_agents`, `team_tasks`, `read_messages`, `help` (`it2agent/mcp/tools.py`). Each maps
  to a broker op or the spawn plan; broker client is injected. `team_tasks`/`read_messages` (#94) are
  the READ surface over the #92 team mirror — `handoff_history` grouped by `task:` goal, and a
  non-destructive `poll` with an `id > since` offset. Also exposes `AGENT_GUIDE.md` as a resource
  (`it2agent/mcp/rpc.py`).
- **cost** (#16), **janitor** (#15), **review** (#14, request-changes routes *through the broker
  mailbox* — `it2agent/review/review_notify.py:1-24`), **tmux -CC** (#5), **inbox**, **flags** (#11).

## Overlap matrix (corrected)

| Capability | iTerm2 native (`sources/ClaudeCode`) | Claude Code agent teams | it2agent | Verdict |
|---|---|---|---|---|
| Drive/observe sessions (send/interrupt/screen/state) | ✅ rich tool surface + **safety gate** + claims | ✅ lead drives teammates | in-memory router (#28) + MCP `spawn` | **Native wins**; RETIRE #28 standalone |
| Spawn an agent session | ✅ `start_session` (prompts) | ✅ lead spawns teammates | spawn (#10) + daemon spawn (#27) + MCP `spawn` | Overlap; keep only as broker-registering launcher |
| Agent↔agent messaging | ❌ clippings only (no delivery/ack) | ✅ per-agent JSON mailbox, auto-delivered | ✅ **durable sqlite mailbox + ack + replay** | teams have it in-process; **only it2agent is durable across death** |
| **Message durability / crash survival** | ❌ | ❌ (mailbox in team dir, removed at session end) | ✅ **broker (WAL, replay-until-ack)** | **it2agent only — the moat** |
| Shared task list | ❌ | ✅ `~/.claude/tasks/` (persists) + file-lock claim | handoff store (different shape) | **Native wins**; MIRROR into broker, don't rebuild |
| **Coordination-state survival after lead death** | provenance ephemeral; watchers durable | ❌ team/members/mailbox removed; teammates not resumable | ✅ **registry + handoff history in sqlite** | **it2agent only — the bridge target** |
| Status board / per-tab status | ✅ cc-status → OSC 21337 → tab status + Cockpit | ✅ agent panel / idle notifications | `emit ccstatus`→OSC 21337 (#88, cooperates); old `SetUserVar` board | **Feed native**; RE-SCOPE #7/#8/#29 |
| At-a-glance panel | ✅ **Cockpit** | ✅ agent panel | daemon status-bar dashboard (#29) | **Native wins**; RE-SCOPE/RETIRE #29 |
| Completion detection | ✅ tab-status + **ScreenWatchPoller** (model judges) | ✅ idle notifications + `TeammateIdle` hook | ack-by-observation (#37) | Native more mature; keep ours only for broker ack |
| Code review | ✅ Code Review overlay + `start_code_review` | (teammate can review) | review surface (#14); notify leg is broker-backed | RE-SCOPE #14 to the durable notify leg only |
| Session grouping | ✅ **workgroups** | ✅ team | worktree grouping (#13) | Different axis (file+runtime isolation) |
| **Runtime isolation ($PORT/$NS/service)** | ❌ | ❌ (worktree = files only) | ✅ **#13** | **it2agent only** |
| **Cost / token dashboard** | ❌ | partial (`/costs` guidance) | ✅ #16 | **it2agent only** |
| **Verify/merge janitor (lint+type+test gate)** | ❌ | ❌ | ✅ #15 | **it2agent only** |
| **tmux -CC crash persistence** | ❌ | uses tmux for panes, no persistence guarantee | ✅ #5 | **it2agent only** |
| Async watchers | ✅ **durable** (`register_watch`, Codable) | idle notifications | — | **Native only** |
| Safety gate on code execution | ✅ **strong, fail-closed** | permission modes | ❌ | **Native only — never duplicate** |
| **Agent-agnostic CLI / MCP** | ❌ (only iTerm2's built-in chat) | ❌ (only Claude Code) | ✅ CLI + escape codes + MCP (#18) | **it2agent only — the delivery vehicle** |
| Cross-machine / cross-session | ❌ (peers = same-Mac; iPhone push) | ❌ (one team per session) | broker db is a portable seam (not yet networked) | neither today; broker is the natural home |

## Recommendation

**Keep — the moat (neither native provides):**
- **Broker** (durable mailbox + ack + replay + persistent registry + handoff history) — the core.
- **#13 worktree + `$PORT`/`$NS` runtime isolation**, **#16 cost**, **#15 janitor**, **#5 tmux -CC**.
- The **agent-agnostic CLI + MCP** as the way any agent (Claude Code CLI/teams, Codex, scripts) reaches
  the durable layer without iTerm2's built-in chat.

**Feed native / cooperate (highest value):**
- Keep **emit → OSC 21337** (#88, done) so it2agent-spawned agents light up native tab status + Cockpit.
- **Mirror Claude Code agent-teams task/coordination state into the broker** via a `TeammateIdle` /
  `TaskCompleted` / `TaskCreated` hook so it survives lead death — see `cooperation-strategy.md`.

**Retire or re-scope (we duplicate native, often worse) — DOCUMENTED (#100, docs + flag text only, no
code removed, no default changed):**
- **#28 in-memory router** as a standalone messaging path → RETIRE (durable broker only). Router doc now
  states the canonical path is the durable broker; the router is kept only as the #37 bridge's degraded
  fallback (`daemon/router.py`, `daemon/README.md`).
- **#7/#8 status color/badge, #29 dashboard** → RE-SCOPE to feed OSC 21337 / Cockpit, not a 2nd board.
  The `agent.status_board` flag description is marked LEGACY and points at `agent.native_status` (#88);
  the #29 dashboard doc points at the native Cockpit (`daemon/README.md`).
- **#14 review** → RE-SCOPE to the durable-broker notify leg; don't reimplement the native overlay.
  Documented in `review/README.md` (keep `review_notify.py`; view diffs in native Code Review).

## Bottom line
Stop competing on session-driving, status, review, and the safety gate — native does those, and one
of the natives (agent teams) now also messages and shares tasks. Double down on the one thing both
natives *documented* they lack: **durable coordination state that survives a crash / lead death, plus
runtime isolation**, exposed to any agent through the CLI/MCP. it2agent is the **durable substrate
under** the native orchestrators, not a rival orchestrator.

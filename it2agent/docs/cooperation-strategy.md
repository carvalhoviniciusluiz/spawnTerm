# it2agent — cooperation strategy v2 (code-grounded + evidence-validated, 2026)

Companion to `native-vs-it2agent.md`. It turns the corrected overlap map into a **concrete, sequenced
integration plan**: for each cooperation path — the exact hook/API/file, the data flow, the it2agent
side (which broker op / MCP tool / hook script), and an acceptance test. It then validates each major
decision against external research (see **Research & evidence**) and ends with a **prioritized backlog**
the tech-lead can open directly.

## The landscape is now THREE overlapping systems
1. **iTerm2 native orchestrator** (`sources/ClaudeCode/**`, gnachman): an in-app chat drives sessions;
   OSC 21337 tab status, Cockpit, workgroups, durable watchers, a strong fail-closed safety gate.
2. **Claude Code agent teams** (Anthropic, experimental): a lead spawns teammates that message each
   other via a per-agent JSON mailbox and share a task list; hooks `TeammateIdle` / `TaskCreated` /
   `TaskCompleted`; storage under `~/.claude/teams/` (removed at session end) and `~/.claude/tasks/`
   (persists).
3. **it2agent** (this fork's external tooling + core additions): the durable broker + runtime isolation.

The founding premise ("terminals have no agent orchestration") is now **false twice over**. it2agent
stops duplicating orchestration and becomes the **durable coordination + runtime-isolation substrate
underneath both** — reachable by any agent through the agent-agnostic CLI/MCP.

## What is genuinely ours (the moat), restated against the code
- **Durable, crash-surviving coordination.** Both natives lose coordination state on death: agent
  teams *document* "no session resumption with in-process teammates… coordination state is lost" and
  remove `~/.claude/teams/{team}/` at session end; iTerm2 provenance is "in-memory only"
  (`SessionProvenanceRegistry.swift:20`). it2agent's broker is sqlite-WAL durable: mailbox with
  replay-until-ack (`broker/mailbox.py:18-40`), registry that survives restart (`broker/store.py:5-12`),
  append-only handoff history (`broker/store.py:178-210`).
- **Runtime isolation, not just files.** git worktrees isolate files only; ports/DBs/services still
  collide. `it2agent/spawn/it2agent-worktree` adds `$IT2AGENT_PORT` + `$IT2AGENT_NS` per agent (#13).
- Also uniquely ours: **cost (#16)**, **janitor (#15)**, **tmux -CC persistence (#5)**, and the
  **agent-agnostic CLI/MCP (#18)**.

---

## COOPERATION PATH 1 — Broker bridge under Claude Code agent teams (the moat; next to build)

**Goal.** Mirror the team's task + coordination state into the durable broker so it **survives lead-
session death** (the documented gap), and expose the mirror back to any agent through the existing MCP
surface. We do **not** replace the team mailbox or task list; we shadow them durably.

### Hook / API / file (authoritative — `code.claude.com/docs/en/hooks`, `…/agent-teams`, v2.1.178+)
Register three hooks in `~/.claude/settings.json` (same file the native cc-status hook edits, so the
two coexist — append, never overwrite; mirror the native add/remove discipline in
`ClaudeCodeOnboarding.swift:109-204`). Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.

```
"hooks": {
  "TaskCreated":   [ { "hooks": [ { "type": "command", "command": "<abs>/it2agent-team-hook created" } ] } ],
  "TaskCompleted": [ { "hooks": [ { "type": "command", "command": "<abs>/it2agent-team-hook completed" } ] } ],
  "TeammateIdle":  [ { "hooks": [ { "type": "command", "command": "<abs>/it2agent-team-hook idle" } ] } ]
}
```

Each hook receives JSON on **stdin**:
- `TaskCreated` / `TaskCompleted`: `{ session_id, transcript_path, cwd, hook_event_name,
  task: { id, title, description } }`.
- `TeammateIdle`: `{ session_id, transcript_path, cwd, hook_event_name, agent_type, agent_id }`.

**Critical contract:** exit code 2 *blocks* the team (rolls back task creation / prevents completion /
keeps the teammate working). Our mirror is an **observer** — it MUST **always `exit 0`** and never write
to stdout in a way that steers Claude. Failure to reach the broker is swallowed (exit 0), matching the
fail-safe posture of every it2agent gate (`emit.gate_open`, `bridge._broker_request`).

### Data flow
```
Claude Code team event ──stdin JSON──▶ it2agent-team-hook ──BrokerClient.request()──▶ broker sqlite (WAL)
                                                                                         │
  team = "session-"+session_id[:8]                                                       ▼
  MCP client (any agent) ◀── tools/call list_agents|status ◀────────────────── durable mirror survives
                                                                                lead death / restart
```

### it2agent side — exact broker ops and mirrored record shapes
The hook is a thin CLI (`it2agent/broker/` client, stdlib only) that self-gates on a new flag
`agent.team_bridge` (default OFF, per `it2agent-flag`) and maps each event to existing broker ops
(no new broker op needed for v1):

- **`idle`** → **`register`** (`broker/store.py:297`, upsert keyed by `session_id`):
  ```json
  {"op":"register","session_id":"<agent_id>","role":"<agent_type>","alive":true,
   "capabilities":["claude-code-teammate","team:session-<sid8>"]}
  ```
  The `agent_id` from the payload is the durable key; `agent_type` becomes `role`. This is what makes
  a resumed/rehydrated view of "who was on the team" survive the team dir being deleted.

- **`created`** → **`handoff_put`** (`broker/store.py:347`, append-only), one row per task version:
  ```json
  {"op":"handoff_put","agent_id":"team:session-<sid8>","goal":"task:<task.id>",
   "context_ptr":"<transcript_path>","verification_status":"pending",
   "owned_files":["<task.title>"]}
  ```
  Keying `agent_id` on the **team** (not a teammate) and `goal` on the **task id** means the append-only
  history *is* the task's lifecycle log, queryable after death. `context_ptr` points at the transcript
  so a fresh agent can re-read the origin. (`owned_files[0]` carries the title pragmatically in v1; a v2
  broker op can add first-class `title`/`description` columns — see backlog.)

- **`completed`** → **`handoff_put`** again for the same `(team, task:<id>)` with
  `verification_status:"completed"`. Because handoffs are append-only, `handoff_history` returns the full
  `pending → completed` timeline; `handoff_get` returns the latest. This directly implements the
  "durable artifact as source of truth across context windows" pattern (see evidence §R1).

- Optional **`send`** (mailbox) on `completed` to notify a coordinator id/role:
  `{"op":"send","to":"lead","from":"team:session-<sid8>","body":"task <id> completed"}` — durable,
  replayed until acked, so a lead that died and resumed still sees it (`broker/mailbox.py:184`).

### Expose it back via MCP (reuse, do not add surface for v1)
The mirror is already visible through the **existing** MCP tools (`mcp/tools.py`):
- `list_agents` → broker `query` → the mirrored teammates (filter by `capability:"team:session-<sid8>"`).
- `status {agent_id:"team:session-<sid8>", goal:"task:<id>"}` → broker `handoff_get` → latest task state.
- A thin **new** tool `team_tasks {team}` (→ broker `handoff_history` filtered by goal-prefix `task:`)
  is the only *optional* addition, and only if a raw history dump proves awkward through `status`.

### Acceptance test
1. `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, `agent.team_bridge` ON, broker running. Start a 2-teammate
   team; create 3 tasks; complete 1; let a teammate go idle.
2. Assert broker state **without Claude Code running**: `query` returns the 2 teammates + a
   `team:session-<sid8>` entry; `handoff_history(agent_id="team:session-<sid8>")` shows 3 `created`
   rows and 1 `completed` row in order.
3. **Kill the lead session** (simulate death; `~/.claude/teams/{team}/` is removed). Re-run the broker
   queries: the mirror is intact (survival is the whole point).
4. Feature-flag OFF ⇒ hook exits 0, writes nothing to the broker. Broker down ⇒ hook still exits 0
   (never blocks the team). Malformed stdin ⇒ exit 0, logged, no broker write.
5. Unit test the pure `event → broker op` mapping with fixture stdin payloads and a fake `BrokerClient`
   (mirrors `daemon/tests/test_bridge.py`); no live Claude Code needed.

---

## COOPERATION PATH 2 — Bidirectional status (emit → native done; native → registry?)

**Outbound (done, #88).** `it2agent-emit ccstatus <status>` writes OSC 21337
(`emit/it2agent_emit.py:208-244`), so an it2agent-spawned agent shows in the native tab status +
Cockpit. Gate `agent.native_status`. Keep. **Acceptance:** spawn an agent, run
`it2agent-emit ccstatus busy --detail "building"`; the tab shows the native status and Cockpit lists it.

**Inbound (native → our registry): recommend NOT building a deep reader; do a narrow, supported read.**
- The native tab status lives in iTerm2 session state (OSC 21337; surfaced to the Python API as a
  user variable / session var), and Cockpit/provenance are **in-process Swift with no external API**
  (`CockpitWindowController`, `SessionProvenanceRegistry`). There is no public file/socket to read them
  from without editing `sources/ClaudeCode/**` (forbidden) or scraping private state (brittle).
- The daemon **already** reads the dot-free `user.agent_*` session vars via the iTerm2 Python API
  (`daemon/registry.py:24`, `AGENT_VAR_KEYS`). That is the supported inbound channel. If we want native
  status in our registry, read the **session variable** the native tab status maps to through the same
  Python-API path the daemon uses — do **not** try to reach into Cockpit/provenance.
- **Verdict:** low priority. The valuable direction is outbound (agents → native surfaces). A one-way
  read of the native tab-status *session var* into the registry is a small, optional enhancement; a
  reader for Cockpit/provenance is **not worth it** (no stable surface, native already displays it).

---

## COOPERATION PATH 3 — Defer to native; retire/re-scope our duplicates (safe deprecation)

Everything below is behind an OFF-by-default feature flag already, so deprecation is low-risk: flip the
flag off, leave the code importable for one release, document the native replacement, then delete.

- **#28 in-memory router (standalone messaging): RETIRE.** Both natives message; the router adds nothing
  durable (`daemon/router.py:26-33` calls itself "best-effort only… no queue, replay, ack"). It stays
  as the **degraded fallback inside the broker bridge** (`daemon/bridge.py:322` `_deliver_in_memory`)
  and only there. **Migration:** keep `agent.messaging` meaning "durable broker path"; document that
  messaging without `agent.broker` is a best-effort fallback, not a feature. No API break.
- **#7/#8 status color/badge + #29 status-bar dashboard: RE-SCOPE.** These paint a *second* board via
  `SetUserVar`/a custom status-bar component (`daemon/dashboard.py:1-30`) that overlaps Cockpit. Keep
  the OSC 21337 path (#88); mark the `SetUserVar=agent_status` color/badge board and the #29 dashboard
  as **legacy**, gated OFF, and point users at native tab status + Cockpit. **Migration:** default the
  `agent.status_board` flag OFF (already OFF); no silent default change; note in `feature-flags.md`.
- **#14 review: RE-SCOPE to the durable notify leg only.** Do not reimplement the native Code Review
  overlay / `start_code_review`. The one genuinely-ours piece is already correct: `request-changes`
  routes through the **broker mailbox** (`review/review_notify.py:1-24`) — durable, acked, survives
  restart. Keep that leg; drop any diff-rendering ambitions to native. **Migration:** keep
  `it2agent-review request-changes` (broker send); document "view diffs in native Code Review."
- **Safety gate, session-driving, Cockpit, watchers: never build.** Native owns these and the safety
  gate is hard, fail-closed, and well-tested (`OrchestratorSafetyGate.swift`). Any it2agent tool that
  drives a session (MCP `spawn` aside) should defer to it.

**Deprecation guardrail:** per repo rule "Don't change defaults silently" — every flag stays OFF as it
is today, so no user experiences a behavior change on upgrade; we only update docs + stop investing.

---

## NEW opportunities found in the audit (not in the prior docs)

- **N1 — Handoff store as the agent-teams *resume* artifact (highest new value).** Beyond mirroring, the
  append-only handoff history (`broker/store.py`) is exactly the "living spec / progress file" the
  research recommends (§R1). On `/resume` (where teams document teammates are gone), a fresh agent can
  read `handoff_history(team, goal="task:*")` via MCP `status`/`team_tasks` and reconstruct what each
  dead teammate had done — turning the documented limitation into an it2agent feature. Scope: the MCP
  read tool + a short "resume from broker" recipe in `AGENT_GUIDE.md`.
- **N2 — Broker `poll since` cursor is already resumable-consumer-shaped** (`mailbox.py:95-107`,
  `poll {agent, since}`). Expose `since` through the MCP `send_message`'s read side (there is no MCP
  *poll* tool today — only `send`). A `read_messages {agent, since}` MCP tool would let any agent drain
  its durable inbox with an offset, matching consumer-offset best practice (§R2). Small, high-leverage.
- **N3 — Team-name derivation is a stable join key.** `team = "session-"+session_id[:8]` is deterministic
  from the hook's `session_id`, so the broker mirror and any external dashboard can join team state
  without Claude Code running. Document it as the canonical broker key for team state.
- **N4 — `agent.team_bridge` unlocks cross-machine later.** Because the mirror is plain sqlite behind a
  unix socket, pointing `BrokerClient` at a networked broker (future) makes team coordination visible
  across machines — something *neither* native can do (agent teams: "one team per session"; iTerm2 peers
  are same-Mac). Not now, but the bridge is the seam. (Mark networked-broker "verify/scope" — not built.)

---

## Research & evidence (operator directive)

Each major decision is validated against academic/industry sources. Verdict = **keep** or **adjust**.

### R1 — Durable external store / handoff beats in-memory; resumability after lead death
- **Evidence.** A 2026 review of agent externalization frames durable memory as "checkpoints for
  resumable execution… persistent state that governance can inspect," distinguishing volatile working
  context from externalized state ([Externalization in LLM Agents, arXiv 2604.08224](https://arxiv.org/html/2604.08224v1)).
  Long-horizon multi-agent engineering explicitly uses **artifact-mediated continuity** — agents
  externalize plans/decisions/evidence into durable artifacts downstream agents re-inspect
  ([Autonomous Long-Horizon Engineering, arXiv 2604.13018](https://arxiv.org/pdf/2604.13018)). Industry
  post-mortems on multi-agent failure converge on the same fix: shared, durable state instead of
  in-context coordination ([Redis, Why Multi-Agent LLM Systems Fail](https://redis.io/blog/why-multi-agent-llm-systems-fail/);
  [Augment Code, 2026](https://www.augmentcode.com/guides/why-multi-agent-llm-systems-fail-and-how-to-fix-them)).
  Anthropic's own agent-teams docs concede the gap: teammates and coordination state are not restored
  after lead death ([code.claude.com/docs/en/agent-teams](https://code.claude.com/docs/en/agent-teams)).
- **Verdict: KEEP.** Our append-only handoff store + sqlite registry *is* the externalized artifact the
  literature prescribes. The bridge (Path 1) and N1 make it the resume mechanism. State of the art.

### R2 — Messaging semantics: at-least-once + idempotent consumer vs our sqlite mailbox + ack-cursor
- **Evidence.** Distributed-systems consensus: true exactly-once is impossible (two-generals); the sound
  pattern is **at-least-once delivery + idempotent consumer / dedup by key**, committing the offset only
  *after* successful processing ([Confluent, Kafka delivery semantics](https://docs.confluent.io/kafka/design/delivery-semantics.html);
  [Inbox pattern, DEV](https://dev.to/actor-dev/inbox-pattern-51af)). Consumer-offset / high-water-mark
  is the canonical replay control.
- **Our model.** `broker/mailbox.py`: monotonic FIFO id, replay of every un-acked row, **up-to-cursor
  monotonic ack** that never rewinds (`ack_messages:139-166`), "exactly-once *per cursor+ack*". This is
  textbook at-least-once + offset commit-after-process. **Sound.**
- **Verdict: KEEP, with one adjust (N2).** The delivery model matches best practice. Gap vs the
  literature: we lack a **dedup-by-idempotency-key** on `send` (the docstring notes "no content dedup"),
  and there is no MCP *read/poll* tool exposing the `since` offset. **Adjust:** add an optional
  idempotency key to `send` (dedup) and a `read_messages {since}` MCP tool (N2). A dead-letter path for
  never-observed messages is a reasonable future (currently they replay forever).

### R3 — Runtime isolation: worktree (files) vs port/DB/service isolation
- **Evidence.** Practitioner consensus in 2026: git worktrees isolate *files only*; parallel agents then
  collide on ports/DBs/`.env` and need explicit port namespacing (`BASE + index*10 + offset`) or Docker
  network namespaces ([Upsun](https://developer.upsun.com/posts/ai/git-worktrees-for-parallel-ai-coding-agents);
  [Penligent, "worktrees need runtime isolation"](https://www.penligent.ai/hackinglabs/git-worktrees-need-runtime-isolation-for-parallel-ai-agent-development/);
  [Docker isolation for parallel agents](https://youmind.com/landing/x-viral-articles/docker-isolation-ai-coding-agents)).
- **Our model.** `it2agent-worktree` derives a deterministic `$IT2AGENT_PORT` (base+hash%span) and
  `$IT2AGENT_NS` per agent — precisely the port/DB namespacing the sources prescribe, and more than
  either native provides (both stop at files).
- **Verdict: KEEP; adjust the #13 upgrade toward the "dynamic + canonical port" and optional-container
  model.** Add: distinguish a **canonical** port (checked-out instance answers on the normal
  `localhost:3000`) from **dynamic** per-instance ports; per-service assign strategies
  (`none`/`hot`/`restart`/`rebuild`); optional Docker/namespace isolation for teams that need network
  isolation, not just prefix isolation. Matches state of the art without over-building.

### R4 — Claude Code agent-teams hooks/storage contract (authoritative + what to verify)
- **Confirmed** ([code.claude.com/docs/en/hooks](https://code.claude.com/docs/en/hooks),
  [.../agent-teams](https://code.claude.com/docs/en/agent-teams), v2.1.178+): the three hooks, their
  stdin JSON shapes (Path 1), exit-2 = block semantics, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, team
  name = `session-`+sid[:8], team config removed at session end, task list persists, mailbox =
  `~/.claude/teams/{team}/inboxes/{agent}.json`.
- **Verify before coding:** (a) exact byte format inside `~/.claude/tasks/{team}/` (docs say "do not edit
  by hand"; we only *read* it as a cross-check, never write) — treat as opaque, rely on the hook payload
  instead; (b) whether `TaskCreated`/`TaskCompleted` payload `task` includes a status/assignee field
  beyond `{id,title,description}` (docs show only those three) — if richer fields exist, capture them;
  (c) the `team_name` field in hook payloads is **deprecated** (session-derived) — derive the team key
  ourselves from `session_id`, do not trust `team_name`.
- **Verdict: proceed; the bridge depends only on the confirmed stdin payloads + exit-0 discipline**, not
  on parsing the private `~/.claude/teams` / `~/.claude/tasks` files. This keeps us robust to their
  "files may change" warning.

---

## Prioritized backlog (open these next; title · scope · dependency)

1. **Broker bridge under agent-teams (`it2agent-team-hook` + `agent.team_bridge` flag)** ·
   TeammateIdle/TaskCreated/TaskCompleted hook → broker `register`/`handoff_put`(+`send`), always exit 0,
   pure event→op mapping unit-tested · *dep: broker (shipped), flags (shipped)*. **The moat; do first.**
2. **MCP read surface for the mirror (`team_tasks` and/or `read_messages {agent,since}`)** ·
   expose `handoff_history` + the mailbox `poll since` offset so any agent drains durable team state /
   its inbox · *dep: #1 for `team_tasks`; standalone for `read_messages`* (N1, N2, R2).
3. **`send` idempotency key + dedup** · optional key on broker `send`; drop duplicate enqueues; keeps
   at-least-once sound under retries · *dep: broker* (R2).
4. **Retire #28 standalone router; document broker-only messaging** · router remains only as the bridge's
   degraded fallback; docs + flag semantics, no API break · *dep: none* (Path 3).
5. **Re-scope status board (#7/#8/#29) to feed OSC 21337 / Cockpit** · mark SetUserVar color/badge board
   + #29 dashboard legacy/OFF; keep emit `ccstatus` (#88) · *dep: #88 (done)* (Path 3).
6. **Re-scope review (#14) to the durable notify leg** · keep `review_notify` broker send; drop overlay
   ambitions; docs point at native Code Review · *dep: broker* (Path 3).
7. **#13 runtime-isolation upgrade (dynamic vs canonical port, assign strategies, optional container)** ·
   level up worktree isolation to the state-of-the-art model · *dep: none* (R3).
8. **(verify) native tab-status session-var → registry (inbound status)** · optional one-way read of the
   native tab-status session var via the daemon's existing Python-API path; skip Cockpit/provenance ·
   *dep: daemon* (Path 2). Low priority.

**Positioning in one line:** it2agent = the **durable coordination + runtime-isolation substrate** that
makes iTerm2's and Claude Code's native agent orchestration **survive crashes and lead-death and run
truly parallel**, usable by any agent — not a competing orchestrator.

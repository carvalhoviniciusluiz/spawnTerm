# it2agent — cooperation strategy (web-research informed, 2026)

Companion to `native-vs-agent.md`. This folds in external research on the current landscape and
turns the overlap map into a concrete improve / cooperate / remove plan.

## The landscape is now THREE overlapping systems
1. **iTerm2 native Claude integration** (`sources/ClaudeCode/`, gnachman): an orchestrator chat drives
   many sessions via tools; cc-status/OSC 21337, Cockpit, workgroups, watchers, safety gate.
2. **Claude Code's own native "agent teams"** (Anthropic, shipped ~Feb 2026): a lead session spawns
   teammates that run in parallel, **talk to each other via a messaging system**, and share a **task
   list**; split-pane mode runs on **tmux or iTerm2**. Storage under `~/.claude/teams/` and
   `~/.claude/tasks/`; hooks `TeammateIdle`/`TaskCompleted`.
3. **it2agent** (this fork's external tooling + core additions).

So the founding premise ("terminals have no agent orchestration") is now **false twice over** — both
iTerm2 and Claude Code ship orchestration. it2agent must stop duplicating them and become the
**durable, runtime-isolation layer underneath both**.

## What the research validates as genuinely ours (the moat)
Both native systems share the **same two documented gaps** — and both are exactly what it2agent owns:

- **Durability of coordination state.** Claude Code agent teams (per community + Anthropic docs) have
  known limits: *"No session resumption for in-process teammates. If the lead session dies, in-process
  teammates are gone and **coordination state is lost**"*, *"task status can lag,"* *"orphaned tmux
  sessions."* iTerm2's provenance/clippings are **in-memory, dropped on restart**. Anthropic's own
  long-running-agents guidance recommends **durable, machine-readable artifacts as the source of truth
  across context windows** (a `claude-progress.txt` + git history as the handoff mechanism so an agent
  resumes with a fresh context). → **it2agent Tier 2 broker** (durable sqlite mailbox + ack + replay +
  persistent registry + **handoff store with history**) is precisely that missing durability layer.
- **Runtime isolation, not just file isolation.** Claude Code's native `isolation: worktree` for
  subagents gives *file* isolation only. Research (Upsun, Penligent/"Coasts", zylos) is explicit: git
  worktrees do NOT isolate host **ports, DBs, Docker, services** — the real source of parallel-agent
  bugs. → **it2agent #13** ($IT2AGENT_PORT/$NS) fills this, and neither native system does.

Also uniquely ours (neither native covers): **cost/token dashboard (#16)**, **verify/merge janitor
gate (#15)**, **tmux -CC crash persistence (#5)**, and the **agent-agnostic CLI/MCP** usable by Claude
Code, Codex, or scripts without iTerm2's built-in chat.

## IMPROVE
**Ours:**
- **#13 runtime isolation → level up to the "Coasts" model:** distinguish **dynamic ports** (every
  instance always reachable) from a **canonical port** (the checked-out instance answers on the normal
  `localhost:3000`); add per-service **assign strategies** (`none`/`hot`/`restart`/`rebuild`); optional
  **DB/Docker/namespace** isolation and a small **observability** listing (instances, ports, status).
- **#4 broker → make it the resume artifact:** persist handoff/goal/owned-files/verification exactly as
  Anthropic's living-spec/`claude-progress` guidance describes, so a fresh agent (or a respawned Claude
  Code teammate whose lead died) resumes from the durable store.
**Native (things we can't change but should ride):** cc-status/OSC 21337 is the real status channel;
Claude Code exposes `TeammateIdle`/`TaskCompleted` hooks and `~/.claude/tasks/` — integration points.

## COOPERATE (highest value — stop competing, plug in underneath)
- **Align `it2agent-emit` to cc-status / OSC 21337** so it2agent-spawned agents light up the **native
  tab-status + Cockpit** — instead of our parallel `SetUserVar=agent_status` board.
- **Bridge the broker under Claude Code agent teams:** a hook (`TeammateIdle`/`TaskCompleted` or a
  wrapper) mirrors the team's task/coordination state into it2agent's **durable broker**, so it
  **survives a lead-session death** (the documented failure). Expose it back via the **MCP surface**.
- **Runtime isolation for the panes both native systems spawn:** wrap Claude Code/iTerm2 split-pane
  teammates with it2agent's worktree+$PORT so parallel teammates don't collide on ports/DBs.

## REMOVE / RETIRE
- **i18n (#66/#67): REMOVE** — done via `chore/remove-i18n`. Localizing only our own pane isn't worth
  the maintenance; the native UI stays English anyway.
- **In-memory router (#28) as a standalone path: RETIRE** — Claude Code teams already message; keep
  it2agent messaging on the **durable broker** only (in-memory relay adds nothing durable).
- **Status board parallelism (#7/#8 color/badge, #29 dashboard): RE-SCOPE** — feed cc-status/OSC 21337
  and the native Cockpit instead of maintaining a second, weaker board.
- **Review surface (#14): RE-SCOPE** — don't reimplement iTerm2's native Code Review overlay; if kept,
  make it a thin durable-broker view.

## Suggested issues to open next
1. Align `it2agent-emit` → cc-status/OSC 21337 (cooperate).
2. Broker-under-agent-teams bridge via Claude Code hooks + MCP (cooperate; the durability moat).
3. #13 runtime-isolation upgrade (dynamic/canonical ports, assign strategies, DB isolation, observability).
4. Retire #28 in-memory router; re-scope #7/#8/#29 and #14 to defer to native.

**Positioning in one line:** it2agent = the **durable coordination + runtime-isolation substrate** that
makes iTerm2's and Claude Code's native agent orchestration **survive crashes and run truly parallel**,
usable by any agent — not a competing orchestrator.

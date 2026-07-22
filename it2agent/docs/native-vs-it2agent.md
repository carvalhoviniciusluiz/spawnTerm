# Native iTerm2 Claude integration × it2agent — overlap map & positioning

**TL;DR (honest).** The base we forked already ships a **mature, native Claude Code orchestration**
(`sources/ClaudeCode/`, by George Nachman): iTerm2's built-in AI chat in "orchestration mode" drives
many terminal sessions via tools (send_text, interrupt, get_screen/get_state, **start_session**,
watchers, workgroups, clippings), gated by a real **AI safety gate** and per-session **claims**, with
a **cc-status hook**, **Cockpit** panel, and **iPhone push**. So the original premise ("iTerm2 has no
agent support") is **largely false for this base** — it has a good one, and in several areas it is
**more mature than what we built**. it2agent's defensible value is a **smaller, specific set** — above
all the **durable broker** the native integration does *not* have.

## The one thing native definitively lacks (our moat)
Verified by source audit: **no agent-to-agent messaging, no durable mailbox/queue, no delivery ack, no
handoff/context store with history.** All native coordination is one orchestrator driving sessions;
"clippings" are a per-workgroup note board (no recipient/delivery/ack/ordering/persistence). This is
*exactly* it2agent Tier 2 (#4): durable file/db mailbox + ack + replay + persistent registry + handoff
history. It is the unsolved frontier the market analysis pointed at, and it is genuinely ours.

## Overlap matrix
| Capability | Native iTerm2 (sources/ClaudeCode) | it2agent | Verdict |
|---|---|---|---|
| Drive/observe sessions (send/interrupt/screen/state) | ✅ rich tool surface + safety gate + claims (orchestrator chat) | in-memory router (#28), MCP tools (#18) | **Native wins** — ours is thinner; overlaps |
| Spawn a session running an agent | ✅ `start_session` (+ `claude`), gated | spawn wrapper (#10), daemon spawn (#27) | Overlap; native GUI-integrated |
| Status board / per-tab status | ✅ `cc-status` hook → `it2 set-status` → OSC 21337 tab status | `it2agent-emit` → `SetUserVar=agent_status` (parallel, weaker) | **Native wins** — align to cc-status/OSC 21337 |
| At-a-glance panel | ✅ **Cockpit** (all CC sessions, by window/status) | daemon status-bar dashboard (#29) | **Native wins**; ours duplicates |
| Completion detection | ✅ tab-status + **ScreenWatchPoller** (headless model judges goal) | ack-by-observation (#37) | Native more mature; same idea |
| Code review | ✅ Code Review overlay + `start_code_review` + review peer pane | review surface (#14) | Overlap; native GUI-integrated |
| Session grouping | ✅ **workgroups** (main+diff+review), roles | worktree grouping (#13) | Different axis (see below) |
| **Durable agent↔agent messaging + ack** | ❌ **none** | ✅ **broker #4 (mailbox+ack+replay)** | **it2agent only — the moat** |
| **Durable registry + handoff w/ history** | ❌ provenance in-memory, lost on restart | ✅ **broker registry + handoff store #36** | **it2agent only** |
| **Worktree + $PORT/service isolation** | ❌ none | ✅ #13 (`$IT2AGENT_PORT`/`_NS`) | **it2agent only** |
| **Cost / token dashboard** | ❌ none | ✅ #16 | **it2agent only** |
| **Verify/merge janitor (lint+type+test gate)** | ❌ none (review overlay only) | ✅ #15 | **it2agent only** |
| **tmux -CC persistence** | ❌ none | ✅ #5 | **it2agent only** |
| **Agent-agnostic CLI / MCP** | orchestration only via iTerm2's built-in chat (not MCP) | ✅ CLI + escape codes + MCP (#18) usable by any agent | **Different model — complementary** |
| Feature-flag config + settings pane | install/uninstall wizard | ✅ per-capability flags (#11) + AI-tab pane (#12) | it2agent granular |
| Cross-machine | ❌ (peers = same-Mac panes; iPhone = push only) | not built | neither |

## Recommendation (keep / retire-or-align / complement)
**Keep — the moat (native lacks entirely):**
- **#4 broker** (durable mailbox + ack + replay + persistent registry + handoff-with-history) — the core differentiator.
- **#13 worktree + $PORT/$NS isolation**, **#16 cost dashboard**, **#15 janitor gate**, **#5 tmux -CC** — real gaps native doesn't cover.

**Retire or align to native (we duplicate it, often worse):**
- **Status board (#7/#8 emit color/badge, #29 dashboard):** switch from the parallel `SetUserVar=agent_status` to iTerm2's **cc-status / OSC 21337** path so status shows in the native tab status + Cockpit instead of a second, weaker board.
- **#28 in-memory router:** native's orchestrator already drives sessions better; keep it2agent messaging on the **durable broker** path, not an in-memory relay.
- Re-scope **#14 review** and **#29 dashboard** to avoid re-implementing the native Code Review overlay / Cockpit.

**Complement / bridge (highest-value integration):**
- Make it2agent's **broker the durable layer *under* native orchestration**: native clippings/provenance are ephemeral — it2agent can persist **handoffs, messages, and registry across restarts/crashes** and expose them to the native orchestrator (e.g. via the MCP surface or a cc-status-compatible bridge).
- Align **emit** to emit `cc-status`/OSC 21337 so the native Cockpit/tab-status "just works" for it2agent-spawned agents.
- Keep the **agent-agnostic CLI + MCP** as the way *external* agents (Claude Code CLI, Codex, scripts) plug into the durable broker without needing iTerm2's built-in chat.

## Bottom line
it2agent should stop competing with the native orchestration (status board, session-driving, review,
cockpit) and **double down on what's uniquely ours: the durable broker (messaging+ack+handoff),
per-agent port/service isolation, cost, janitor, tmux persistence, and the agent-agnostic CLI/MCP** —
positioned as the **durable, agent-agnostic layer that complements** iTerm2's native orchestration.

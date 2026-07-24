# it2agent

it2agent is a personal, iTerm2-based terminal for orchestrating multiple AI coding agents. It
spawns agents with an identity, gives each one isolation (its own git worktree, port, and optional
Docker/DB namespace), moves durable messages and handoffs between them through a small external
broker, and publishes agent state into iTerm2's native tab status — while cooperating with the
terminal's own native Claude Code orchestration and with Claude Code agent teams. It is a private
fork of iTerm2 for our own use; the terminal is the substrate, and every capability is an opt-in
feature flag that defaults **OFF**.

The project's own tooling lives under [`it2agent/`](it2agent/); the rest of the tree is the
iTerm2-based app, touched only in a few places (see [Repo layout](#repo-layout)).

---

## Requirements

You need a working macOS build of the app plus the Python bits that drive it.

- **macOS** (the app's deployment target is macOS 12+).
- **A development build of the app.** From the repo root:
  ```sh
  tools/build.sh Development
  ```
  (Build logs go to `tmp/build.log`; only errors/warnings print on failure. See the iTerm2 build
  notes preserved in the source tree for first-time `make setup` prerequisites.)
- **Python 3** with the **`iterm2`** package, for the live orchestration layer (daemon, spawn,
  MCP, live smoke):
  ```sh
  python3 -m pip install iterm2
  python3 -c "import iterm2"   # must succeed
  ```
- **Enable the iTerm2 Python API** (operator / GUI step): **Settings → General → Magic → Enable
  Python API**. Equivalent from the shell:
  ```sh
  defaults write com.googlecode.iterm2 EnableAPIServer -bool true
  defaults read  com.googlecode.iterm2 EnableAPIServer   # must print 1
  ```
- **Install the it2agent PATH wrappers** so the app and your shell find every CLI from anywhere:
  ```sh
  it2agent install          # writes wrappers into ~/.local/bin (override with --dir)
  ```
  From a fresh checkout, run it via the bundled dispatcher: `it2agent/it2agent install`.
- **Enable the native Claude Code integration** for the cooperation surfaces (operator / GUI step):
  in the running app, **menu → Install/Reinstall Claude Code Integration → Install Hook**.

---

## Quickstart

```sh
it2agent install                      # 1. put the wrappers on PATH (one time)
it2agent brief                        # 2. see what is turned on right now
it2agent help                         # 2b. full, always-current capability guide
it2agent-flag enable agent.native_status   # 3. turn on a capability (default OFF)
it2agent spawn --role backend --task "wire the API" -- $SHELL   # 4. spawn an agent tab
```

Everything is a feature flag and every flag defaults OFF — a capability does nothing until you
enable it. `it2agent-flag list` shows every flag and its state; `it2agent-flag enable|disable
agent.<key>` toggles one; `it2agent-flag agent.<key>` queries one (prints `1`, exit 0, when ON).

---

## Capabilities

Every capability is a per-user feature flag under `[features]` in
`$XDG_CONFIG_HOME/it2agent/config.toml` (falls back to `~/.config/it2agent/config.toml`), **all
default OFF**. Enable one with `it2agent-flag enable agent.<key>`. Full reference:
[`it2agent/docs/feature-flags.md`](it2agent/docs/feature-flags.md); the live, generated cheat-sheet
(flag → command/MCP tool → example) is [`it2agent/AGENT_GUIDE.md`](it2agent/AGENT_GUIDE.md).

| Flag (`agent.…`) | What it does | Enable when |
| --- | --- | --- |
| `status_board` | **Legacy** tab-color + user-var status board. Superseded by `native_status`. | Prefer `native_status`; kept only for compatibility. |
| `worktree_isolation` | Each agent gets its own git worktree and a dedicated `$PORT` so they never collide. | Running several agents against one repo. |
| `messaging` | Agent-to-agent messages across tabs, routed through the broker. | Agents need to hand work to each other. |
| `inbox` | Durable per-agent inbox so messages survive restarts. | You want delivery to outlive a crash. |
| `cost_dashboard` | Running dashboard of token usage and cost. | Watching spend during long sessions. |
| `janitor` | Background cleanup of stale worktrees and sessions. | Long-lived fleets accumulate cruft. |
| `mcp` | Exposes it2agent to your agents as an MCP server (9 tools). | Driving orchestration from an MCP-capable agent. |
| `daemon` | Orchestration daemon tracking agents and their idle/busy state. | You want live registry + idle detection. |
| `broker` | The durable broker — mailbox, registry, state, ack over a local socket. | Foundation for messaging/inbox/handoff/moat. |
| `review` | Per-agent diff view to approve-and-merge or request changes on a worktree. | Reviewing agent output before merge. |
| `tmux` | Runs agents inside a `tmux -CC` session so they survive quit/crash and reattach. | You need agents to outlive the app. |
| `claude_statusbar` | Status-bar item summarizing Claude Code sessions (Waiting/Working/Idle). | Watching many Claude sessions at a glance. |
| `menubar` | Menu-bar item with a live count of busy AI agents. | You want a global busy-count badge. |
| `codex_status` | Shows Codex CLI working/idle activity in the tab status. | Running Codex CLI agents. |
| `native_status` | Publishes agent state to iTerm2's native tab status + Cockpit via OSC 21337. | The recommended, cooperative status surface. |
| `team_bridge` | Mirrors Claude Code agent-teams state into the durable broker so it survives the lead session's death. | Running Claude Code agent teams (the moat). |
| `canonical_port` | The focused agent also answers on the normal localhost port (e.g. 3000), not just its dynamic one. | Testing a focused agent on its usual port. |
| `isolate_docker` | Sets `COMPOSE_PROJECT_NAME` per agent so Compose stacks don't collide. | Agents each bring up a Compose stack. |
| `isolate_db` | Exports a per-agent Postgres schema/search_path so agents don't share DB state. | Agents each need isolated DB state. |
| `autobrief` | On each Claude Code session start, injects a short it2agent brief so a fresh agent discovers the tooling. | You want agents to self-discover capabilities. |

The list above is the current schema (`KNOWN_FLAGS` in
[`it2agent/flags/it2agent_flag.py`](it2agent/flags/it2agent_flag.py)); `AGENT_GUIDE.md` is generated
from it, so it can never go stale.

---

## Architecture

**iTerm2 is the substrate** (spawn, tag, observe, inject, display, persist). Durable state that the
terminal has no concept of — a queue, a registry, shared state, delivery ack — lives in a **small
external broker**; the terminal is transport, not a router. A Python daemon bridges the two.

- **Broker** — sqlite/unix-socket process: durable mailbox, agent registry, append-only handoff
  history, exactly-once ack. `it2agent broker serve`.
- **Daemon** — iTerm2 Python API orchestration: registry, spawn-with-identity, idle/busy tracking.
  `it2agent daemon`.
- **Emit / native status** — agents write OSC 21337 so they appear in iTerm2's native tab status +
  Cockpit (`it2agent emit ccstatus`, flag `agent.native_status`).
- **Spawn + isolation** — `it2agent spawn` opens a tab with a stamped identity; with
  `worktree_isolation` each agent gets its own git worktree, dynamic port(s), and optional
  Docker/DB namespace.
- **MCP** — `it2agent mcp` exposes 9 tools (`spawn`, `assign`, `handoff`, `send_message`, `status`,
  `list_agents`, `team_tasks`, `read_messages`, `help`) over stdio JSON-RPC, backed by the daemon +
  broker.
- **Team bridge (the moat)** — an observer hook mirrors Claude Code agent-teams state into the
  broker so the task list and coordination survive the lead session's death.
- **Autobrief / discovery** — a SessionStart hook injects a short capability brief into fresh
  agents.

Deeper reading: [`design.md`](it2agent/docs/design.md) ·
[`native-vs-it2agent.md`](it2agent/docs/native-vs-it2agent.md) ·
[`cooperation-strategy.md`](it2agent/docs/cooperation-strategy.md) ·
[`runtime-isolation-upgrade.md`](it2agent/docs/runtime-isolation-upgrade.md) ·
[`feature-flags.md`](it2agent/docs/feature-flags.md).

---

## Testing

Two layers. The **pure/gate layer** needs no running terminal; the **live layer** drives a real
development build with the Python API on. A short how-to also lives in
[`it2agent/tests/README.md`](it2agent/tests/README.md).

### Pure gate (what CI runs)

```sh
sh it2agent/tests/run_headless.sh
```

Runs every `test_*.py` / `test_*.sh` under `it2agent/**/tests/` plus `live_smoke.py --only ccstatus`
(the one live surface that needs no terminal — exact OSC 21337 bytes). It isolates the flag config so
flags read OFF, matching a fresh CI host. Exit 0 iff every suite passes.

### Live gate (needs the dev build + Python API)

```sh
python3 it2agent/tests/live_smoke.py --json
```

Exercises the `spawn` / `tmux` / `mcp` surfaces against a running development build with the Python
API enabled. Fails on any surface that is not `PASS`.

### CI

- `.github/workflows/headless-tests.yml` — the pure layer, on every push/PR to `master`
  (hosted macOS runner).
- `.github/workflows/live-smoke.yml` — the live gate on a **self-hosted** macOS runner (label
  `it2agent-live`), on manual dispatch, a daily schedule, and PRs labelled `live`. Details:
  [`it2agent/docs/live-smoke-ci.md`](it2agent/docs/live-smoke-ci.md).
- `.github/workflows/test.yml` — the Python-API library + the app's Xcode tests.

### Moat validation (real team + kill-lead)

The headline claim is that agent-team coordination **survives the death of the lead session**. The
mechanics, durability, safety, and install are proven headless by a driver:

```sh
python3 it2agent/tests/coop_team_bridge_mirror.py
```

It stands up a broker, feeds the exact events Claude Code emits (`TaskCreated` / `TaskCompleted` /
`TeammateIdle`) into the observer hook, asserts observer-safety (always exit 0, zero stdout), checks
the mirror (register + handoff pending→completed + notify lead), proves idempotency, then **kills the
broker and restarts on the same db** — the team registration and task lifecycle are still there.

The one part that needs a human is a **real Claude Code team plus killing the lead** (see AC6 in
[`it2agent/tests/COOPERATION_VALIDATION_PROMPT.md`](it2agent/tests/COOPERATION_VALIDATION_PROMPT.md)):

1. Start the durable broker (bypass the flag gate for local testing):
   ```sh
   it2agent broker serve --no-gate &
   ```
2. Create a **throwaway** git repo (not a real project) and install the team-bridge hook
   project-locally into it (writes only that repo's gitignored `.claude/settings.local.json`):
   ```sh
   D="$(mktemp -d)/proj"; mkdir -p "$D"
   ( cd "$D" && git init -q && git commit -q --allow-empty -m init )
   ( cd "$D" && it2agent-team-hook install --scope project )
   ```
3. Turn on the experiment and the flag:
   ```sh
   export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
   it2agent-flag enable agent.team_bridge
   ```
4. **(Operator)** Inside `$D`, run a Claude Code session with a 2-teammate team that creates and
   completes a task, then goes idle.
5. Read the mirror through the MCP tools (no live Claude Code needed): `team_tasks` / `read_messages`
   (the `team` key is the session id or the derived `team:session-<sid8>`).
6. **(Operator)** **Kill the lead session** and re-query `team_tasks`: the mirror and task lifecycle
   must still be intact. That durability is the moat.

### Go-live / production readiness

| Area | Status |
| --- | --- |
| Core substrate (flags, broker, daemon, emit/native status, MCP, headless gate) | 🟢 daily use |
| Spawn, worktree/port isolation, tmux persistence | 🟡 dogfood — validate the live gate on your Mac |
| Moat (team bridge with a real team) + GUI (AI Agents settings pane) | 🔴 not signed off until validated with a real Claude Code team and a human in the loop |

---

## Convention: where Claude Code config goes

Any config it2agent needs Claude Code to pick up — hooks, env, MCP wiring — is **always** written to
the active project's `<git-root>/.claude/settings.local.json`: per-project, machine-local, and
gitignored. Never the committed `.claude/settings.json`, never global, unless you explicitly opt into
a wider scope. Presence in a project *is* the per-project opt-in ("installed = enabled"), and
install/uninstall are symmetric — uninstall removes only our entries. Full rationale:
[`it2agent/docs/claude-config-convention.md`](it2agent/docs/claude-config-convention.md).

---

## Repo layout

- **[`it2agent/`](it2agent/)** — the project's own tooling (the umbrella `it2agent` dispatcher and
  its subcommands, the feature-flag system, broker, daemon, emit, spawn/worktree, tmux, mcp, team
  bridge, review, janitor, cost, inbox, autobrief), the docs, and the test gates. This never modifies
  the terminal's source.
- **`sources/`** — the iTerm2-based app. Fork-direct native edits are deliberately narrow: the
  **AI-Agents settings pane** and a few small native fixes (for example, the vimdiff `/dev/null`
  fix). These live only in this personal fork and are never submitted upstream.

The umbrella front door — `it2agent <sub>` — forwards to the sibling `it2agent-<sub>` tool, so
`it2agent help`, `it2agent brief`, `it2agent broker serve`, `it2agent-flag list`, etc. all work from a
spawned agent's tab. Start with `it2agent help`.

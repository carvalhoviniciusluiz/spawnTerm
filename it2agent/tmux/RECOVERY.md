# Recovery & reattach (Tier 3, #5)

How an agent — and its window layout — come back after iTerm2 quits, the SSH
link drops, or iTerm2 crashes, and how that composes with the Tier 2 broker.

## The two things that must come back, and who owns each

| Thing | Owner | Survives a crash because… |
|-------|-------|---------------------------|
| The **process** (the running agent) + **window/tab/pane layout** | **tmux** (Tier 3) | tmux is a separate long-lived server; iTerm2 is just a *client* of it under `-CC`. Killing the client never kills the server. |
| The **durable messages / handoff / state** the agent was working from | **broker** (Tier 2, #4) | the broker is a sqlite-backed unix-socket service, independent of iTerm2 *and* tmux. |

`tmux -CC` brings the agent *back*; the broker tells it *where it was*. Neither
replaces the other. tmux holds no message history; the broker holds no process.

## What happens on a crash/disconnect

1. iTerm2 quits/crashes, or you disconnect (close the laptop, drop SSH).
2. The **tmux server keeps running.** Every agent spawned with
   `it2agent-tmux spawn` (flag ON) is a process inside a tmux session named
   `st-<agent-id>` — it keeps executing, detached.
3. The **broker keeps running** (it is not tied to iTerm2). Any messages/handoff
   an agent had not yet consumed stay durably queued with ack semantics (#35).
4. The Tier 1 **daemon** loses its API connection when iTerm2 dies. On restart
   it re-seeds its registry from live sessions (`_seed_from_app`), so once you
   reattach (below), the reattached sessions are re-registered automatically.

## Reattaching

From any iTerm2 tab (a fresh launch is fine):

```sh
# Reopen a specific agent's session by the same identity you spawned it with:
it2agent/tmux/it2agent-tmux attach --role worker --task "build #5"
# …or by explicit id / session name:
it2agent/tmux/it2agent-tmux attach --id 5
it2agent/tmux/it2agent-tmux attach --session st-worker-build-5
```

This runs `tmux -CC attach -t st-<id>` in a new iTerm2 tab. iTerm2 re-establishes
native integration and **restores the windows/tabs/panes** for that tmux session,
with the agents still alive exactly where they were.

To see what is still alive first:

```sh
tmux ls            # lists surviving sessions: st-worker-build-5, st-reviewer-…, …
```

`spawn` itself is also recovery-safe: `it2agent-tmux spawn …` uses
`tmux new-session -A`, which **attaches if the session already exists** and only
creates (and runs the agent command + identity emits) if it does not. So
re-running the exact spawn command after a crash reattaches rather than starting
a duplicate agent. Because session names are derived deterministically from the
agent id (see `it2agent-tmux name`), the same agent always maps to the same
session — collision-safe by construction.

## How a reattached agent *resumes* (the broker half)

Restoring the process is not the same as restoring context. A well-behaved
it2agent agent, on (re)start, pulls its durable state from the broker:

1. its **inbox** — unacked messages replay in order (#35), so nothing addressed
   to it during the outage is lost;
2. its **handoff / state record** (#36) — the last checkpoint of what it was
   doing, written by itself or by a coordinating agent.

So the recovery flow is: **tmux restores the agent and its layout → the daemon
re-registers the session → the agent drains its broker inbox/handoff and picks
up where it left off.** The identity emits (`agent_role/task/status`, tab color,
badge) were stamped at first spawn; the daemon re-snapshots them on re-seed.

## Caveats (and the honest unknowns)

- The daemon↔agent *escape-code* channel (custom control sequences, user vars)
  under tmux is the surface most at risk of being swallowed by tmux — see
  `API_VALIDATION.md`. If a real run shows raw OSC 1337 does not survive tmux,
  the agent-side emit must wrap in tmux passthrough. That does **not** affect
  recovery of the process/layout (owned by tmux) or of messages (owned by the
  broker over its own socket, not through iTerm2 at all).
- Reboots: a machine reboot kills the tmux server too. True cross-reboot
  persistence is out of scope for #5 (it would need `tmux-resurrect`-style
  session save, or the broker replaying enough state to re-spawn). The broker
  state survives a reboot (it is on disk); the tmux processes do not.

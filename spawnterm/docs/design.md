# spawnTerm — design & iTerm2 capability map

Durable rationale behind the tiers. Read with **Epic #1**. TL;DR: **iTerm2 is the substrate; a small
external broker owns durable state.** Do not invert that.

## The problem
Multiple AI agents run in separate iTerm2 tabs/panes. Coordination today is manual and lossy: no
at-a-glance status, no reliable cross-tab messaging, handoffs are files someone must find, humans
can't easily observe/intervene, and spawned agents carry no identity the terminal knows.

## Architecture verdict
iTerm2 gives two complementary control planes:
- **(A) In-band escape codes** — any agent writes bytes to its own stdout to signal state.
- **(B) Out-of-band Python API** — a single websocket daemon observes and drives *every* session.

Enough to spawn, tag, observe, route, and display. But iTerm2 has **no durable queue, no queryable
registry, no persistent shared state, no delivery ack, no cross-session bus**. Those belong in an
**external broker** (Tier 2). iTerm2 is transport + substrate, **not** a router or state store. The
Python daemon (Tier 1) bridges the broker to live sessions.

## Capability map (what each tier builds on)
**A — escape codes (Tier 0), affects only the emitting session:**
`OSC 1337 ; SetUserVar=agent_status=<b64>` (state; iTerm2 forbids `.` in the key) · `SetColors`/tab color · `SetMark` · `AddAnnotation`
· `RequestAttention=yes` (needs-human) · `OSC 9 ; <msg>` (notification) · `OSC 9 ; 4 ; state ; pct`
(progress) · badge interpolates user vars. Cheap, no daemon.

**B — Python API (Tier 1):** create tabs/windows with `command` + tag via `async_set_variable("user.*")`
· stable `session_id` · subscribe to `new_session`/`terminate_session` (registry), `custom_escape_sequence`
(agent→daemon ingest), `prompt` (idle), screen updates · read any session's screen (`async_get_screen_contents`)
· inject into any session (`async_send_text`) · custom status-bar component (dashboard).

**Triggers** (regex→action): "Invoke Script Function", "Post Notification", "Set User Variable",
"Send Text", "Capture Output" — declarative automation with zero daemon code.

**tmux `-CC` (Tier 3):** iTerm2's only native persistence — windows reopen in the same state after
quit/disconnect; agents survive crashes. Validate the Python API works over tmux-CC sessions.

## What iTerm2 CANNOT do → external broker (Tier 2)
Durable message queue · cross-session bus · agent registry/identity/liveness · durable shared state ·
delivery ack. Custom control sequences are **fire-and-forget** (lost if the daemon isn't subscribed;
no replay/ack/order). → sqlite + unix-socket broker; the Tier 1 daemon relays; ack by observing the
target's screen/vars.

## Tier 4 (iTerm2 core, fork-direct — only where the API can't reach)
iTerm2 core changes made **directly in this personal fork** (never submitted upstream), backed by real
usage evidence: optional delivery ack on `async_send_text`; native queryable session registry;
persisted user-vars. **Not** a broker in iTerm2 (wrong layer).

## Guardrails
- Tiers 0–3 = `scope:external-tooling`, live in `spawnterm/`, never edit iTerm2 source.
- Tier 4 = `scope:iterm2-core`: changes to iTerm2 source are made directly in this personal fork; never
  submitted upstream (see the "Fork policy" comment on Epic #1). Built + tested here.
- The issue comment thread is the durable log; one PR per issue; `Closes #N`.

Sources: iTerm2 docs (escape-codes, python-api, triggers, coprocesses, status-bar, tmux-integration).

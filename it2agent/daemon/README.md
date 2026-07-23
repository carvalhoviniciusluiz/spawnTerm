# it2agent/daemon

Tier 1.1 (#26, sub-task of #3) — the iTerm2 Python API orchestration daemon.
This is the **foundation** of Tier 1; #27/#28/#29 build on it.
`scope:external-tooling` — it runs *on* iTerm2's Python API and **never** modifies
iTerm2 source.

The daemon connects to the iTerm2 Python API websocket, survives the session via
`iterm2.run_forever`, and maintains an **in-memory, ephemeral** registry of
sessions. It ingests agent messages (parses, logs, and best-effort routes them
between sessions — #28) and tracks which sessions are idle / awaiting input.

> The durable queue / registry-as-source-of-truth is **Tier 2 (#4)** and is out
> of scope here. This registry is rebuilt from live iTerm2 state on every start.

## Enable the iTerm2 Python API

The daemon needs iTerm2's Python API turned on:

**iTerm2 → Settings → General → Magic → “Enable Python API”.**

Auth is the standard iTerm2 mechanism — the `iterm2` library reads the API
cookie (or the `ITERM2_COOKIE` environment variable). The daemon does **not**
hand-roll auth.

Install the client library (the only external dependency, imported lazily so the
pure modules and tests never need it):

```sh
pip3 install iterm2
```

## Feature flag: `agent.daemon`

Like every it2agent capability, the daemon is an individually toggleable,
per-user feature flag that **defaults OFF** (see `it2agent/docs/feature-flags.md`).
The daemon **refuses to start** when the flag is OFF: it prints a message and
exits `0`.

```sh
it2agent-flag enable agent.daemon     # turn it on
it2agent-flag agent.daemon            # query (1/exit0 = on)
it2agent-flag disable agent.daemon    # turn it off
```

The gate is checked by importing the #11 helper (`it2agent_flag.is_enabled`),
falling back to shelling out to `it2agent-flag`. Bypass for local testing with
`--no-gate` or `IT2AGENT_FORCE=1`.

## Run

```sh
python3 it2agent/daemon/it2agent_daemon.py            # gated on agent.daemon
python3 it2agent/daemon/it2agent_daemon.py --no-gate  # local testing, ignore the flag
python3 it2agent/daemon/it2agent_daemon.py -v         # debug logging
```

Logs go to **stderr**. Exit codes: `0` (clean / gated-off), `1` (the `iterm2`
package is missing).

## Spawn a tagged agent (Tier 1.2, #27)

The `spawn` subcommand opens a **new iTerm2 tab** running an agent via the Python
API, inheriting the spawner's working directory and stamping the new session's
identity (the dot-free `user.agent_*` vars):

```sh
# Inherit the spawner's cwd, tag a worker, run Claude:
python3 it2agent/daemon/it2agent_daemon.py spawn \
    --role worker --task "build #27" --id a1 -- claude --dangerously-skip-permissions

# Open in a specific folder / in $HOME:
python3 it2agent/daemon/it2agent_daemon.py spawn --dir ~/proj/api --role reviewer -- "$SHELL" -l
python3 it2agent/daemon/it2agent_daemon.py spawn --home -- "$SHELL" -l
```

**Working directory (the #3 quick win).** Precedence `--home` > `--dir` >
inherit the spawner's cwd (the default). `--home` and `--dir` are mutually
exclusive (exit `2`).

| Selection | Resolved cwd |
| --- | --- |
| *(default)* | The spawner's cwd — the new tab inherits it. |
| `--dir <path>` | That specific folder. |
| `--home` | The user's home directory (`$HOME`). |

**Identity flags.** `--role` / `--task` / `--id` / `--status` become the ordered
dot-free assignments `user.agent_id`, `user.agent_role`, `user.agent_task`,
`user.agent_status`. Empty values are skipped; `--status` defaults to `busy`.

**Gate.** Identity tagging gates on **`agent.status_board`** (the same flag
`it2agent-emit` self-gates on — *not* `agent.daemon`). Spawning a tab is
core and **always** proceeds; when `status_board` is OFF the plan carries an
empty variable list, so the tab opens **untagged**. Bypass with `--no-gate` or
`IT2AGENT_FORCE=1`. Because spawning is core, the `spawn` subcommand is routed
*before* the `agent.daemon` start-gate and is not blocked by it.

### Shell wrapper (#10) vs this daemon path

Two ways to spawn a tagged agent — use whichever matches your setup:

| Use the **shell** path (`it2agent/spawn/it2agent-spawn`, #10) | Use **this daemon** path (`it2agent-daemon spawn`) |
| --- | --- |
| Stock iTerm2, **no daemon / Python API** required. | The Python API is enabled and you already run on it. |
| Opens the tab via **AppleScript** and stamps identity by writing `it2agent-emit` calls + a `cd` **into the new session**. | Opens the tab via **`Window.async_create_tab`** and sets identity through **`async_set_variable`** on the API. |
| Zero Python deps. | Needs the `iterm2` package. |

Both honor the same cwd precedence and the same `agent.status_board` gate, so
they are interchangeable from the operator's point of view. The pure plan logic
lives in `spawn.py`; the shell wrapper is the daemon-free twin.

## What it subscribes to

| iTerm2 monitor | Effect on the registry |
| --- | --- |
| `NewSessionMonitor` (`new_session`) | **add** a `SessionRecord` keyed by `session_id` (title, cwd, agent vars). |
| `SessionTerminationMonitor` (`terminate_session`) | **remove** the record. |
| `CustomControlSequenceMonitor` (`custom_escape_sequence`, id `it2agent`) | **ingest**: parse + structured-log the envelope, then best-effort **route** it (#28). |
| `PromptMonitor` (`prompt`, one per session) | mark the session **idle / awaiting input**. |

## Agent identity user vars (dot-free)

iTerm2 forbids `.` in a user-var key, so the emitter (#7) writes **dot-free**
names — the API surfaces them under the `user.` namespace:

`user.agent_status` · `user.agent_role` · `user.agent_task` · `user.agent_id`

The registry keys on those dot-free names (`agent_status`, `agent_role`,
`agent_task`, `agent_id`).

## Agent → daemon message envelope

Agents signal the daemon with an iTerm2 **custom control sequence**:

```
OSC 1337 ; Custom=id=it2agent : <json payload> ST
```

The payload is a small JSON envelope; only `v` and `type` are required:

```json
{"v": 1, "type": "msg", "to": "<agent_id>", "from": "<agent_id>", "body": "..."}
```

Parsing is **defensive** — malformed input is logged and dropped, never crashes
the daemon. `parse_envelope` is reused by the router in #28.

## Routing (`agent.messaging`, Tier 1.3, #28)

A parsed envelope is handed to the pure `router` module, which resolves the
`to` field against the live registry and returns a structured `RoutingDecision`
(target session ids + the exact text to inject, or an undeliverable reason). On
a deliverable decision the adapter calls `Session.async_send_text` into each
target. The injected line is prefixed `[it2agent] message from <sender>: …` so
the receiving agent can tell a relayed message apart from local input.

**Resolution precedence** for `to`:

1. **`agent_id`** — an exact id match wins outright; nothing else is considered.
2. **`agent_role`** — fallback only when *no* session matched by id. **All**
   sessions in that role receive the message (fan-out).
3. otherwise → undeliverable (`no match`).

The sender's own session is never a target (self-send guard). Undeliverable
reasons: `messaging disabled`, `no destination`, `empty body`, `no match`,
`self`.

**Gate.** Routing is gated on the `agent.messaging` feature flag, which —
like every capability — **defaults OFF**. When it is OFF the daemon still
parses and logs each ingested envelope but does **not** route. Turn it on with
`it2agent-flag enable agent.messaging`.

> **Best-effort in-memory relay (the #28 fallback).** No queue, replay, ack,
> retry, or ordering. If the target session is absent the message is
> undeliverable; if it is present the text is injected and may be lost if the
> agent is busy. The durable inbox + ack path is **Tier 2 (#4)** — precisely the
> gap the #37 bridge below closes when the broker is up.
>
> **Re-scope (#100):** the **canonical** messaging path is the durable broker
> (Tier 2). This in-memory router is kept **only** as the degraded fallback the
> #37 bridge selects when the broker is unreachable — not a standalone feature.
> No API or default change; `agent.messaging` still means "route via the broker
> when it is up, in-memory only when it is not." See `native-vs-it2agent.md`.

## Daemon↔broker bridge (Tier 2.4, #37)

The bridge is the **glue** that upgrades the #28 in-memory relay to the Tier 2
**durable broker** (`it2agent/broker`, #34/#35/#36) whenever the broker is
reachable, and falls back to #28 when it is not. It is pure glue: it does **not**
reimplement iTerm2 transport (it reuses this daemon) and does **not** put durable
state in iTerm2 (it reuses the broker). All the decision logic lives in the
iTerm2-free `bridge.py` (unit-tested with a fake broker client + fake screen
text in `tests/test_bridge.py`); `adapter.py` only supplies the iTerm2 reads and
writes.

### Two flags: `agent.messaging` × `agent.broker`

| `agent.messaging` | `agent.broker` (+ broker reachable) | Mode |
| --- | --- | --- |
| OFF | — | **off** — parse/log only, no relay at all |
| ON | OFF or unreachable | **in-memory** — the #28 best-effort router |
| ON | ON and reachable | **durable** — broker mailbox + ack |

The **durable** path needs **both** flags on *and* a live broker; messaging-only
(or a down broker) falls back to in-memory; messaging-off is a no-op. On startup
the daemon best-effort-connects to the broker (`bridge.connect_broker` pings it);
a missing/down broker just means no client, so the daemon runs in-memory only.
The daemon **never crashes because the broker is down** — every broker call is
wrapped and degrades on failure.

### Ingest → durable send

An ingested `custom_escape_sequence` envelope is handed to
`Bridge.handle_ingest`. In **durable** mode it becomes a `broker send
{to,from,body}` (enqueued in the durable mailbox — delivery happens later by
polling) instead of the in-memory route. If that send fails, ingest degrades to
the #28 in-memory route for that message and logs `degraded`.

### Delivery → poll + inject + ack-by-observation

A delivery poll loop (`_delivery_loop`, ~1 s cadence, only when a broker is
present) drives `Bridge.deliver_once`: for every recipient key addressable by a
live session (the union of each session's `agent_id` / `agent_role`), it `poll`s
the mailbox, resolves each un-acked message against the live registry with the
same #28 precedence (id-before-role, fan-out, self-guard), and injects the
delivery line `[it2agent#<id>] message from <sender>: <body>` into each target.

**Ack-by-observation heuristic.** After injecting, the daemon reads the target's
visible screen (`async_get_screen_contents`). A message counts as **observed**
iff its unique marker `[it2agent#<id>]` appears in that screen text — i.e. the
injected bytes reached the session and were echoed. Only an observed message is
`ack`ed on the broker; an un-observed one is left un-acked and **replayed** on
the next poll (at-least-once until observed). The predicate is the pure
`was_observed(screen_text, marker)` — unit-tested in isolation (marker present →
ack; absent/empty → no ack). (Edge: a self-addressed message has no non-sender
target, so it is never observed and stays durable — acceptable, it is the
sender's own message.)

### Registry population (#36)

Daemon lifecycle events keep the **durable** broker registry in sync with live
sessions (gated on `agent.broker`): `new_session` → `broker register
{session_id, role, task, alive:true}`, `terminate_session` → `broker touch
{session_id, alive:false}`. The role/task come from the dot-free `user.agent_*`
vars read at register time. This is independent of the messaging relay — the
registry reflects liveness even with messaging off.

## Inbound native-state read (#115)

Cooperation is otherwise **outbound** — we publish into the native surfaces
(OSC 21337 tab status via `it2agent-emit ccstatus`, gate `agent.native_status`).
The daemon adds a small **one-way inbound** read: at startup it enumerates the
live iTerm2 sessions over the Python API and reflects what *native* knows about
each — its name, the dot-free `user.agent_*` vars, and the native **cc-status /
OSC 21337 tab-status** text — into our registry, so it2agent tools see what
native sees. It is **read-only** on the native side and writes **only** our own
registry (and, gated on `agent.broker`, the durable broker registry via the same
`register` op as lifecycle events). It does **not** duplicate the Cockpit.

The mapping is **pure and unit-tested** (`inbound.py`,
`tests/test_inbound.py`): a session-record dict → a `RegistryOp`. Status
precedence mirrors the native `WorkgroupIntrospection`: an explicit
`agent_status` var wins; absent that, the native cc-status `statusText`
(`working` / `waiting` / `idle`) is translated into our lifecycle vocabulary
(`busy` / `blocked` / `idle`). Gating matches the rest of the daemon — it runs
only inside the live `run()` loop (daemon up + Python API reachable); with no
connection, or when the `iterm2` module is absent, it is a **clean no-op** and a
bad per-session read degrades to an omitted field rather than aborting.

## Agent dashboard (status-bar component, #29)

> **Re-scope note (#100):** this status-bar dashboard **duplicates the native
> Cockpit** and its OSC 21337 tab status. Do **not** invest in it as a second
> board — point users at the native **tab status + Cockpit** (fed by
> `it2agent-emit ccstatus`, gate `agent.native_status`, #88). It stays behind
> `agent.status_board` (default OFF); no code removal, no default change. See
> `native-vs-it2agent.md` (Path 3 / #29).

The daemon can register a custom **iTerm2 status-bar component** that shows, per
session, the agent's role + lifecycle status (+ task if it fits), e.g.
`▶ backend: busy — build #29`. It re-renders whenever the session's
`user.agent_role` / `user.agent_status` / `user.agent_task` variables change.

Statuses map to a glyph + the colorblind-safe Okabe-Ito lifecycle palette (the
same values as `it2agent/emit/docs/colors.md`, #8), with a shape-distinct glyph
so the state reads without color:

| Status | Glyph | Color |
| --- | --- | --- |
| `busy` | `▶` | `0072B2` (blue) |
| `blocked` | `⚠` | `E69F00` (orange) |
| `done` | `✓` | `009E73` (bluish green) |
| `idle` | `○` | `999999` (gray) |

A missing role degrades to `agent`; a missing or unrecognized status degrades to
`idle`; the task is truncated with `…` when it nearly fits and omitted when there
is no room.

### Gate: `agent.status_board` (default OFF)

The component is registered **only** when the `agent.status_board` feature
flag is ON — the same flag the Tier 0 emitter uses — checked through the shared
`it2agent-flag` helper (#11), not a new flag. When it is OFF (or the helper is
unreachable) the component is **not registered** and nothing is shown. Bypass
for local testing with `IT2AGENT_FORCE=1`.

```sh
it2agent-flag enable agent.status_board   # allow the dashboard component
```

### Add it to the status bar

Once the daemon is running with the flag on, add the component in iTerm2:

**iTerm2 → Settings → Profiles → Session → Status bar → Configure Status Bar**,
then drag **“it2agent Agent”** from the Components list into the layout.

### Where the code lives

All of #29 lives in `dashboard.py`: the **pure** formatter core
(`format_component`, `style_for_status`, `PALETTE` — no `iterm2` import,
unit-tested in `tests/test_dashboard.py`) plus the `iterm2.StatusBarComponent`
wiring, which imports `iterm2` **lazily** inside the wiring functions. The daemon
touches it through a single hook: `it2agent_daemon.py` calls
`maybe_register_dashboard(connection, logger)`, which no-ops when the gate is
closed.

## Architecture (testability)

iTerm2 is not available in CI, so the real logic is **pure** and importable
without the `iterm2` package:

| Module | iTerm2? | Role |
| --- | --- | --- |
| `registry.py` | **pure** | `Registry` + `SessionRecord`: add/remove/update/query + idle transition. |
| `envelope.py` | **pure** | `parse_envelope` → `Envelope` / `ParseResult`. |
| `spawn.py` | **pure** | `build_spawn_plan` → `SpawnPlan`: cwd resolution + ordered dot-free identity vars (#27). |
| `router.py` | **pure** | `route` / `route_if_enabled` → `RoutingDecision`; `messaging_enabled` gate (#28). |
| `bridge.py` | **pure** (+ in-repo broker client I/O; no `iterm2`) | `Bridge` + `select_mode` / `build_send_op` / `was_observed` / `recipient_keys` / registry-op builders: durable-vs-in-memory glue, ack-by-observation, `connect_broker` (#37). |
| `dashboard.py` | pure core + lazy `import iterm2` | `format_component` / `PALETTE` (pure, tested) + the `StatusBarComponent` wiring (#29). |
| `adapter.py` | lazy `import iterm2` | `DaemonAdapter`: monitor events → registry + broker registry; `spawn_agent` executes a `SpawnPlan`; hands ingest to the bridge; delivery poll loop + the injected screen read/send. |
| `it2agent_daemon.py` | lazy `import iterm2` | entry point: CLI, flag gate, `run_forever`, `spawn` subcommand. |

`iterm2` is imported **only inside methods**, never at module top-level, so the
pure path imports and tests cleanly.

## Tests

```sh
bash it2agent/daemon/tests/run_tests.sh
```

Pure Python, no `iterm2` needed. Covers registry add/remove/update, the idle
transition, envelope parse (valid + every malformed case), spawn-plan cwd
resolution (inherit / `--dir` / `--home` / mutual-exclusion) and the identity var
plan (dot-free names, order, gate-off omits identity), routing (id/role
precedence, fan-out, self-guard, undeliverable cases, the `agent.messaging`
gate), the #37 bridge (mode selection across both flags, envelope→`send` op,
durable/in-memory/degraded ingest, delivery polling, ack-by-observation, and
registry population — all with a fake broker client + fake screens, no live
socket), the status-bar dashboard formatter (#29), the default-OFF daemon flag
gate, and the guarantee that no daemon module imports `iterm2` at load time.

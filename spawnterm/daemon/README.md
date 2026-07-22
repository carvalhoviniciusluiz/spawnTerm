# spawnterm/daemon

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

## Feature flag: `spawnterm.daemon`

Like every spawnTerm capability, the daemon is an individually toggleable,
per-user feature flag that **defaults OFF** (see `spawnterm/docs/feature-flags.md`).
The daemon **refuses to start** when the flag is OFF: it prints a message and
exits `0`.

```sh
spawnterm-flag enable spawnterm.daemon     # turn it on
spawnterm-flag spawnterm.daemon            # query (1/exit0 = on)
spawnterm-flag disable spawnterm.daemon    # turn it off
```

The gate is checked by importing the #11 helper (`spawnterm_flag.is_enabled`),
falling back to shelling out to `spawnterm-flag`. Bypass for local testing with
`--no-gate` or `SPAWNTERM_FORCE=1`.

## Run

```sh
python3 spawnterm/daemon/spawnterm_daemon.py            # gated on spawnterm.daemon
python3 spawnterm/daemon/spawnterm_daemon.py --no-gate  # local testing, ignore the flag
python3 spawnterm/daemon/spawnterm_daemon.py -v         # debug logging
```

Logs go to **stderr**. Exit codes: `0` (clean / gated-off), `1` (the `iterm2`
package is missing).

## What it subscribes to

| iTerm2 monitor | Effect on the registry |
| --- | --- |
| `NewSessionMonitor` (`new_session`) | **add** a `SessionRecord` keyed by `session_id` (title, cwd, agent vars). |
| `SessionTerminationMonitor` (`terminate_session`) | **remove** the record. |
| `CustomControlSequenceMonitor` (`custom_escape_sequence`, id `spawnterm`) | **ingest**: parse + structured-log the envelope, then best-effort **route** it (#28). |
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
OSC 1337 ; Custom=id=spawnterm : <json payload> ST
```

The payload is a small JSON envelope; only `v` and `type` are required:

```json
{"v": 1, "type": "msg", "to": "<agent_id>", "from": "<agent_id>", "body": "..."}
```

Parsing is **defensive** — malformed input is logged and dropped, never crashes
the daemon. `parse_envelope` is reused by the router in #28.

## Routing (`spawnterm.messaging`, Tier 1.3, #28)

A parsed envelope is handed to the pure `router` module, which resolves the
`to` field against the live registry and returns a structured `RoutingDecision`
(target session ids + the exact text to inject, or an undeliverable reason). On
a deliverable decision the adapter calls `Session.async_send_text` into each
target. The injected line is prefixed `[spawnterm] message from <sender>: …` so
the receiving agent can tell a relayed message apart from local input.

**Resolution precedence** for `to`:

1. **`agent_id`** — an exact id match wins outright; nothing else is considered.
2. **`agent_role`** — fallback only when *no* session matched by id. **All**
   sessions in that role receive the message (fan-out).
3. otherwise → undeliverable (`no match`).

The sender's own session is never a target (self-send guard). Undeliverable
reasons: `messaging disabled`, `no destination`, `empty body`, `no match`,
`self`.

**Gate.** Routing is gated on the `spawnterm.messaging` feature flag, which —
like every capability — **defaults OFF**. When it is OFF the daemon still
parses and logs each ingested envelope but does **not** route. Turn it on with
`spawnterm-flag enable spawnterm.messaging`.

> **Best-effort only (non-goal here).** This is an in-memory relay: no queue,
> replay, ack, retry, or ordering. If the target session is absent the message
> is undeliverable; if it is present the text is injected and may be lost if the
> agent is busy. The durable inbox + ack path is **Tier 2 (#4)** — precisely the
> gap this best-effort relay leaves open.

## Agent dashboard (status-bar component, #29)

The daemon can register a custom **iTerm2 status-bar component** that shows, per
session, the agent's role + lifecycle status (+ task if it fits), e.g.
`▶ backend: busy — build #29`. It re-renders whenever the session's
`user.agent_role` / `user.agent_status` / `user.agent_task` variables change.

Statuses map to a glyph + the colorblind-safe Okabe-Ito lifecycle palette (the
same values as `spawnterm/emit/docs/colors.md`, #8), with a shape-distinct glyph
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

### Gate: `spawnterm.status_board` (default OFF)

The component is registered **only** when the `spawnterm.status_board` feature
flag is ON — the same flag the Tier 0 emitter uses — checked through the shared
`spawnterm-flag` helper (#11), not a new flag. When it is OFF (or the helper is
unreachable) the component is **not registered** and nothing is shown. Bypass
for local testing with `SPAWNTERM_FORCE=1`.

```sh
spawnterm-flag enable spawnterm.status_board   # allow the dashboard component
```

### Add it to the status bar

Once the daemon is running with the flag on, add the component in iTerm2:

**iTerm2 → Settings → Profiles → Session → Status bar → Configure Status Bar**,
then drag **“spawnTerm Agent”** from the Components list into the layout.

### Where the code lives

All of #29 lives in `dashboard.py`: the **pure** formatter core
(`format_component`, `style_for_status`, `PALETTE` — no `iterm2` import,
unit-tested in `tests/test_dashboard.py`) plus the `iterm2.StatusBarComponent`
wiring, which imports `iterm2` **lazily** inside the wiring functions. The daemon
touches it through a single hook: `spawnterm_daemon.py` calls
`maybe_register_dashboard(connection, logger)`, which no-ops when the gate is
closed.

## Architecture (testability)

iTerm2 is not available in CI, so the real logic is **pure** and importable
without the `iterm2` package:

| Module | iTerm2? | Role |
| --- | --- | --- |
| `registry.py` | **pure** | `Registry` + `SessionRecord`: add/remove/update/query + idle transition. |
| `envelope.py` | **pure** | `parse_envelope` → `Envelope` / `ParseResult`. |
| `router.py` | **pure** | `route` / `route_if_enabled` → `RoutingDecision`; `messaging_enabled` gate. |
| `dashboard.py` | pure core + lazy `import iterm2` | `format_component` / `PALETTE` (pure, tested) + the `StatusBarComponent` wiring (#29). |
| `adapter.py` | lazy `import iterm2` | `DaemonAdapter`: translates iTerm2 monitor events into registry calls; delivers routed text. |
| `spawnterm_daemon.py` | lazy `import iterm2` | entry point: CLI, flag gate, `run_forever`. |

`iterm2` is imported **only inside methods**, never at module top-level, so the
pure path imports and tests cleanly.

## Tests

```sh
bash spawnterm/daemon/tests/run_tests.sh
```

Pure Python, no `iterm2` needed. Covers registry add/remove/update, the idle
transition, envelope parse (valid + every malformed case), routing (id/role
precedence, fan-out, self-guard, undeliverable cases, the `spawnterm.messaging`
gate), the default-OFF daemon flag gate, and the guarantee that no daemon module
imports `iterm2` at load time.

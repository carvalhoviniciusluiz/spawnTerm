# spawnterm/daemon

Tier 1.1 (#26, sub-task of #3) — the iTerm2 Python API orchestration daemon.
This is the **foundation** of Tier 1; #27/#28/#29 build on it.
`scope:external-tooling` — it runs *on* iTerm2's Python API and **never** modifies
iTerm2 source.

The daemon connects to the iTerm2 Python API websocket, survives the session via
`iterm2.run_forever`, and maintains an **in-memory, ephemeral** registry of
sessions. It ingests agent messages (logs them — routing is #28) and tracks
which sessions are idle / awaiting input.

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
| `CustomControlSequenceMonitor` (`custom_escape_sequence`, id `spawnterm`) | **ingest**: parse the envelope and structured-log it (routing is #28). |
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

## Architecture (testability)

iTerm2 is not available in CI, so the real logic is **pure** and importable
without the `iterm2` package:

| Module | iTerm2? | Role |
| --- | --- | --- |
| `registry.py` | **pure** | `Registry` + `SessionRecord`: add/remove/update/query + idle transition. |
| `envelope.py` | **pure** | `parse_envelope` → `Envelope` / `ParseResult`. |
| `adapter.py` | lazy `import iterm2` | `DaemonAdapter`: translates iTerm2 monitor events into registry calls. |
| `spawnterm_daemon.py` | lazy `import iterm2` | entry point: CLI, flag gate, `run_forever`. |

`iterm2` is imported **only inside methods**, never at module top-level, so the
pure path imports and tests cleanly.

## Tests

```sh
bash spawnterm/daemon/tests/run_tests.sh
```

Pure Python, no `iterm2` needed. Covers registry add/remove/update, the idle
transition, envelope parse (valid + every malformed case), the default-OFF flag
gate, and the guarantee that no daemon module imports `iterm2` at load time.

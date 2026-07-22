# spawnterm/inbox

Tier 3 (#17, needs #4 broker) — the **human-attention router / agent inbox**.
Babysitting N agents does not scale; the inbox interrupts a human **only when
needed**. It is a durable queue of agent requests, gated by a policy over
**reversibility + scope + cost** (the LangChain Agent-Inbox model), with
attention routed to the exact iTerm2 pane and an approve / edit / reject surface.

`scope:external-tooling` — pure policy + workflow glue. It **reuses** the broker
(#4, for durable state) and the emitter (#7, for attention); it does **not**
reinvent transport or state, and it never imports or modifies iTerm2 source. No
external deps (stdlib + the in-repo broker client only).

## Feature flag: `spawnterm.agent_inbox`

Like every spawnTerm capability, an individually toggleable, per-user flag that
**defaults OFF** (seeded in #11). When OFF the inbox is a **no-op**: `submit`
enqueues nothing, `list` shows nothing, decisions record nothing — and the broker
is never touched.

```sh
spawnterm-flag enable spawnterm.agent_inbox     # turn it on
spawnterm-flag spawnterm.agent_inbox            # query (1/exit0 = on)
```

The gate reuses the #11 `spawnterm_flag.is_enabled` helper (falling back to the
`spawnterm-flag` binary on PATH, then to OFF). Bypass locally with `--no-gate` or
`SPAWNTERM_FORCE=1` — the same fail-safe convention as the emitter and daemon.

## The policy model (pure, unit-tested)

Every request carries an **action descriptor**: `action` plus the three axes the
policy reasons over — is it `reversible`, what is its `scope`, what does it
`cost`. `policy.classify(request, config)` is a total, side-effect-free function
returning one of three decisions:

| Decision | Meaning |
| --- | --- |
| `auto_approve` | Safe/read-only allow-listed action — runs itself, no human. |
| `needs_human` | Queued for a human to approve / edit / reject. |
| `block` | Refused outright; never reaches a human. |

Rules, first match wins (`PolicyResult.rule` names the one that fired):

1. **BLOCK** — action in `block_list`; OR `block_cost` set and cost exceeds it;
   OR the action is **irreversible** and its scope is in `block_scopes`.
2. **AUTO_APPROVE** — action in `allow_list` **and** every guard holds: scope in
   `auto_scopes`, cost within `max_auto_cost`, and (unless
   `require_reversible_for_auto` is off) reversible. **Deny-by-default:** an
   action absent from `allow_list` is never auto-approved. An allow-listed action
   that trips a guard is *downgraded* to `needs_human` (not blocked), and the
   reason says which guard failed.
3. **NEEDS_HUMAN** — everything else.

The engine (`policy.py`) is pure: no file reading, no broker, no clock. Config
loading lives in `config.py`; the axes/allow-list combinations are covered in
`tests/test_policy.py`.

## Auto-approve allow-list config

The allow-list (and the other policy knobs) is a small TOML file, resolved like
the flags config:

```
$SPAWNTERM_INBOX_CONFIG  >  $XDG_CONFIG_HOME/spawnterm/inbox.toml  >  ~/.config/spawnterm/inbox.toml
```

```toml
[policy]
allow_list = ["git.status", "git.diff", "fs.read"]   # eligible for auto-approve
block_list = ["fs.rm_rf"]                             # refused outright
auto_scopes = ["read"]                                # safe scopes for auto
block_scopes = ["system"]                             # irreversible here => block
max_auto_cost = 0.0                                   # auto only at/below this cost
block_cost = 5.0                                      # hard ceiling (optional)
require_reversible_for_auto = true                    # irreversible never auto
```

A missing file, missing `[policy]` table, or missing key falls back to a
**conservative built-in default**: the default allow-list is a handful of
read-only actions (`git.status`, `git.diff`, `git.log`, `git.show`, `fs.read`,
`fs.list`, `shell.readonly`, `ls`, `cat`, `pwd`), auto only in the `read` scope
at zero cost. Anything that mutates state is deliberately absent, so it defaults
to `needs_human`. `spawnterm-inbox config-path` prints the resolved path.

## Intake path (how requests arrive)

An agent submits an `InboxRequest`. Two supported paths, same code:

* **CLI** — `spawnterm-inbox submit --action … --scope … [--cost N] [--reversible]
  [--session <pane>] [--agent <id>] [--summary …]`. The natural path for an agent
  that shells out (or that the Tier 1 daemon runs on the agent's behalf when it
  forwards a "needs-human" signal).
* **In-process** — construct `Inbox(store, config, emitter)` and call
  `submit(request)`. The daemon (#3/#37) can forward directly this way.

`Inbox.submit` then: **gate** (no-op if off) → **enqueue** durably (so *every*
submission has an audit id) → **classify**:

* `auto_approve` → record an auto decision (`decided_by="policy:auto"`), notify
  the agent, no attention.
* `block` → record a blocked decision (`decided_by="policy:block"`), notify the
  agent, no attention.
* `needs_human` → leave pending and **raise attention** to the target pane.

## Attention routing

`attention.route_attention(request, result)` is **pure**: it returns a route
(target `session` + a concise message) only for `needs_human`; auto and blocked
requests never page a human. The route is realized by an injectable
`AttentionEmitter`:

* `EmitAttentionEmitter` (default) shells out to `spawnterm-emit attention <msg>`,
  which writes `RequestAttention=yes` + an `OSC 9` notification. Because
  `spawnterm-emit` self-gates on `spawnterm.status_board`, it is safe to call
  unconditionally. When the agent self-reports from its own pane, the emit lands
  on the right pane; the route also carries `session` so a session-aware emitter
  (the daemon) can target the exact pane.
* `RecordingEmitter` — the test/degraded double.

`iterm2` is kept out entirely: attention reaches the pane through the
`spawnterm-emit` subprocess, per the design note.

## Approve / edit / reject workflow

```sh
spawnterm-inbox list                 # pending requests awaiting a human
spawnterm-inbox show <id>            # one request + its policy decision & reason
spawnterm-inbox approve <id> [--note …]
spawnterm-inbox edit <id> [--action …] [--scope …] [--cost N] [--reversible y|n] [--note …]
spawnterm-inbox reject <id> [--note …]
```

`edit` is approve-with-changes: the operator's modified action descriptor is
recorded on the decision (`Verdict.EDITED`). Every decision is appended durably
and **pushed back to the requesting agent's mailbox** (`kind:"inbox_decision"`),
so the agent learns the outcome.

## Broker interop (durability)

The queue is durable because it **rides the broker's mailbox** (#4/#35:
`send`/`poll`/`ack`) — the *simplest correct* option (a new broker table/op would
duplicate machinery the mailbox already has). Two append-only streams:

* `spawnterm.inbox.requests` — one message per request; the broker message id
  **is** the request id (monotonic, unique).
* `spawnterm.inbox.decisions` — one message per decision, body carries its
  `request_id`.

**Pending = requests with no matching decision**, reconciled client-side. This is
deliberate: the mailbox `ack` is *up-to-cursor*, so a single stream with
ack-as-resolved would wrongly resolve lower ids when a human decides out of order.
`InboxStore.compact()` acks only the *contiguous fully-resolved prefix* of the
requests stream to bound growth (safe — a prefix ack never removes a still-pending
higher id).

The store talks to anything exposing `request(message) -> dict`, so the broker
client is injected and mocked in tests (`InMemoryBroker`, a faithful in-process
twin of the mailbox semantics).

### Graceful degradation

If the broker is unreachable the client raises `OSError`; the store re-raises it
as `BrokerUnavailable`. The CLI then prints a clear error and exits `1` (the queue
is durable state — it cannot be listed without the broker). Pass `--in-memory` to
run against a throwaway in-process `InMemoryBroker` (**non-durable** — lost when
the process exits; for a single-process demo or smoke test). Embedded callers can
construct `InboxStore(InMemoryBroker(), durable=False)` directly.

## Architecture (testability)

| Module | I/O? | Role |
| --- | --- | --- |
| `model.py` | **pure** | `InboxRequest` / `DecisionRecord` / `Decision` / `Verdict` value types. |
| `policy.py` | **pure** | `PolicyConfig` + `classify()` — the auto/human/block engine. |
| `config.py` | stdlib `tomllib` | load the allow-list config, merged over conservative defaults. |
| `attention.py` | pure route + `subprocess` emitter | `route_attention()` + injectable `AttentionEmitter`. |
| `store.py` | injected broker | durable queue over the mailbox; `InMemoryBroker` double; `BrokerUnavailable`. |
| `gate.py` | flags helper | the `spawnterm.agent_inbox` gate (fail-safe OFF). |
| `inbox.py` | glue | intake → policy → queue/attention → decision. |
| `spawnterm_inbox.py` / `spawnterm-inbox` | CLI | `submit`/`list`/`show`/`approve`/`edit`/`reject`. |

No module imports `iterm2`; attention goes through the `spawnterm-emit`
subprocess. The broker is injected, never imported by the pure path.

## Run

```sh
# 1) enable the flag (or use --no-gate / SPAWNTERM_FORCE=1)
spawnterm-flag enable spawnterm.agent_inbox
# 2) start the broker (durable state lives there)
python3 spawnterm/broker/spawnterm_broker.py serve
# 3) submit / triage
spawnterm/inbox/spawnterm-inbox submit --action git.push --scope repo --session $ITERM_SESSION_ID --agent me
spawnterm/inbox/spawnterm-inbox list
spawnterm/inbox/spawnterm-inbox approve 1
```

## Tests

```sh
bash spawnterm/inbox/tests/run_tests.sh
```

Pure Python + stdlib only; no pip deps, no iTerm2, no external services, no
sleeps. Covers the policy engine across reversibility/scope/cost + allow-list
(auto / needs-human / block), the config loader, attention routing as pure logic,
the intake → queue → decision flow with a mock broker + recording emitter,
graceful degradation when the broker is down, and the gate-off no-op + module
purity (no `iterm2` import).

# it2agent/broker

Tier 2.1 (#34, sub-task of #4) — the durable-state **broker**. This is the
**foundation** of Tier 2; #35 (mailbox + ack), #36 (agent registry + handoff),
and #37 (daemon↔broker bridge) build on it.

`scope:external-tooling` — the broker runs **entirely outside iTerm2** and never
imports or modifies iTerm2 source. It exists precisely because iTerm2 has **no
durable message queue, no queryable registry, no persistent shared state, and no
delivery ack** (see `it2agent/docs/design.md` — "What iTerm2 CANNOT do → external
broker"). Durable state lives **here**, never in iTerm2. The Tier 1 daemon
bridges the two in #37.

> #34 ships the **core only**: sqlite schema + migration framework, the
> unix-socket server + client, and the extensible op-dispatch skeleton with
> `ping`/`health`. The `send`/`poll`/`ack` (#35), `register`/`query`/`handoff`
> (#36) ops arrive in later sub-issues by registering handlers — no restructuring.

## Feature flag: `agent.broker`

Like every it2agent capability, the broker is an individually toggleable,
per-user feature flag that **defaults OFF** (see
`it2agent/docs/feature-flags.md`). The **server refuses to start** when the flag
is OFF: it prints a message and exits `0` — the same gate pattern as the Tier 1
daemon.

```sh
it2agent-flag enable agent.broker     # turn it on
it2agent-flag agent.broker            # query (1/exit0 = on)
it2agent-flag disable agent.broker    # turn it off
```

The gate is checked by importing the #11 helper (`it2agent_flag.is_enabled`),
falling back to shelling out to `it2agent-flag`. Bypass for local testing with
`--no-gate` or `IT2AGENT_FORCE=1`. Client subcommands (`ping`/`health`) talk to
an already-running server and are **not** gated.

## Paths (per-user)

| What | Env override | Default |
| --- | --- | --- |
| sqlite db | `$IT2AGENT_BROKER_DB` | `$XDG_STATE_HOME/it2agent/broker.db` → `~/.local/state/it2agent/broker.db` |
| unix socket | `$IT2AGENT_BROKER_SOCK` | `$XDG_RUNTIME_DIR/it2agent/broker.sock` → `~/.local/state/it2agent/broker.sock` |

Resolve them at runtime with `python3 it2agent/broker/it2agent_broker.py paths`.
`$XDG_RUNTIME_DIR` is the natural home for a socket (ephemeral, per-session) but
is not always set on macOS, hence the state-dir fallback.

## sqlite: WAL + idempotent migration

The db is opened in **WAL** journal mode with a **busy timeout** (5 s), so
multiple client processes (CLI clients, the #37 daemon bridge, the server) can
read/write the same file safely.

Schema creation is **idempotent**. A `schema_version` meta table records which
numbered migrations have run; `schema.apply_schema()` applies only the pending
ones and is safe to call on every startup. Future sub-issues add tables by
appending to `MIGRATIONS` in `schema.py` (v2 for #35 `messages`, v3 for #36
`agents` + handoff/state history) — a shipped migration is never edited in place.

## Wire protocol: newline-delimited JSON

Framing is **one JSON object per line** (UTF-8, terminated by `\n`) in both
directions — the simplest frame that is streamable, language-agnostic, and
trivially testable.

**Request** — only `op` is required:

```json
{"op": "ping", "echo": "hi"}
```

**Response** — always an object with a boolean `ok`:

```json
{"ok": true, "pong": true, "echo": "hi"}
{"ok": false, "error": {"code": "unknown_op", "message": "unknown op: nope", "op": "nope"}}
```

### Base ops (#34)

| Op | Request | Response |
| --- | --- | --- |
| `ping` | `{"op":"ping"}` (optional `echo`) | `{"ok":true,"pong":true}` (echoes `echo` when given) |
| `health` | `{"op":"health"}` | `{"ok":true,"schema_version":N,"db":"…","sock":"…","ops":[…]}` |

### Registry ops (#36)

The **agents** table is a queryable, *persistent* registry keyed by `session_id`
that survives a broker restart (unlike the daemon's ephemeral registry in #26):
`role`, `task`, `capabilities` (JSON array), `last_seen`, `alive`.

| Op | Request | Response |
| --- | --- | --- |
| `register` | `{"op":"register","session_id":"s1","role":"coder","task":"…","capabilities":["python"],"alive":true}` | `{"ok":true,"agent":{…}}` |
| `query` | `{"op":"query","role?":"…","alive?":true,"capability?":"…"}` | `{"ok":true,"agents":[…],"count":N}` |
| `touch` | `{"op":"touch","session_id":"s1","alive?":true}` | `{"ok":true,"agent":{…}}` (or `not_found`) |

`register` is an **upsert** keyed by `session_id` (re-registering replaces the
row and refreshes `last_seen`). `query` AND-combines its optional filters —
`role`/`alive` in SQL, `capability` as membership in the decoded JSON list —
and returns matches most-recently-seen first (no filters → all agents). `touch`
is the liveness update: it refreshes `last_seen`/`alive`, returning `not_found`
for an unregistered session.

### Handoff ops (#36)

The **handoffs** table is an **append-only history** per agent/goal: each row
has `agent_id`, `goal`, `context_ptr`, `owned_files` (JSON array),
`verification_status`, `created_at`, and a monotonic `id`. Nothing is updated in
place — a new version is appended each time.

| Op | Request | Response |
| --- | --- | --- |
| `handoff_put` | `{"op":"handoff_put","agent_id":"a1","goal":"g","context_ptr":"…","owned_files":["f"],"verification_status":"…"}` | `{"ok":true,"handoff":{…,"id":N}}` |
| `handoff_get` | `{"op":"handoff_get","agent_id":"a1","goal?":"g"}` | `{"ok":true,"handoff":{…}}` (latest, or `null`) |
| `handoff_history` | `{"op":"handoff_history","agent_id":"a1","goal?":"g"}` | `{"ok":true,"handoffs":[…],"count":N}` |

`handoff_get` returns the **latest** version (highest `id`) for the agent,
scoped to `goal` when given; `handoff_history` returns **all** versions oldest →
newest. Both return an empty result (`null` / `[]`) for an unknown agent.
Missing/mistyped required fields → `code:"bad_request"`.

Unknown op → `{"ok":false,"error":{"code":"unknown_op",…}}`. A bad-shape request
(not an object, missing/empty `op`) → `code:"bad_request"`. A malformed line
(invalid JSON / not an object) → `code:"bad_request"` too. A handler that raises
→ `code:"internal"`. **The server never crashes on bad input.**

### Mailbox ops (#35) — durable per-agent queue + ack

The mailbox is the it2agent differentiator: where tmux `send-keys` fanout is
fire-and-forget and only ~70-80% reliable, this is a **db-backed queue with
acknowledgement** — a message is durable across a broker restart and is
re-delivered until the recipient acks it. Logic lives in `mailbox.py` (pure
functions over a sqlite connection + thin `@register` handlers); the server
wires the ops in by importing the module.

| Op | Request | Response |
| --- | --- | --- |
| `send` | `{"op":"send","to":"agent1","from":"boss","body":"…"}` | `{"ok":true,"id":N}` |
| `poll` / `fetch` | `{"op":"poll","agent":"agent1","since":N?}` | `{"ok":true,"messages":[…],"count":K}` |
| `ack` | `{"op":"ack","agent":"agent1","msg_id":N}` | `{"ok":true,"acked":K,"cursor":N}` |

Each message dict is `{"id":N,"from":"…","to":"…","body":"…","created_at":ts,"state":"delivered"}`.
Missing/empty `to`/`from`/`agent`, a non-string `body`, a negative/non-integer
`since`, or a bad `msg_id` all return `code:"bad_request"` — the server never
crashes on malformed input.

**Semantics** (durable in sqlite — nothing in memory, so no message is lost
across a broker restart):

* **Ordering** — strict per-recipient FIFO by the monotonic
  `messages.id` (`INTEGER PRIMARY KEY AUTOINCREMENT`). `poll` returns a
  recipient's messages ordered by ascending id.
* **States** — a message moves `pending` → `delivered` → `acked`. `poll`
  promotes the `pending` rows it hands out to `delivered`; `ack` moves rows up
  to a cursor to `acked`.
* **Replay** — `poll` returns every *un-acked* row (`pending` **or**
  `delivered`), so a delivered-but-unacked message is re-returned on the next
  `poll` (at-least-once) until it is acked. `since` is an optional exclusive id
  floor for a caller that tracks its own cursor and wants to page forward past a
  known id; omit it to replay everything un-acked.
* **Ack cursor** — `ack(agent, msg_id)` is *up-to-cursor*: it acks every one of
  the agent's messages with `id <= msg_id` and advances the per-agent high-water
  cursor to `max(existing, msg_id)` (never rewinds). Acking is idempotent —
  re-acking the same id acks nothing new (`acked:0`) and leaves the cursor put.
* **Idempotent send** — we do **not** dedup by content: every `send` appends a
  new row with a fresh id. Exactly-once is guaranteed at the **ack layer**, not
  the send layer — an acked message (`id <= cursor`) is never returned again, so
  delivery is exactly-once *per cursor+ack*. A caller that wants to suppress
  duplicate work should ack.

Schema: migration **v2** adds `messages` (with per-recipient `(recipient,id)`
and `(recipient,state,id)` indexes) and `ack_cursors` (per-agent high-water id).

### op-dispatch API (extensibility)

Ops are entries in a registry keyed by name. #35/#36/#37 add ops without touching
the server:

```python
from dispatch import register, ok, error

@register("send")                       # new op name
def _send(request, ctx):                # (request dict, BrokerContext)
    ...                                 # ctx.conn is the live sqlite connection
    return ok(id=row_id)                # or error("bad_request", "…")
```

`dispatch.handle(request, ctx)` routes one decoded request to its handler and
**always** returns a response dict (it converts bad shapes, unknown ops, and
handler exceptions into structured errors). `health` reports the currently
registered ops.

## Reusable Python client

```python
from client import BrokerClient

c = BrokerClient()            # default socket path; or BrokerClient(sock_path=…)
print(c.ping(echo="hi"))      # {'ok': True, 'pong': True, 'echo': 'hi'}
print(c.health())             # {'ok': True, 'schema_version': 1, ...}
print(c.request({"op": "…"})) # arbitrary op → response dict
```

Each call opens a short-lived connection, writes one request line, reads one
response line, and closes — stateless and safe to share. Raises `OSError` if the
server is unreachable.

## Run

```sh
# Start the server (gated on agent.broker):
python3 it2agent/broker/it2agent_broker.py serve
python3 it2agent/broker/it2agent_broker.py serve --no-gate      # ignore the flag (local)
python3 it2agent/broker/it2agent_broker.py serve --sock /tmp/b.sock --db /tmp/b.db -v

# From another shell, talk to it (not gated):
python3 it2agent/broker/it2agent_broker.py ping
python3 it2agent/broker/it2agent_broker.py health
python3 it2agent/broker/it2agent_broker.py paths
```

Logs go to **stderr**. Exit codes: `0` (clean / gated-off / successful client
call), `1` (client could not reach the server, or the server returned
`ok:false`), `2` (usage error).

## Architecture (testability)

The real logic is **pure** and importable with no socket and no external deps —
the socket server is a thin transport:

| Module | I/O? | Role |
| --- | --- | --- |
| `paths.py` | **pure** | per-user db + socket path resolution (XDG + overrides). |
| `schema.py` | **pure** (stdlib `sqlite3`) | `open_db`/`apply_schema`/`init_db`: WAL, busy timeout, idempotent numbered migrations. |
| `protocol.py` | **pure** (stdlib `json`) | `encode`/`decode`: newline-delimited JSON framing. |
| `dispatch.py` | **pure** | `register`/`handle`/`ok`/`error` + `BrokerContext`; the `ping`/`health` handlers. |
| `store.py` | **pure** (stdlib `sqlite3`) | #36 agent registry + append-only handoff history: `upsert_agent`/`query_agents`/`touch_agent` + `put_handoff`/`get_handoff`/`handoff_history`, and their `register`/`query`/`touch`/`handoff_*` ops. |
| `client.py` | stdlib `socket` | `BrokerClient`: reusable synchronous request/response. |
| `server.py` | stdlib `asyncio` | `BrokerServer`: bind unix socket → decode line → `dispatch.handle` → encode reply. |
| `it2agent_broker.py` | — | entry point: CLI, flag gate, path resolution, `serve`/`ping`/`health`/`paths`. |

No module imports `iterm2`. The broker is independent of iTerm2 by design.

## Tests

```sh
bash it2agent/broker/tests/run_tests.sh
```

Pure Python + stdlib only; no pip deps, no iTerm2, no external services. Covers
path resolution, sqlite schema (WAL is set, busy timeout, idempotent migration
run twice + across reopen), wire-protocol round-trips + framing rejections,
op-dispatch (ping/health routing, unknown/bad-shape errors, handler-exception
isolation, `register` extensibility), the default-OFF `agent.broker` flag
gate + the no-`iterm2` purity guarantee, the #35 mailbox (send→poll→ack, replay
of un-acked messages, strict FIFO ordering, ack-cursor advance/idempotency,
durability across a db reopen, malformed-request errors), and an end-to-end
unix-socket round-trip (server on a socket in a tmpdir, exercised by the real
`BrokerClient`; no sleeps).

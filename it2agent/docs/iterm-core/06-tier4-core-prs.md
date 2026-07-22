# Implementation plan — Tier 4 iTerm2 core primitives (issue #6, fork-direct)

**Policy:** per the Fork policy (Epic #1), these changes are made **directly in this personal fork**
and are **never submitted upstream**. No maintainer discussion, no upstream PR.
**Scope:** `scope:iterm2-core` (modifies iTerm2 source, here in the fork). Three **independent**
items → decomposed into issues **#49 (A)**, **#50 (B)**, **#51 (C)**; build + `ModernTests` to verify.
**Depends on:** Tier 1/2 usage evidence (built + merged as external tooling).

## Framing (read first)
Each item is a **minimal, general-purpose primitive** on top of machinery iTerm2 already has. **None
introduces broker/queue/delivery semantics** — the durable queue, registry-of-record, handoff store
and ack live in it2agent's external Tier 2 broker (sqlite + unix socket), by design. We ask iTerm2
only to close the specific edges the API cannot express cleanly, each useful to any scripter.

Invasiveness (least → most): **A (ack-only) ≈ C < B (labels + filter)**.

---

## Item A — optional delivery ack on `async_send_text`

### Evidence from the working tooling
it2agent's Tier 2 bridge delivers a routed message into a target session with `async_send_text`,
then confirms delivery by **observing the target's screen** for a unique marker before `ack`-ing the
durable mailbox (`it2agent/daemon/bridge.py`, ack-by-observation). That screen-scrape is a
workaround for the API giving no delivery signal — a native ack would make it robust.

### How it works today
- Proto: `SendTextRequest` (`proto/api.proto:1536-1546`: `session`, `text`, `suppress_broadcast`)
  and `SendTextResponse` (`:1548-1555`: only `Status {OK, SESSION_NOT_FOUND}`).
- Handler `-apiServerSendText:handler:` (`sources/API/iTermAPIHelper.m:2405-2433`) resolves the
  session, calls `writeTask:`/`writeTaskNoBroadcast:`, and **immediately** returns `Status=OK`.
- `OK` means "session exists / request accepted", **not** "bytes dispatched". The write path
  (`PTYSession.m:writeTaskImpl:` `3905-3973` → `writeData:` `3975-3997` → `[_shell writeTask:]`) can
  defer into `_dataQueue`/`_sshWriteQueue` with no completion callback. No "text delivered"
  notification exists.

### Proposed minimal change
Add an optional request field (e.g. `optional bool wait_for_dispatch = 4;`) to `SendTextRequest`
(`proto/api.proto:1536-1546`) and, when set, defer `handler(response)` in
`iTermAPIHelper.m:2420-2432` until the enqueued data has been flushed to the PTY. The pipeline
already distinguishes immediate vs deferred writes, so a completion hook threads from the queue
drain.

- **Conservative variant (trivial):** ack "accepted + enqueued" (no pipeline change).
- **True variant (medium):** ack "bytes handed to the kernel PTY" — needs a completion plumbed back
  through `writeData:`/PTYTask and the deferred-queue drain.

**Invasiveness:** small (conservative) to medium (true flush ack). **Not a broker:** reports on the
existing single write; no persistence, no fan-out.

---

## Item B — native queryable session registry / labels

### Evidence from the working tooling
Both the Tier 1 daemon (`it2agent/daemon/registry.py`, ephemeral) and the Tier 2 broker
(`it2agent/broker/store.py`, persistent) maintain their **own** agent registry because the API
gives GUID-only `new_session`/`terminate_session` events and an unfilterable `ListSessions`. A
native label + query surface would let lightweight clients skip a bespoke registry.

### How it works today
- `ListSessionsRequest` is **empty** (`proto/api.proto:1533-1534`); the handler ignores it and
  returns the full hierarchy (`iTermAPIHelper.m:2358-2403`).
- `SessionSummary` (`proto/api.proto:1572-1577`) exposes only `unique_identifier`, `frame`,
  `grid_size`, `title` — **no user-assignable label**.
- `NewSessionNotification`/`TerminateSessionNotification` carry only the GUID
  (`proto/api.proto:1098-1100`, `:1138-1140`); subscriptions are keyed by connection with **no
  filter** (`iTermAPIHelper.m:1889-1970`, emit at `:898-928`).

### Proposed minimal change
- **Labels:** add `repeated string tags` (or `map<string,string> labels`) to `SessionSummary`
  (`:1572-1577`), populated in `-newListSessionsResponse` (`iTermAPIHelper.m:2381-2399`). **Back the
  storage with the existing user-vars** (Item C) instead of a new store — keeps it minimal.
- **Query:** add optional filter fields to `ListSessionsRequest` (`:1533-1534`) applied in
  `-apiServerListSessions:` (`:2358-2403`) — the response is already rebuilt each call, so filtering
  is localized.
- **Richer notifications (optional):** include tags in the new/terminate notifications at the emit
  sites (`:912-927`).

**Invasiveness:** small (tags field + populate, filter) to medium (if label storage is new rather
than reusing user-vars). **Not a broker:** a read model + descriptive labels over state iTerm2
already owns; no message passing.

---

## Item C — persist user-vars to a sidecar (survive session end)

### Evidence from the working tooling
it2agent identity lives in dot-free user-vars `user.agent_status` / `agent_role` / `agent_task` /
`agent_id` (set by `it2agent-emit`; consumed by the badge, the daemon dashboard, and the review /
cost surfaces). Those vars **die with the session**, so a crashed/closed agent loses its identity —
exactly the continuity gap Tier 3 (`tmux -CC`) + Tier 2 (broker handoff) work around. A native
sidecar would make per-session identity durable for any script, not just it2agent.

### How it works today
- `-screenSetUserVar:` (`PTYSession.m:16501-16530`) sets `user.<name>` on `variablesScope` (rejects
  `.` in the key; base64-decodes the value; mirrors to tmux).
- Storage: `_userVariables` is a child `iTermVariables` under `"user"` inside `_variables`
  (`PTYSession.m:852-859`); values are included in `_variables.encodableDictionaryValue`
  (`iTermVariables.m:600-631`).
- Persistence **only** via the whole-app **arrangement** path: saved at
  `PTYSession.m:6809` (`result[SESSION_ARRANGEMENT_VARIABLES] = _variables.encodableDictionaryValue`)
  **inside the `if (includeContents)` guard** (`:6797`), restored at `:1670-1692`. On an ordinary
  process exit (no arrangement capture) the session is deallocated (`:1138-1139`) and user-vars are
  lost. There is **no independent per-session sidecar**.

### Proposed minimal change
**Write-through on set (preferred):** in `-screenSetUserVar:` (`:16510-16523`), also persist the
`user.*` pair to a sidecar keyed by a stable session id (`_stableID` `:845` or `_guid` `:867`); read
/replay it at session init (`:852-859`) or alongside the arrangement restore loop (`:1670-1692`).
This does **not** depend on `includeContents`/arrangement capture, so it survives normal session end
and app restart. Serialization is already solved (the vars flow through `encodableDictionaryValue`).

Open design questions (decide during implementation): stable-key choice (`_stableID` vs `_guid`), sidecar
location/format, GC of stale sidecars, and interaction with tmux user-vars (`:16512-16515`).

**Invasiveness:** small in code, medium in design decisions. **Not a broker:** durability for the
existing user-vars value store; no delivery/ordering/consumers.

---

## Recommended sequencing
1. **Item A (conservative ack)** first — smallest, immediately removes the ack-by-observation
   screen-scrape hack.
2. **Item C** next — unblocks durable identity and also becomes the storage backing for B's labels.
3. **Item B** last — labels (reusing C) + query filter.

For each: implement directly in the fork against the file:line evidence above, keep it a minimal
general-purpose primitive (not a broker), gate new behavior behind an advanced setting (default OFF),
add a `ModernTests` test, build clean with `tools/build.sh`, update `docs/notes-3.7.txt`, one PR per
sub-issue (`Closes #49/#50/#51`). No upstream step.

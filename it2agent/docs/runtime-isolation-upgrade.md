# it2agent — runtime-isolation upgrade (#13, the "Coasts model")

**Status: DESIGN + RESEARCH (docs only — no code).** Part of Epic **#1**, follow-up to
shipped issue **#13** (worktree + `$PORT`/service isolation). Companion to
`cooperation-strategy.md` (§R3, backlog item 7) and `it2agent/spawn/WORKTREE.md`.

This document audits what #13 isolates **today**, validates a concrete upgrade against
2026 market/academic practice (the "Coasts" dynamic-vs-canonical-port model, Upsun/
Platform.sh preview-environment patterns, Docker/compose-project isolation, schema-per-agent
DB isolation), and lays out a **phased, prioritized sub-backlog** to open. It is written to
be coded from, but nothing here is implemented yet.

`scope:external-tooling` throughout — every proposal is CLI/env-var glue around `git`,
`it2agent-flag`, and (optionally) `docker`/`psql`. None of it touches iTerm2 source, and
all of it stays gated behind `agent.worktree_isolation` (or sub-flags seeded OFF).

---

## 1. Current vs target — capability table

What #13 does today is in `it2agent/spawn/it2agent-worktree` (the pure allocator +
side-effect layer), consumed by `it2agent/spawn/it2agent-spawn` and
`it2agent/tmux/it2agent-tmux`. Evidence for each "today" cell is cited in §2/§Audit.

| Capability | #13 today | Upgrade target | Verdict |
|---|---|---|---|
| **File isolation** | git worktree per agent, own branch, kept outside repo tree (`it2agent-worktree:196-210`) | unchanged — this is correct and complete | keep |
| **Port model** | one deterministic port per agent, `base + hash%span`, probed upward for a free TCP port (`:214-215`, `:256-272`) | **dynamic** port per declared service (always reachable) **+** an optional **canonical-port lease** so the checked-out agent answers on the normal `localhost:PORT` | adopt (adjust) |
| **Multiple ports** | exactly one `$IT2AGENT_PORT` per agent | N declared ports per agent (web + db + cache…), each with its own dynamic allocation | adopt |
| **Real bind-conflict avoidance** | probe detects a *listening* port and walks the range (`port_in_use :245-254`) | keep probe; add a persisted **lease file** so two agents allocating concurrently don't both pick the same "free" port (TOCTOU gap today) | adopt |
| **Service namespace** | `$IT2AGENT_NS` string prefix, DB/schema/service-safe (`:219`, `nsify`) | keep; make it drive concrete **DB isolation** (schema-per-agent) and **Docker isolation** (`COMPOSE_PROJECT_NAME`) via opt-in `--isolate` | adopt |
| **DB isolation** | none — hands out a *prefix string*; honoring it is the agent command's job (`WORKTREE.md` "contract") | optional `--isolate db`: create/drop a per-agent Postgres **schema** (or database) named from `$IT2AGENT_NS` | adopt (guarded) |
| **Docker isolation** | none | optional `--isolate docker`: set `COMPOSE_PROJECT_NAME=$IT2AGENT_NS` so compose stacks get isolated networks/volumes/containers | adopt (guarded) |
| **OS namespaces** | none | (macOS reality) skip Linux netns/cgroups; document Docker/VM as the only real network-namespace path on Darwin | **skip** on macOS |
| **Per-switch service policy** | none (agents are independent tabs, no "switch") | `--assign none\|hot\|restart\|rebuild` semantics for when a worktree is *re-used*/re-checked-out; low value for our tab-per-agent model | adjust → thin subset |
| **Observability** | none — the allocation is only visible via `plan`/`env`/`--dry-run` for a *single* agent | `it2agent-worktree ls`/`status`: list all live agent worktrees, their branch, port(s), namespace, dirty/merged state, and lease age | adopt (high value) |
| **Cleanup** | safe by default: refuses dirty tree + unmerged branch (`cmd_cleanup :383-420`) | keep; extend to release the port lease + drop the DB schema / compose project when `--isolate` was used | adopt |
| **Gating** | one flag `agent.worktree_isolation`, fail-safe OFF (`:65`, `:100-108`) | keep; new capabilities behind **sub-flags** (all OFF) so the upgrade is incremental and reversible | keep |

**One-line framing.** #13 gives **file isolation + one deterministic port + a namespace
string**. The upgrade turns the namespace string into *real* runtime isolation (multi-port
leases, optional DB/Docker) and adds the missing operator view (`ls`), without abandoning the
CLI/env-var style or the fail-safe-OFF gate.

---

## Audit — exactly what we isolate today (cite file:line)

Read against `it2agent/spawn/it2agent-worktree` unless noted.

- **Files: yes.** `create` runs `git worktree add [-b <branch>] <path>` on a deterministic
  branch `it2agent/<slug>-<hash6>`, at a path *outside* the repo
  (`<parent>/.it2agent-worktrees/<repo>/<leaf>`), so it never shows as untracked in the main
  checkout (`compute_plan :185-220`, worktree add in `cmd_create :285-346`). Idempotent: a
  re-spawn of the same `--id` reuses the existing worktree.
- **Port: a deterministic candidate, then a live probe.** `PORT = base + (int(first 7 hex of
  sha1("<repo>|<id>")) % span)`, default range `41000..41999` (`:67-68`, `:214-215`).
  `create` then walks upward within the range for a port with no `LISTEN` socket via
  `lsof`/`nc` (`probe_free_port :256-272`, `port_in_use :245-254`). `--no-probe` forces the
  deterministic value; `plan`/`env`/`--dry-run` always report the deterministic candidate
  (reproducible on any machine).
- **Namespace: a string only.** `NAMESPACE = <nsify(role)>_<hash6>`, guaranteed
  identifier-safe (`:219`, `nsify :162-176`). It is *advisory* — nothing creates a DB or a
  container. `WORKTREE.md` spells out the contract: "it2agent hands them out; your command
  wires them in" (`createdb "${IT2AGENT_NS}_app"`, `docker compose -p "$IT2AGENT_NS"`).
- **Exports into the session.** `env`/spawn inject `IT2AGENT_PORT`, `IT2AGENT_NS`,
  `IT2AGENT_WORKTREE`, `IT2AGENT_BRANCH` before the agent command runs
  (`cmd_env :438-441`; `it2agent-spawn` isolation block builds the same four exports;
  `it2agent-tmux:382` joins them `;`-separated for the inner tmux script).
- **Gate.** All side effects gate on `agent.worktree_isolation` via `it2agent-flag`, fail-safe
  OFF when the flag/helper is missing (`FLAG_KEY :65`, `gate_open :100-108`). `plan`/`env` are
  pure and never gate. In spawn/tmux, isolation is opt-in via the flag or `--force-isolation`
  and is **not** turned on by `--no-gate`/`IT2AGENT_FORCE` (bug-P2 fix, documented at
  `it2agent-spawn` isolation block and `it2agent-tmux:334-345`).
- **Cleanup.** `cmd_cleanup` refuses a **dirty** worktree (`git status --porcelain` non-empty,
  `:383-395`) and an **unmerged** branch (not in `git branch --merged <base>`, `:397-420`);
  `--force` overrides. Base auto-detected (`origin/HEAD` → `main`/`master` → current).

**What we do NOT isolate today (the gap this upgrade closes):**

1. **Real port *binding* conflicts across concurrent allocations.** The probe is a
   check-then-use with no lock: two agents spawned simultaneously can both observe the same
   port as free and both export it (TOCTOU). Nothing persists "port X is leased to agent Y".
2. **Only one port.** A realistic stack needs web + DB + cache ports; #13 hands out one.
3. **No canonical port.** Every agent gets a high-range port; there is no notion of "the
   checked-out/foreground agent answers on the *normal* `localhost:3000`", which is the DX the
   Coasts/Upsun sources call out as the point of the exercise.
4. **DB / Docker / services: not isolated, only *named*.** `$IT2AGENT_NS` is a hint; two agents
   still share one Postgres `dev` DB and one Docker daemon default project unless the *agent
   command* opts in. Nothing creates or tears down the schema/compose project.
5. **No OS-level namespaces.** No netns/cgroups (and on macOS these do not exist for host
   processes — see §2 R5).
6. **No observability.** There is no way to ask "which agents are live, on what ports, in what
   state?" — only per-agent `plan`/`env`.

---

## 2. Concrete design (per feature)

Each feature is a small, additive change to `it2agent-worktree` (the allocator/side-effect
helper) plus a passthrough flag in `it2agent-spawn`/`it2agent-tmux`. All new behavior is
gated; when its sub-flag is OFF the tool behaves byte-for-byte like #13.

### 2.1 Multi-port + dynamic allocation with a persisted lease

**Problem solved:** gap #1 (TOCTOU) and #2 (one port only).

**Env/CLI surface.**

```
it2agent-worktree plan   --id 13 --role worker --ports web,db,cache
it2agent-worktree create --id 13 --role worker --ports web,db,cache
# exports:
#   IT2AGENT_PORT_WEB=41724   IT2AGENT_PORT_DB=41903   IT2AGENT_PORT_CACHE=41255
#   IT2AGENT_PORT=41724        # = the first declared port, back-compat alias
```

- `--ports <name[,name...]>` (default `web`, which reproduces today's single `$IT2AGENT_PORT`).
  Each name gets `IT2AGENT_PORT_<UPPER>`; the first is also exported as bare `IT2AGENT_PORT`
  so every existing consumer keeps working.
- **Deterministic candidate per name:** `base + (int(first 7 hex of sha1("<repo>|<id>|<name>"))
  % span)` — the existing formula (`:214-215`) with the port name folded into the hash so the
  three ports don't collide with each other. Reproducible in `plan`/`--dry-run`.
- **Lease file** closes the TOCTOU gap: `create` writes a claim to
  `$IT2AGENT_WORKTREE_ROOT/.leases/<port>.lease` (contents: `agent-id repo pid epoch`) under an
  `flock`/`mkdir` mutex, and treats a live lease as "in use" *in addition to* the `lsof`/`nc`
  probe (`port_in_use :245-254`). Probe still walks the range on conflict
  (`probe_free_port :256-272`). Stale leases (no live pid / older than a TTL) are reclaimed.
  Lease dir lives beside the worktrees so `cleanup` and the janitor (#15) can see it.
- **Sub-flag:** reuse `agent.worktree_isolation` for `--ports` (it is a natural extension of
  the existing port scheme). The lease file is unconditional once isolation is ON (it only
  makes the existing probe correct).

### 2.2 Canonical-port lease (the checked-out agent on the normal port)

**Problem solved:** gap #3. This is the distinctive half of the Coasts model.

**Env/CLI surface.**

```
it2agent-worktree canonical --id 13 --port web       # lease the canonical port to this agent
it2agent-worktree canonical --release                # give it back
# while held, the agent additionally gets:
#   IT2AGENT_CANONICAL_PORT_WEB=3000
```

- A per-repo, per-port **singleton lease**: exactly one agent at a time may hold the canonical
  value (the project's *normal* dev port, e.g. `3000`, declared in the Coastfile — see §2.6, or
  passed as `--canonical-port 3000`). Stored as `.leases/canonical-<name>.lease`.
- The holder additionally exports `IT2AGENT_CANONICAL_PORT_<NAME>`. Non-holders keep only their
  dynamic port. This gives the "every instance always reachable on its dynamic port; the
  foreground instance *also* answers on `localhost:3000`" behavior the sources describe.
- Acquiring transfers the lease (last checkout wins) and is reported in `ls` (§2.5).
- **Sub-flag:** `agent.canonical_port` (OFF). Pure add-on; when OFF, only dynamic ports exist.
- *Note (verify):* Coasts implements the canonical port by *forwarding* it to the container of
  the checked-out instance; our CLI/env-var equivalent is to hand the agent command the number
  and let it bind — we do not proxy. A future daemon (#3) could add real forwarding.

### 2.3 `--assign none|hot|restart|rebuild` (adjusted to a thin subset)

**Problem solved:** partial — gap in service lifecycle when a worktree is *re-used*.

The Coasts assign strategies describe what happens to each service **when you switch which
branch a slot is checked out to**. Our model is *tab-per-agent* (each agent is its own
long-lived worktree; we rarely "re-point" a slot), so the full four-way matrix is largely
overkill (§2/R3). Keep a **thin subset** as a documented hook, not a scheduler:

- `--assign none` (default): `create` on an existing worktree reuses it untouched — this is
  **today's idempotent behavior** (`cmd_create :300-346`). No change.
- `--assign restart`: on reuse, run an optional repo-declared `restart` hook (Coastfile
  `[services]`), for the case where an agent's dev server must be bounced.
- `hot`/`rebuild`: **document as recognized values that map to `none` unless a Coastfile hook is
  declared** — we do not own the process supervisor, so we only invoke a user hook. Full
  file-watch/image-rebuild orchestration is explicitly out of scope for a CLI on macOS.
- **Sub-flag:** folded into `agent.canonical_port`/Coastfile support; no standalone flag until a
  hook is actually declared.

### 2.4 `--isolate db|docker|namespace` (guarded, opt-in real isolation)

**Problem solved:** gap #4 (DB/Docker only *named* today) and gap #5 (namespaces).

```
it2agent-worktree create --id 13 --role worker --isolate db
it2agent-worktree create --id 13 --role worker --isolate docker
```

- **`--isolate db`** — turn `$IT2AGENT_NS` into a real Postgres **schema** (cheapest isolation;
  §2/R4): on `create`, `psql -c "CREATE SCHEMA IF NOT EXISTS $IT2AGENT_NS"`; export
  `IT2AGENT_DB_SCHEMA=$IT2AGENT_NS` and, for convenience, a `PGOPTIONS=-c search_path=$IT2AGENT_NS`
  export. On `cleanup`, `DROP SCHEMA ... CASCADE` **only** if the safety guards pass (never drop
  a schema whose owning branch is unmerged). Connection comes from the repo's own
  `$DATABASE_URL`/`.env` (we never invent credentials). Database-per-agent (`CREATE DATABASE`)
  offered as `--isolate db=database` for stricter needs (§2/R4 tradeoff). **Sub-flag:**
  `agent.isolate_db` (OFF).
- **`--isolate docker`** — export `COMPOSE_PROJECT_NAME=$IT2AGENT_NS` (and
  `IT2AGENT_COMPOSE_PROJECT=$IT2AGENT_NS`) so `docker compose up` gets its own network, volumes,
  and container names, giving *in-container* port reuse with host isolation (§2/R6). `cleanup`
  runs `docker compose -p "$IT2AGENT_NS" down -v` behind the safety guards. **Sub-flag:**
  `agent.isolate_docker` (OFF).
- **`--isolate namespace`** — **rejected on macOS.** Document that Linux netns/cgroups isolation
  has no host-process equivalent on Darwin; the only real network-namespace isolation on macOS
  is a container/VM, i.e. `--isolate docker`. `--isolate namespace` errors with that pointer
  rather than pretending (§2/R5).

Multiple isolations compose: `--isolate db --isolate docker`. All are additive to the existing
worktree+port allocation and no-op when their sub-flag is OFF.

### 2.5 `it2agent-worktree ls` / `status` (observability)

**Problem solved:** gap #6. Highest value-per-line in the whole upgrade, and unlocks the
janitor (#15).

```
it2agent-worktree ls                     # table, all live agent worktrees for this repo
it2agent-worktree ls --all --json        # every repo; machine-readable for the daemon/MCP
it2agent-worktree status --id 13         # one agent, verbose
```

`ls` composes purely from data we already have — `git worktree list --porcelain`, the branch
naming scheme, the `.leases/` dir (§2.1), and `git status --porcelain` per worktree — so it
needs **no new state store**. Columns:

| id/branch | worktree path | ports (web/db/…) | canonical? | namespace | isolate | dirty | merged | lease age |

- Derives the same allocation `plan` already prints, then annotates it with live facts (is the
  port actually listening? is the lease held? is the tree dirty? is the branch merged into
  base?).
- `--json` is the seam the Tier-1 daemon (#3) and MCP surface (#18) consume, and what a future
  status board renders. It is the "visual status / list instances + ports + status" capability
  every source names as table stakes.
- **Sub-flag:** none needed — `ls`/`status` are read-only reporters (like `plan`/`env`, they
  never gate).

### 2.6 Optional `Coastfile`-style project declaration

**Problem solved:** lets a repo declare its ports/services once instead of passing `--ports`
every spawn.

A single optional file at repo root, `.it2agent/isolation.toml` (our TOML, parsed by the same
constrained parser the flags system uses — see `docs/feature-flags.md`), declaring:

```toml
[ports]
web = { canonical = 3000 }
db  = { canonical = 5432 }
[isolate]
db = "schema"          # schema | database | off
docker = true
[services.web]
assign = "restart"     # none | restart (+ documented hot|rebuild → none)
restart = "npm run dev:restart"
```

`create`/`plan` read it when present; explicit flags override it. Modeled on the Coasts
`Coastfile` (`compose=`, `primary_port`, per-service policy) but scoped to what a macOS CLI can
honor. **Sub-flag:** none — the file is inert unless a gated feature reads it.

### How it plugs into the existing flow

Nothing above changes the spawn *control flow*. `it2agent-spawn`/`it2agent-tmux` already: gate
on `agent.worktree_isolation`, resolve the repo root, derive the id, call
`it2agent-worktree create ...`, parse `key=value` lines from stdout, set `CWD` to the worktree,
and inject the `IT2AGENT_*` exports (see the isolation blocks in both wrappers). The upgrade is:
(a) `create` prints **more** `key=value` lines (`port_web=…`, `canonical_port_web=…`,
`db_schema=…`, `compose_project=…`); (b) the wrappers forward the new passthrough flags
(`--ports`, `--isolate`, `--canonical-port`) and blindly export any `IT2AGENT_*` the helper
emits. Because parsing is already generic key/value, most of the wrapper change is argument
forwarding, not new logic.

---

## 3. Research & evidence (adopt / adjust / skip per decision)

Sources gathered 2026-07 via Firecrawl search + fetch. Verdicts are honest about what is
overkill for a **personal, single-Mac** fork. Where a claim is not verified against primary
source, it is marked **verify**.

### R1 — File isolation alone is insufficient (the premise)
- **Evidence.** Upsun: "Worktrees isolate code but not the runtime environment… You get
  separate file systems but shared ports, databases, and services"; "Every dev server defaults
  to the same ports: 3000, 5432, 8080. Launch two React apps from different worktrees and one
  fails"
  ([Upsun](https://developer.upsun.com/posts/ai/git-worktrees-for-parallel-ai-coding-agents)).
  A Linux practitioner writeup independently: "Git worktree gives you multiple working
  directories but they still share the same database, same ports, same Docker daemon — it solves
  code isolation, not environment isolation"
  ([Medium, "Isolated Playgrounds for LLM Coding Agents"](https://medium.com/@llupRisingll/the-quest-for-true-development-environment-isolation-on-linux-71dffbf23aad)).
- **Verdict: KEEP.** This is exactly why #13 already adds a port + namespace. The upgrade just
  makes the namespace *real*.

### R2 — Dynamic + canonical port (the Coasts model)
- **Evidence.** Penligent "Coasts": "every instance gets a dynamic port in a high range for
  each declared port, and the checked-out instance also receives the canonical port forwarded to
  the host." Configuration via a TOML `Coastfile` (`compose=`, `primary_port`), CLI `coast
  build / run / ls / ui`
  ([Penligent](https://www.penligent.ai/hackinglabs/git-worktrees-need-runtime-isolation-for-parallel-ai-agent-development/)).
  Microsoft's Aspire team reaches the same architecture from the other side — automatic
  per-worktree port allocation plus a **proxy** so "the agent always talks to the same proxy,
  and the proxy figures out where the current AppHost is running"
  ([MS DevBlogs](https://devblogs.microsoft.com/aspire/scaling-ai-agents-with-aspire-isolation/)).
- **Verdict: ADOPT (adjust).** Adopt dynamic-per-declared-port (§2.1) and a canonical-port
  *lease* (§2.2). **Adjust:** we hand the agent the number and let it bind; we do **not** build
  the port-forwarding proxy (that needs the Tier-1 daemon #3). Mark the "forwarded to the host"
  half **verify/future**.

### R3 — Per-service assign strategies (none/hot/restart/rebuild)
- **Evidence.** Coasts: `none` (state persists), `hot` (file-watchers reload), `restart` (stop/
  start on new branch code), `rebuild` (image reconstructed); and the key nuance "databases and
  caches often should not reset on every branch reassignment. Frontend and backend services
  often should" ([Penligent](https://www.penligent.ai/hackinglabs/git-worktrees-need-runtime-isolation-for-parallel-ai-agent-development/)).
- **Verdict: ADJUST → thin subset.** These strategies matter when one *slot* is repeatedly
  re-pointed at different branches. Our model is tab-per-agent (a worktree ≈ an agent for its
  lifetime), so the matrix is mostly overkill. Keep `none` (= today's idempotent reuse) and an
  optional `restart` hook (§2.3); treat `hot`/`rebuild` as recognized aliases that map to a
  user-declared hook or to `none`. Do not build a process supervisor.

### R4 — DB isolation (schema-per-agent vs db-per-agent)
- **Evidence.** Concrete schema-per-run pattern: `CREATE SCHEMA agent_run_abc123; CREATE TABLE
  agent_run_abc123.customers AS SELECT * FROM production.customers`
  ([MindStudio](https://www.mindstudio.ai/blog/ai-agents-isolated-database-fork-experiment-pattern)).
  Multitenancy tradeoff survey: isolated-DB (strongest, heaviest) vs shared-schema-with-tenant-id
  (lightest) vs schema-per-tenant (middle) — pick by isolation-vs-overhead
  ([Medium, multitenancy patterns](https://medium.com/@beta_49625/multitenancy-patterns-isolated-dbs-shared-schemas-and-the-trade-offs-ecf1ad660f70)).
- **Verdict: ADOPT (guarded).** Default `--isolate db` = **schema** (cheap, one `CREATE SCHEMA`,
  fast teardown), offer `db=database` for strict cases (§2.4). Guarded behind `agent.isolate_db`
  (OFF) and only when the repo supplies its own DB URL — we never provision a server or invent
  credentials. This is opt-in; most personal-fork use won't need it.

### R5 — OS namespaces on macOS
- **Evidence.** The strongest local-isolation writeup achieves true per-agent isolation on
  **Linux** via `systemd-machined`/`machinectl` containers with separate IPs, explicitly a Linux
  mechanism ([Medium, "Isolated Playgrounds"](https://medium.com/@llupRisingll/the-quest-for-true-development-environment-isolation-on-linux-71dffbf23aad)).
  Linux netns/cgroups have no host-process equivalent on Darwin (**verify** — no primary Apple
  source cited; this is well-established but not linked here).
- **Verdict: SKIP on macOS.** Do not implement `--isolate namespace` for host processes; error
  with a pointer to `--isolate docker` (containers/VMs are the only real netns on macOS). This is
  the one capability from the market model that is genuinely N/A for our target platform.

### R6 — Docker/compose-project isolation
- **Evidence.** Docker's own docs: "Compose uses a project name to isolate environments from
  each other," set via `COMPOSE_PROJECT_NAME` or `-p`
  ([Docker docs](https://docs.docker.com/compose/how-tos/project-name/)); VS Code documents the
  same for per-clone devcontainers
  ([VS Code](https://code.visualstudio.com/remote/advancedcontainers/set-docker-compose-project-name)).
  This gives isolated networks/volumes/containers so services can reuse their normal ports
  *inside* the project while staying isolated on the host.
- **Verdict: ADOPT (guarded).** `--isolate docker` = `COMPOSE_PROJECT_NAME=$IT2AGENT_NS`
  (§2.4). This is the macOS-correct substitute for R5's netns and directly generalizes the
  `docker compose -p "$IT2AGENT_NS"` pattern `WORKTREE.md` already documents. Behind
  `agent.isolate_docker` (OFF).

### R7 — Observability (list instances / ports / status)
- **Evidence.** Coasts ships "Coastguard" (a local UI on port 31415) showing "projects, running
  instances, checkout state, port mappings, logs, runtime stats, image artifacts, volumes, and
  secret metadata" ([Penligent](https://www.penligent.ai/hackinglabs/git-worktrees-need-runtime-isolation-for-parallel-ai-agent-development/));
  `coast ls` is a first-class command. Upsun lists "Visual status and progress tracking" as a
  core requirement of any mature tool
  ([Upsun](https://developer.upsun.com/posts/ai/git-worktrees-for-parallel-ai-coding-agents)).
- **Verdict: ADOPT (highest priority).** Build the CLI half — `it2agent-worktree ls`/`status`
  with `--json` (§2.5). Skip a bespoke web UI (a personal fork already has iTerm2's status board
  + the daemon/MCP as the render surfaces). `--json` is the integration seam.

### R8 — Prior art baseline (Emdash, the one tool that solved ports)
- **Evidence.** #13's own framing: "only Emdash solves it via `$EMDASH_PORT`." Emdash is a
  worktree-isolation tool for parallel coding agents (`brew install --cask emdash`) that
  transforms a pre-reserved worktree into a task worktree with instant `git worktree move` +
  `git branch -m` ([codeline.co repo review](https://www.codeline.co/thoughts/repo-review/2026/emdash-parallel-coding-agents-with-worktree-isolation);
  [emdash AGENTS.md](https://github.com/emdash-cms/emdash/blob/main/AGENTS.md)). The exact
  env-var name `$EMDASH_PORT` is asserted by #13 but **not confirmed** in the fetched sources —
  **verify** before citing it as precedent in code comments.
- **Verdict: KEEP as validation.** Our `$IT2AGENT_PORT` already matches the one differentiator
  the market recognized; the upgrade (multi-port + canonical + optional DB/Docker + `ls`) moves
  us *past* the single-port baseline. A "reserve pool" of pre-created worktrees (Emdash's speed
  trick) is a possible **future** optimization, not part of this upgrade.

**Honest overkill call.** For a personal single-Mac fork, the must-haves are **R7 (`ls`)** and
**R2's dynamic multi-port + lease** (they fix a real correctness gap — the TOCTOU probe). DB and
Docker isolation (R4/R6) are genuinely useful but opt-in and secondary; assign strategies (R3)
are mostly ceremony for our model; OS namespaces (R5) are N/A. Build the cheap correctness +
visibility wins first; leave the heavy isolations behind OFF sub-flags for when a real project
needs them.

---

## 4. Phased, prioritized sub-backlog (open these; smallest-valuable-first)

Each is one issue: **title · one-line scope · dependency**. Ordered so every step ships value
alone and later steps build on earlier ones. All gate OFF by default.

1. **Persisted port lease (fix the TOCTOU probe)** · add an `flock`/`mkdir`-guarded
   `.leases/<port>.lease` that `create` writes and the probe consults alongside `lsof`/`nc`, with
   stale-lease reclaim · *dep: none (pure hardening of `it2agent-worktree` #13)*. **Do first —
   smallest, fixes a real correctness bug.**
2. **`it2agent-worktree ls` / `status` (+`--json`)** · read-only reporter composing `git worktree
   list` + branch scheme + `.leases/` + `git status`, table and JSON, no new state · *dep: #1 (to
   show lease state)*. **Highest value-per-line; unblocks janitor #15 and daemon #3.**
3. **Multi-port `--ports web,db,cache`** · N deterministic ports per agent, each
   `IT2AGENT_PORT_<NAME>`, first aliased to bare `IT2AGENT_PORT` (back-compat); spawn/tmux forward
   the flag · *dep: #1*.
4. **Canonical-port lease + `agent.canonical_port` flag** · singleton per-repo/port lease so the
   checked-out agent also exports `IT2AGENT_CANONICAL_PORT_<NAME>`; `canonical`/`--release`
   subcommands; shown in `ls` · *dep: #1, #3*.
5. **`--isolate docker` + `agent.isolate_docker` flag** · export `COMPOSE_PROJECT_NAME=$IT2AGENT_NS`;
   `cleanup` runs `docker compose -p … down -v` behind the existing safety guards · *dep: #2 (ls
   should surface it); reuses `$IT2AGENT_NS`*.
6. **`--isolate db[=schema|database]` + `agent.isolate_db` flag** · create/drop a per-agent
   Postgres schema (default) or database from `$IT2AGENT_NS` using the repo's own DB URL; export
   `IT2AGENT_DB_SCHEMA`; teardown guarded · *dep: #2*.
7. **Optional `.it2agent/isolation.toml` (Coastfile-style) project declaration** · declare ports/
   canonical/isolate/service policy once; flags override; parsed by the flags TOML parser · *dep:
   #3, #4, #5, #6 (it just front-ends them)*.
8. **`--assign none|restart` thin service hook (+ document hot/rebuild → none)** · on worktree
   reuse, optionally run a Coastfile-declared `restart` hook; recognize but do not orchestrate
   hot/rebuild · *dep: #7*. **Lowest priority — mostly ceremony for our tab-per-agent model.**

Explicitly **not** scheduled: `--isolate namespace` on macOS (R5, rejected), a bespoke web UI
à la Coastguard (R7 — reuse the status board / daemon / MCP), and a pre-created worktree reserve
pool (R8 — future speed optimization).

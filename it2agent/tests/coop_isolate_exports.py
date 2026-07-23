#!/usr/bin/env python3
"""Cooperation AC — service isolation is ENV-ONLY and flag-gated (#112).

Proves `it2agent-worktree --isolate docker,db` hands over ONLY environment
variables the project's own tooling reads (COMPOSE_PROJECT_NAME / IT2AGENT_DB_SCHEMA
+ PGOPTIONS) — never runs docker, never touches Postgres — and that each mode is
gated on its own flag (agent.isolate_docker / agent.isolate_db), fail-safe OFF.

Runs `create --dry-run` so there are NO side effects at all (no worktree, no
lease, no container). Uses an isolated IT2AGENT_CONFIG so the operator's flags
are never touched, and points --repo at the it2agent git root so plan derivation
succeeds. Four checks:

  1. flags ON  + --isolate docker,db -> both export blocks appear + isolate= line
  2. flags OFF + --isolate docker,db -> NO env_ exports (inert)
  3. only isolate_docker ON           -> only COMPOSE_PROJECT_NAME (per-flag gating)
  4. --isolate namespace              -> hard parse-time error on macOS (no netns)

Exit 0 on PASS, 1 on FAIL. No pip deps.

Usage:
    coop_isolate_exports.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ST = HERE.parent
WORKTREE = str(ST / "spawn" / "it2agent-worktree")
FLAG = str(ST / "flags" / "it2agent-flag")
REPO = str(ST.parent)  # the git root that contains it2agent/

DOCKER = "env_COMPOSE_PROJECT_NAME="
DB_SCHEMA = "env_IT2AGENT_DB_SCHEMA="
DB_PGOPTIONS = "env_PGOPTIONS="


def flag(env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([FLAG, *args], env=env, capture_output=True, text=True)


def create_dry(env: dict, *isolate_args: str) -> subprocess.CompletedProcess:
    argv = [
        WORKTREE, "create", "--repo", REPO,
        "--id", "coop-iso", "--role", "backend", "--dry-run", *isolate_args,
    ]
    return subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)


def main() -> int:
    env = dict(os.environ)
    env["IT2AGENT_CONFIG"] = os.path.join(
        tempfile.mkdtemp(prefix="it2agent-coop-iso-"), "config.toml"
    )
    env.pop("IT2AGENT_FORCE", None)  # never let a stray force defeat the gates

    failures: list[str] = []

    # create --dry-run itself gates on agent.worktree_isolation; keep it ON for
    # every case so we are isolating the *isolate* flags, not the create gate.
    flag(env, "enable", "agent.worktree_isolation")

    # 1) both isolate flags ON -> both export blocks present.
    flag(env, "enable", "agent.isolate_docker")
    flag(env, "enable", "agent.isolate_db")
    on = create_dry(env, "--isolate", "docker,db")
    on_has_docker = DOCKER in on.stdout
    on_has_db = DB_SCHEMA in on.stdout and DB_PGOPTIONS in on.stdout
    print("[isolate ON  docker,db] docker=%s db=%s isolate_line=%s" % (
        on_has_docker, on_has_db, "isolate=" in on.stdout))
    for line in on.stdout.splitlines():
        if line.startswith("env_") or line.startswith("isolate="):
            print("    ", line)
    if not (on_has_docker and on_has_db):
        failures.append("flags ON did not export both docker + db isolation vars")

    # 2) both isolate flags OFF -> NO env_ exports (inert), still exit 0.
    flag(env, "disable", "agent.isolate_docker")
    flag(env, "disable", "agent.isolate_db")
    off = create_dry(env, "--isolate", "docker,db")
    off_env_lines = [l for l in off.stdout.splitlines() if l.startswith("env_")]
    print("[isolate OFF docker,db] exit=%d env_lines=%d -> %s" % (
        off.returncode, len(off_env_lines), "inert OK" if not off_env_lines else "LEAK"))
    if off.returncode != 0 or off_env_lines:
        failures.append("flags OFF still exported isolation vars (not inert)")

    # 3) per-flag gating: only docker ON -> only COMPOSE_PROJECT_NAME.
    flag(env, "enable", "agent.isolate_docker")
    only = create_dry(env, "--isolate", "docker,db")
    only_docker = DOCKER in only.stdout and DB_SCHEMA not in only.stdout
    print("[only docker ON] docker=%s db_absent=%s -> %s" % (
        DOCKER in only.stdout, DB_SCHEMA not in only.stdout,
        "per-flag OK" if only_docker else "GATING FAIL"))
    if not only_docker:
        failures.append("per-flag gating broken: db exported while isolate_db OFF")
    flag(env, "disable", "agent.isolate_docker")

    # 4) --isolate namespace is rejected on macOS (parse-time, every command).
    ns = create_dry(env, "--isolate", "namespace")
    ns_rejected = ns.returncode != 0 and "namespace is not supported" in ns.stderr
    print("[--isolate namespace] exit=%d -> %s" % (
        ns.returncode, "rejected OK" if ns_rejected else "NOT REJECTED"))
    print("    ", ns.stderr.strip()[:100])
    if not ns_rejected:
        failures.append("--isolate namespace was not rejected on macOS")

    # leave every flag we touched OFF.
    for f in ("agent.worktree_isolation", "agent.isolate_docker", "agent.isolate_db"):
        flag(env, "disable", f)

    if failures:
        print("\nCOOP-ISOLATE FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nCOOP-ISOLATE PASS: docker/db isolation exports are env-only, per-flag "
          "gated, inert when OFF, and namespace is rejected on macOS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

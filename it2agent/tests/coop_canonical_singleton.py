#!/usr/bin/env python3
"""Cooperation AC — canonical port is a per-(repo, name) SINGLETON (#110).

Beyond the always-unique dynamic port every agent gets, the *focused* agent may
additionally hold the project-normal port (e.g. 3000) for a named port so it
answers on the address of record. This proves the singleton contract WITHOUT any
iTerm2: exactly one agent per repo holds each canonical name at a time, and
`--release` hands it back so the next agent can take it.

Sets up a throwaway git repo (so the operator's real repos and leases are never
touched), enables `agent.worktree_isolation` + `agent.canonical_port` in an
isolated config, then:

  1. agent A `create --ports web,db` -> A's worktree is made; A holds
     canonical_port_web/db (the focused holder)
  2. agent B `create --ports web,db` -> B's worktree is made; B gets NO canonical
     port (singleton — A holds it and A's worktree is live, so not reclaimable)
  3. agent A `canonical --release`    -> hands both names back
  4. agent B `canonical --ports web,db` acquire -> now B takes them (handover
     only after release)

The canonical lease keys on the worktree path and is only reclaimed when that
worktree is gone (or its owner pid died), so the singleton is meaningful only
against REAL worktrees — hence we `create` them (in the throwaway repo) rather
than compute a bare plan. Two distinct --id values = two distinct worktrees.

Exit 0 on PASS, 1 on FAIL. No pip deps.

Usage:
    coop_canonical_singleton.py
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


def run(env: dict, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), env=env, capture_output=True, text=True, timeout=30)


def canonical_lines(cp: subprocess.CompletedProcess) -> list[str]:
    return [l for l in cp.stdout.splitlines() if l.startswith("canonical_port_")]


def git(repo: str, *argv: str) -> None:
    subprocess.run(["git", "-C", repo, *argv], check=True,
                   capture_output=True, text=True)


def make_repo() -> str:
    repo = tempfile.mkdtemp(prefix="it2agent-coop-canon-")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@e.st")
    git(repo, "config", "user.name", "t")
    (Path(repo) / "README.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def main() -> int:
    env = dict(os.environ)
    env["IT2AGENT_CONFIG"] = os.path.join(
        tempfile.mkdtemp(prefix="it2agent-coop-canon-cfg-"), "config.toml"
    )
    env.pop("IT2AGENT_FORCE", None)
    # keep worktrees/leases inside the throwaway tree
    repo = make_repo()
    env["IT2AGENT_WORKTREE_ROOT"] = os.path.join(repo, ".wt")

    for f in ("agent.worktree_isolation", "agent.canonical_port"):
        subprocess.run([FLAG, "enable", f], env=env, capture_output=True, text=True)

    failures: list[str] = []

    def create(agent_id: str) -> subprocess.CompletedProcess:
        return run(env, WORKTREE, "create", "--repo", repo,
                   "--id", agent_id, "--role", "backend", "--ports", "web,db")

    def acquire(agent_id: str) -> subprocess.CompletedProcess:
        return run(env, WORKTREE, "canonical", "--repo", repo,
                   "--id", agent_id, "--role", "backend", "--ports", "web,db")

    # 1) A's worktree is created and A becomes the canonical holder.
    a1 = create("canon-A")
    a1_lines = canonical_lines(a1)
    print("A create ->", a1_lines or "(none)")
    if not any("canonical_port_web=" in l for l in a1_lines):
        failures.append("holder A did not receive a canonical port")

    # 2) B's worktree is created but B is refused canonical while A holds it.
    b1 = create("canon-B")
    b1_lines = canonical_lines(b1)
    print("B create (A holds) ->", b1_lines or "(none)")
    if b1_lines:
        failures.append("singleton broken: B got a canonical port while A holds it")

    # 3) A releases.
    rel = run(env, WORKTREE, "canonical", "--repo", repo, "--id", "canon-A",
              "--role", "backend", "--ports", "web,db", "--release")
    print("A release ->", rel.stdout.strip())
    if "canonical_released=" not in rel.stdout:
        failures.append("A --release did not report a release")

    # 4) B now takes them (handover only after release). B's worktree already
    # exists from step 2, so the acquire is a pure canonical claim.
    b2 = acquire("canon-B")
    b2_lines = canonical_lines(b2)
    print("B acquire (after A release) ->", b2_lines or "(none)")
    if not any("canonical_port_web=" in l for l in b2_lines):
        failures.append("handover failed: B could not take canonical after A released")

    for f in ("agent.worktree_isolation", "agent.canonical_port"):
        subprocess.run([FLAG, "disable", f], env=env, capture_output=True, text=True)

    if failures:
        print("\nCOOP-CANONICAL FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nCOOP-CANONICAL PASS: canonical port is a per-repo singleton — one "
          "holder at a time, released names hand over to the next agent")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Cooperation AC — a fleet of agents in ONE repo gets DISTINCT leased ports (#105/#110).

Runtime isolation is the point: N agents working the same repo must never collide
on a port. This spins up a fleet of THREE worktrees CONCURRENTLY (to exercise the
allocation mutex against a time-of-check/time-of-use race) in a throwaway git repo
and asserts:

  1. each agent leased a DISTINCT dynamic port (no TOCTOU collision)
  2. `it2agent-worktree status --json` lists the whole fleet with branch/port/
     ports/canonical/clean/stale keys (machine-readable for the janitor/daemon/MCP)
  3. `ls` renders the same fleet as a human table

No iTerm2 needed — this is the worktree/lease layer, which is pure filesystem.
Everything lives in a throwaway repo + isolated config, torn down on exit.

Exit 0 on PASS, 1 on FAIL. No pip deps.

Usage:
    coop_fleet_ports.py [--size 3]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ST = HERE.parent
WORKTREE = str(ST / "spawn" / "it2agent-worktree")
FLAG = str(ST / "flags" / "it2agent-flag")


def git(repo: str, *argv: str) -> None:
    subprocess.run(["git", "-C", repo, *argv], check=True, capture_output=True, text=True)


def make_repo() -> str:
    repo = tempfile.mkdtemp(prefix="it2agent-coop-fleet-")
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@e.st")
    git(repo, "config", "user.name", "t")
    (Path(repo) / "README.md").write_text("x\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def port_from(stdout: str) -> int | None:
    for line in stdout.splitlines():
        if line.startswith("port="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=3)
    args = ap.parse_args()

    env = dict(os.environ)
    env["IT2AGENT_CONFIG"] = os.path.join(
        tempfile.mkdtemp(prefix="it2agent-coop-fleet-cfg-"), "config.toml"
    )
    env.pop("IT2AGENT_FORCE", None)
    repo = make_repo()
    env["IT2AGENT_WORKTREE_ROOT"] = os.path.join(repo, ".wt")

    subprocess.run([FLAG, "enable", "agent.worktree_isolation"], env=env,
                   capture_output=True, text=True)

    roles = ["backend", "frontend", "reviewer", "docs", "infra"][: args.size]
    failures: list[str] = []

    # 1) create the whole fleet CONCURRENTLY (exercises the alloc mutex / TOCTOU).
    procs = []
    for i, role in enumerate(roles):
        procs.append((role, subprocess.Popen(
            [WORKTREE, "create", "--repo", repo, "--id", "fleet-%d" % i,
             "--role", role],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)))
    ports: dict[str, int] = {}
    for role, p in procs:
        out, _ = p.communicate(timeout=60)
        port = port_from(out)
        print("create %-9s -> port=%s" % (role, port))
        if port is None:
            failures.append("%s: no port in create output" % role)
        else:
            ports[role] = port

    distinct = len(set(ports.values())) == len(ports) and len(ports) == len(roles)
    print("leased ports:", sorted(ports.values()), "-> distinct=%s" % distinct)
    if not distinct:
        failures.append("port collision in fleet (TOCTOU): %r" % ports)

    # 2) status --json lists the whole fleet with stable keys.
    st = subprocess.run([WORKTREE, "status", "--repo", repo, "--json"],
                        env=env, capture_output=True, text=True, timeout=30)
    try:
        fleet = json.loads(st.stdout)
    except json.JSONDecodeError as exc:
        fleet = []
        failures.append("status --json was not valid JSON: %s" % exc)
    print("status --json count=%d" % len(fleet))
    for row in fleet:
        print("   ", json.dumps({k: row.get(k) for k in
              ("branch", "port", "ports", "canonical", "clean", "stale")}, sort_keys=True))
    if len(fleet) != len(roles):
        failures.append("status --json listed %d rows, expected %d" % (len(fleet), len(roles)))
    required_keys = {"branch", "worktree", "port", "ports", "canonical",
                     "changes", "clean", "stale", "stale_reason"}
    for row in fleet:
        missing = required_keys - set(row)
        if missing:
            failures.append("status row missing keys %s" % sorted(missing))
            break
    json_ports = sorted(r.get("port") for r in fleet if r.get("port") is not None)
    if json_ports and json_ports != sorted(ports.values()):
        failures.append("status --json ports %r != leased %r" % (json_ports, sorted(ports.values())))

    # 3) ls renders a human table for the same fleet.
    ls = subprocess.run([WORKTREE, "ls", "--repo", repo],
                        env=env, capture_output=True, text=True, timeout=30)
    ls_rows = [l for l in ls.stdout.splitlines() if l.startswith("it2agent/")]
    print("ls table rows=%d (header + %d agents)" % (len(ls.stdout.splitlines()), len(ls_rows)))
    if len(ls_rows) != len(roles):
        failures.append("ls table listed %d agent rows, expected %d" % (len(ls_rows), len(roles)))

    subprocess.run([FLAG, "disable", "agent.worktree_isolation"], env=env,
                   capture_output=True, text=True)

    if failures:
        print("\nCOOP-FLEET FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nCOOP-FLEET PASS: %d agents in one repo got distinct leased ports and "
          "the fleet is listed by ls / status --json" % len(roles))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Cooperation AC — MCP orchestration end-to-end with the FULL 9-tool surface
(#18 + #92 read side). Extends ac8_mcp_drive.py.

Stands in for a real MCP agent (Claude Code) driving ``it2agent_mcp.py`` over stdio
JSON-RPC. Boots a durable broker, points the MCP server at it, and drives the whole
orchestration chain — now including the two READ tools the team bridge exposes:

    spawn -> assign -> handoff(pending) -> handoff(completed) ->
    send_message -> team_tasks -> read_messages

Then verifies REAL durable side effects out of band (not just the JSON reply):

  * tools/list                -> exactly the 9 tools (was 7 before team_tasks +
                                 read_messages landed; ac8 still asserts 7 and is
                                 now stale — this driver is the current one)
  * spawn / assign            -> agents in the broker registry (query)
  * handoff x2                -> the task lifecycle is an append-only history
  * team_tasks(team)          -> groups the 'task:' goals into a per-task
                                 pending->completed lifecycle (the durable team
                                 read-model, survives lead death)
  * send_message              -> delivered to the recipient mailbox (poll)
  * read_messages(since=0)    -> returns the message WITHOUT acking; a subsequent
                                 out-of-band poll STILL sees it (idempotent read,
                                 not a consume)

The one thing this cannot do headless is open a real iTerm2 tab (the spawn
launcher needs a live iTerm2 + the ``iterm2`` package), so ``launch.launched`` is
expected False here — that is the 🔴 part. The registry side effect still lands.

Exit 0 on PASS, 1 on FAIL. Broker torn down on exit. No pip deps.

Usage:
    coop_mcp_orchestrate.py [--broker PATH] [--mcp PATH] [--python PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_BROKER = HERE.parent / "broker" / "it2agent_broker.py"
DEFAULT_MCP = HERE.parent / "mcp" / "it2agent_mcp.py"

SPAWN_ID = "coop-spawned"
ASSIGN_ID = "coop-assigned"
RECIPIENT = "coop-recipient"
SENDER = "coop-driver"
TEAM_KEY = "team:session-coopmcp"
TASK_GOAL = "task:T1"
EXPECTED_TOOLS = [
    "spawn", "assign", "handoff", "send_message", "status",
    "list_agents", "team_tasks", "read_messages", "help",
]


def rpc(sock_path: str, obj: dict, timeout: float = 5.0) -> dict:
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock_path)
    try:
        s.sendall((json.dumps(obj) + "\n").encode())
        line = s.makefile().readline()
    finally:
        s.close()
    return json.loads(line) if line else {}


def wait_for_socket(sock: str, deadline: float) -> bool:
    while time.time() < deadline:
        if os.path.exists(sock):
            try:
                rpc(sock, {"op": "health"})
                return True
            except OSError:
                pass
        time.sleep(0.1)
    return False


def jsonrpc(idx: int, method: str, params: dict) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": idx, "method": method, "params": params})


def call(idx: int, name: str, arguments: dict) -> str:
    return jsonrpc(idx, "tools/call", {"name": name, "arguments": arguments})


def structured(resp: dict) -> dict:
    result = resp.get("result") or {}
    return result.get("structuredContent") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default=str(DEFAULT_BROKER))
    ap.add_argument("--mcp", default=str(DEFAULT_MCP))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="it2agent-coop-mcp-")
    db = os.path.join(tmp, "broker.db")
    sock = os.path.join(tmp, "broker.sock")

    env = dict(os.environ)
    env["IT2AGENT_BROKER_DB"] = db
    env["IT2AGENT_BROKER_SOCK"] = sock
    env["IT2AGENT_FORCE"] = "1"  # bypass agent.mcp / agent.broker gates for the harness

    broker = None
    failures: list[str] = []
    try:
        broker = subprocess.Popen(
            [args.python, args.broker, "serve", "--no-gate"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_for_socket(sock, time.time() + args.timeout):
            print("FAIL: broker did not come up")
            return 1

        lines = [
            jsonrpc(1, "initialize", {"protocolVersion": "2024-11-05"}),
            jsonrpc(2, "tools/list", {}),
            call(3, "spawn", {"command": "true", "id": SPAWN_ID, "role": "backend", "task": "build"}),
            call(4, "assign", {"agent_id": ASSIGN_ID, "role": "frontend", "task": "ui"}),
            call(5, "handoff", {"agent_id": TEAM_KEY, "goal": TASK_GOAL,
                                "context_ptr": "ctx.md", "verification_status": "pending"}),
            call(6, "handoff", {"agent_id": TEAM_KEY, "goal": TASK_GOAL,
                                "verification_status": "completed"}),
            call(7, "send_message", {"to": RECIPIENT, "from": SENDER, "body": "coop hello"}),
            call(8, "team_tasks", {"team": TEAM_KEY}),
            call(9, "read_messages", {"agent": RECIPIENT, "since": 0}),
        ]
        proc = subprocess.run(
            [args.python, args.mcp, "--no-gate"],
            input="\n".join(lines) + "\n",
            env=env, capture_output=True, text=True, timeout=30,
        )
        responses = {}
        for raw in proc.stdout.splitlines():
            raw = raw.strip()
            if not raw:
                continue
            responses[json.loads(raw).get("id")] = json.loads(raw)

        # tools/list must expose exactly the 9 tools, in order.
        tools = (responses.get(2, {}).get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools]
        print("tools/list ->", names)
        if names != EXPECTED_TOOLS:
            failures.append("tools/list = %r, expected %r" % (names, EXPECTED_TOOLS))

        sp = structured(responses.get(3, {}))
        tt = structured(responses.get(8, {}))
        rm = structured(responses.get(9, {}))
        print("spawn        ->", json.dumps(sp, sort_keys=True))
        print("team_tasks   ->", json.dumps(tt, sort_keys=True))
        print("read_messages->", json.dumps(rm, sort_keys=True))

        # ---- Out-of-band verification against the durable store ------------
        reg = rpc(sock, {"op": "query"})
        ids = {a.get("session_id") for a in (reg.get("agents") or [])}
        print("broker registry ->", sorted(i for i in ids if i))
        for want in (SPAWN_ID, ASSIGN_ID):
            if want not in ids:
                failures.append("%s not registered in the broker" % want)

        # team_tasks composed the per-task lifecycle from the append-only history.
        tasks = tt.get("tasks") or []
        t1 = next((t for t in tasks if t.get("goal") == TASK_GOAL), None)
        if t1 is None:
            failures.append("team_tasks did not surface %s" % TASK_GOAL)
        else:
            lifecycle = [h.get("verification_status") for h in (t1.get("history") or [])]
            print("team_tasks lifecycle for %s ->" % TASK_GOAL, lifecycle, "status=", t1.get("status"))
            if lifecycle != ["pending", "completed"] or t1.get("status") != "completed":
                failures.append("team_tasks lifecycle wrong: %r status=%r" % (lifecycle, t1.get("status")))

        # read_messages returned the message and did NOT ack it.
        rm_msgs = rm.get("messages") or []
        if not any(m.get("body") == "coop hello" for m in rm_msgs):
            failures.append("read_messages did not return the delivered message")
        replay = rpc(sock, {"op": "poll", "agent": RECIPIENT})
        replay_bodies = [m.get("body") for m in (replay.get("messages") or [])]
        print("out-of-band poll after read_messages ->", replay_bodies)
        if "coop hello" not in replay_bodies:
            failures.append("read_messages consumed the message (should be non-destructive)")

        launched = (sp.get("launch") or {}).get("launched")
        print("NOTE: spawn tab launch (needs live iTerm2) launched=%r "
              "-> registry/handoff/mailbox side effects asserted above" % launched)
    finally:
        if broker is not None and broker.poll() is None:
            broker.terminate()
            try:
                broker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                broker.kill()

    if failures:
        print("\nCOOP-MCP FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nCOOP-MCP PASS: the 9-tool MCP surface drives spawn/assign/handoff/"
          "send_message with real durable side effects, and team_tasks/read_messages "
          "read them back (team lifecycle + non-destructive inbox)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

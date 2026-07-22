"""AC8 driver — MCP tools have REAL side effects, not just JSON (#73).

Stands in for a real MCP agent (Claude Code) driving ``it2agent_mcp.py`` over
stdio JSON-RPC. It boots a durable broker, points the MCP server at it via
``IT2AGENT_BROKER_SOCK``, then calls the orchestration tools in sequence:

    spawn -> assign -> handoff -> send_message

For each, it then queries the broker **directly** (out of band) to prove the
side effect actually landed in the durable store, rather than trusting the MCP
reply alone:

  * spawn        -> agent registered in the broker registry (query)
  * assign       -> role/task upserted (query)
  * handoff      -> record present + latest (handoff_get)
  * send_message -> message delivered to the recipient mailbox (poll) + ack

The one thing this cannot do headless is open a real iTerm2 tab: the spawn
launcher shells out to the daemon, which needs a live iTerm2 + the ``iterm2``
package, so ``launch.launched`` is expected to be False here (marked 🔴 in the
prompt). The *registry* side effect of spawn still lands and is asserted.

Exit 0 on PASS, 1 on FAIL. Broker torn down on exit. No pip deps.

Usage:
    ac8_mcp_drive.py [--broker PATH] [--mcp PATH] [--python PATH]
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

SPAWN_ID = "ac8-spawned"
ASSIGN_ID = "ac8-assigned"
RECIPIENT = "ac8-recipient"
SENDER = "ac8-driver"
GOAL = "ac8-goal"


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
    """Pull the tool payload out of an MCP tools/call response."""
    result = resp.get("result") or {}
    return result.get("structuredContent") or {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", default=str(DEFAULT_BROKER))
    ap.add_argument("--mcp", default=str(DEFAULT_MCP))
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--timeout", type=float, default=10.0)
    args = ap.parse_args()

    tmp = tempfile.mkdtemp(prefix="it2agent-ac8-")
    db = os.path.join(tmp, "broker.db")
    sock = os.path.join(tmp, "broker.sock")

    env = dict(os.environ)
    env["IT2AGENT_BROKER_DB"] = db
    env["IT2AGENT_BROKER_SOCK"] = sock
    env["IT2AGENT_FORCE"] = "1"  # bypass the agent.mcp / agent.broker gates for the harness

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

        # Build the JSON-RPC script an MCP client would send.
        lines = [
            jsonrpc(1, "initialize", {"protocolVersion": "2024-11-05"}),
            jsonrpc(2, "tools/list", {}),
            call(3, "spawn", {"command": "true", "id": SPAWN_ID, "role": "backend", "task": "build"}),
            call(4, "assign", {"agent_id": ASSIGN_ID, "role": "frontend", "task": "ui"}),
            call(5, "handoff", {"agent_id": SPAWN_ID, "goal": GOAL,
                                "context_ptr": "ctx.md", "verification_status": "pending"}),
            call(6, "send_message", {"to": RECIPIENT, "from": SENDER, "body": "ac8 hello"}),
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
            obj = json.loads(raw)
            responses[obj.get("id")] = obj

        # tools/list must expose the 7 tools.
        tools = (responses.get(2, {}).get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools]
        print("tools/list ->", names)
        if len(names) != 7:
            failures.append("tools/list returned %d tools, expected 7" % len(names))

        sp = structured(responses.get(3, {}))
        asg = structured(responses.get(4, {}))
        ho = structured(responses.get(5, {}))
        sm = structured(responses.get(6, {}))
        print("spawn        ->", json.dumps(sp, sort_keys=True))
        print("assign       ->", json.dumps(asg, sort_keys=True))
        print("handoff      ->", json.dumps(ho, sort_keys=True))
        print("send_message ->", json.dumps(sm, sort_keys=True))

        # ---- Out-of-band verification against the durable store ------------
        reg = rpc(sock, {"op": "query"})
        ids = {a.get("session_id") for a in (reg.get("agents") or [])}
        print("broker registry ->", sorted(i for i in ids if i))
        if SPAWN_ID not in ids:
            failures.append("spawn did not register %s in the broker" % SPAWN_ID)
        if ASSIGN_ID not in ids:
            failures.append("assign did not register %s in the broker" % ASSIGN_ID)

        got = rpc(sock, {"op": "handoff_get", "agent_id": SPAWN_ID, "goal": GOAL})
        h = got.get("handoff") or {}
        print("broker handoff_get ->", json.dumps(h, sort_keys=True))
        if h.get("goal") != GOAL or h.get("context_ptr") != "ctx.md":
            failures.append("handoff tool did not persist a record in the store")

        polled = rpc(sock, {"op": "poll", "agent": RECIPIENT})
        msgs = polled.get("messages") or []
        print("broker poll(%s) ->" % RECIPIENT, json.dumps(msgs, sort_keys=True))
        if not any(m.get("body") == "ac8 hello" for m in msgs):
            failures.append("send_message did not deliver to the recipient mailbox")
        else:
            top = max(int(m["id"]) for m in msgs)
            acked = rpc(sock, {"op": "ack", "agent": RECIPIENT, "msg_id": top})
            print("broker ack ->", json.dumps(acked, sort_keys=True))
            if not acked.get("ok"):
                failures.append("ack of delivered message failed")
            replay = rpc(sock, {"op": "poll", "agent": RECIPIENT})
            if replay.get("messages"):
                failures.append("message re-delivered after ack (exactly-once broken)")

        # Spawn's tab launch is expected to fail headless (no iTerm2); that is
        # the 🔴 part. We only assert the non-tab side effect (registration).
        launched = (sp.get("launch") or {}).get("launched")
        print("NOTE: spawn tab launch (needs live iTerm2) launched=%r "
              "-> registry side effect asserted above" % launched)
    finally:
        if broker is not None and broker.poll() is None:
            broker.terminate()
            try:
                broker.wait(timeout=5)
            except subprocess.TimeoutExpired:
                broker.kill()

    if failures:
        print("\nAC8 FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nAC8 PASS: spawn/assign/handoff/send_message each produced a real durable side effect")
    return 0


if __name__ == "__main__":
    sys.exit(main())

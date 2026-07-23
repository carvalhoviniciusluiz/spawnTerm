#!/usr/bin/env python3
"""Socket-backed e2e for the #94 MCP read surface (team_tasks / read_messages).

Unlike the pure handler tests (``test_tools.py``, mock broker), this spins up a
**real** ``BrokerServer`` on a unix socket in a tmpdir, seeds it through the
reusable ``BrokerClient`` exactly the way the team bridge (#92) writes — a
``register`` + append-only ``handoff_put`` task lifecycle + a few mailbox
messages — then drives the real MCP tool handlers against that live broker.

It proves the two properties the issue calls out:

  * ``team_tasks`` reconstructs the per-task pending→completed lifecycle from the
    append-only handoff history, keyed on the team key, after the fact.
  * ``read_messages`` is NON-destructive: a ``since`` read returns only the delta
    and NEVER acks, so the ack cursor is untouched and a subsequent normal poll
    still replays every message. Contrasted with a real ``ack`` (which DOES
    consume) to make the difference unmistakable.

No sleeps: the server signals a ``threading.Event`` the instant it is accepting
connections (mirrors ``broker/tests/test_server_e2e.py``). Fast and non-flaky.
"""

import asyncio
import os
import sys
import tempfile
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MCP_DIR = os.path.dirname(_HERE)
_BROKER_DIR = os.path.join(os.path.dirname(_MCP_DIR), "broker")
for _p in (_MCP_DIR, _BROKER_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tools  # noqa: E402
from tools import Deps  # noqa: E402
from client import BrokerClient  # noqa: E402
from server import BrokerServer  # noqa: E402

TEAM_KEY = "team:session-abcd1234"


def _no_spawn(*_args, **_kwargs):
    """The read tools must never launch a tab; fail loudly if one tries."""
    raise AssertionError("read tools must not invoke the spawn launcher")


class TestTeamReadE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sock = os.path.join(self._tmp.name, "broker.sock")
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.server = BrokerServer(self.sock, self.db)
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.assertTrue(self.ready.wait(timeout=10), "server did not start")
        self.client = BrokerClient(sock_path=self.sock, timeout=10)
        self.deps = Deps(broker=self.client, spawn=_no_spawn)
        self._seed()

    def tearDown(self):
        self.server.request_stop()
        self.thread.join(timeout=10)
        self._tmp.cleanup()

    def _run(self):
        asyncio.run(self.server.serve(ready=self.ready))

    def _seed(self):
        # A teammate registered under the team (as TeammateIdle would).
        self.assertTrue(self.client.request({
            "op": "register", "session_id": "teammate-1",
            "capabilities": ["claude-code-teammate", TEAM_KEY], "alive": True,
        })["ok"])
        # Append-only task lifecycle, exactly as the bridge's handoff_put writes:
        # task:T1 pending→completed, task:T2 pending.
        for goal, status in (
            ("task:T1", "pending"),
            ("task:T2", "pending"),
            ("task:T1", "completed"),
        ):
            self.assertTrue(self.client.request({
                "op": "handoff_put", "agent_id": TEAM_KEY,
                "goal": goal, "verification_status": status,
            })["ok"])
        # A few durable mailbox messages for one agent.
        self.msg_ids = []
        for body in ("m1", "m2", "m3"):
            resp = self.client.request({"op": "send", "to": "impl-1", "from": "lead", "body": body})
            self.assertTrue(resp["ok"])
            self.msg_ids.append(resp["id"])

    # -- team_tasks --------------------------------------------------------- #

    def test_team_tasks_reconstructs_lifecycle_after_the_fact(self):
        result = tools.call_tool("team_tasks", {"team": TEAM_KEY}, self.deps)
        self.assertTrue(result["ok"])
        self.assertEqual(result["team"], TEAM_KEY)
        self.assertEqual(result["count"], 2)
        by_task = {t["task"]: t for t in result["tasks"]}
        self.assertEqual(
            [h["verification_status"] for h in by_task["T1"]["history"]],
            ["pending", "completed"],
        )
        self.assertEqual(by_task["T1"]["status"], "completed")
        self.assertEqual(by_task["T2"]["status"], "pending")
        self.assertEqual(len(by_task["T2"]["history"]), 1)

    def test_team_tasks_derives_key_from_session_id(self):
        # Passing the raw session id derives the same key the bridge wrote.
        result = tools.call_tool("team_tasks", {"team": "abcd1234-ffff"}, self.deps)
        self.assertTrue(result["ok"])
        self.assertEqual(result["team"], TEAM_KEY)
        self.assertEqual(result["count"], 2)

    # -- read_messages (non-destructive offset read) ------------------------ #

    def test_read_messages_delta_and_non_destructive(self):
        first, second, third = self.msg_ids

        # since=0 (default) → the whole inbox.
        allmsgs = tools.call_tool("read_messages", {"agent": "impl-1"}, self.deps)
        self.assertTrue(allmsgs["ok"])
        self.assertEqual([m["id"] for m in allmsgs["messages"]], [first, second, third])

        # since=<first> → only the delta after it.
        delta = tools.call_tool("read_messages", {"agent": "impl-1", "since": first}, self.deps)
        self.assertEqual([m["id"] for m in delta["messages"]], [second, third])
        self.assertEqual(delta["since"], first)

        # NON-DESTRUCTIVE: reading did not advance the cursor. A subsequent read
        # from the start still sees everything...
        again = tools.call_tool("read_messages", {"agent": "impl-1"}, self.deps)
        self.assertEqual([m["id"] for m in again["messages"]], [first, second, third])
        # ...and a raw broker poll (the normal consumer path) still replays all.
        polled = self.client.request({"op": "poll", "agent": "impl-1"})
        self.assertEqual([m["id"] for m in polled["messages"]], [first, second, third])

        # Contrast: an actual ack DOES consume — proving read_messages never did.
        self.assertTrue(self.client.request({"op": "ack", "agent": "impl-1", "msg_id": third})["ok"])
        drained = self.client.request({"op": "poll", "agent": "impl-1"})
        self.assertEqual(drained["messages"], [])
        # And read_messages now reflects the acked (consumed) state too.
        after_ack = tools.call_tool("read_messages", {"agent": "impl-1"}, self.deps)
        self.assertEqual(after_ack["messages"], [])


if __name__ == "__main__":
    unittest.main()

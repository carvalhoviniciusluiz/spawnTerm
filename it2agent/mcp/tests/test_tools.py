#!/usr/bin/env python3
"""Tests for the pure MCP tool handlers (#18).

Each handler is exercised with a mock broker client and a mock spawn launcher,
asserting the exact broker op it produces and the well-formed result it returns.
No sockets, no iTerm2, no live services.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from tools import Deps  # noqa: E402


class FakeBroker:
    """Records every op and returns a scripted reply (or raises)."""

    def __init__(self, reply=None, raises=None):
        self.reply = reply if reply is not None else {"ok": True}
        self.raises = raises
        self.ops = []

    def request(self, op):
        self.ops.append(op)
        if self.raises is not None:
            raise self.raises
        return self.reply


class FakeLauncher:
    """Records spawn launches; returns a canned launch result."""

    def __init__(self, result=None):
        self.result = result if result is not None else {"launched": True, "returncode": 0}
        self.calls = []

    def __call__(self, arguments, command, plan):
        self.calls.append((arguments, command, plan))
        return self.result


def make_deps(broker=None, launcher=None):
    return Deps(
        broker=broker if broker is not None else FakeBroker(),
        spawn=launcher if launcher is not None else FakeLauncher(),
    )


class TestSpawn(unittest.TestCase):
    def test_builds_plan_and_launches(self):
        launcher = FakeLauncher()
        broker = FakeBroker(reply={"ok": True, "agent": {"session_id": "a1"}})
        deps = make_deps(broker, launcher)
        result = tools.call_tool(
            "spawn",
            {"command": "claude", "id": "a1", "role": "impl", "task": "build #18"},
            deps,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["command"], "claude")
        self.assertIn("cwd", result["plan"])
        # Identity vars were built by the pure plan (dot-free user.agent_*).
        names = [n for n, _ in result["plan"]["variables"]]
        self.assertIn("user.agent_id", names)
        self.assertIn("user.agent_role", names)
        # Launcher was invoked once with the resolved command.
        self.assertEqual(len(launcher.calls), 1)
        self.assertEqual(launcher.calls[0][1], "claude")
        # Because an id was given, the agent was registered in the broker.
        self.assertTrue(result["registered"])
        self.assertEqual(broker.ops[0]["op"], "register")
        self.assertEqual(broker.ops[0]["session_id"], "a1")
        self.assertEqual(broker.ops[0]["role"], "impl")

    def test_command_as_argv_list(self):
        launcher = FakeLauncher()
        deps = make_deps(launcher=launcher)
        result = tools.call_tool("spawn", {"command": ["npm", "run", "dev"]}, deps)
        self.assertTrue(result["ok"])
        self.assertEqual(result["command"], "npm run dev")

    def test_no_id_skips_registration(self):
        broker = FakeBroker()
        deps = make_deps(broker)
        result = tools.call_tool("spawn", {"command": "bash"}, deps)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["registered"])
        self.assertEqual(broker.ops, [])

    def test_missing_command_is_bad_request(self):
        result = tools.call_tool("spawn", {}, make_deps())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_request")

    def test_home_and_cwd_conflict_is_bad_request(self):
        result = tools.call_tool(
            "spawn", {"command": "bash", "home": True, "cwd": "/tmp"}, make_deps()
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_request")

    def test_broker_down_does_not_fail_spawn(self):
        broker = FakeBroker(raises=OSError("no socket"))
        deps = make_deps(broker)
        result = tools.call_tool("spawn", {"command": "bash", "id": "a1"}, deps)
        self.assertTrue(result["ok"])  # launch is primary; register is best-effort
        self.assertFalse(result["registered"])
        self.assertIn("no socket", result["register_error"])


class TestAssign(unittest.TestCase):
    def test_maps_to_register(self):
        broker = FakeBroker(reply={"ok": True, "agent": {"session_id": "a1"}})
        deps = make_deps(broker)
        result = tools.call_tool(
            "assign",
            {"agent_id": "a1", "role": "reviewer", "task": "review", "capabilities": ["git"]},
            deps,
        )
        self.assertTrue(result["ok"])
        op = broker.ops[0]
        self.assertEqual(op, {
            "op": "register",
            "session_id": "a1",
            "role": "reviewer",
            "task": "review",
            "capabilities": ["git"],
            "alive": True,
        })

    def test_missing_agent_id(self):
        result = tools.call_tool("assign", {"role": "x"}, make_deps())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_request")

    def test_bad_capabilities_type(self):
        result = tools.call_tool("assign", {"agent_id": "a1", "capabilities": "git"}, make_deps())
        self.assertFalse(result["ok"])


class TestHandoff(unittest.TestCase):
    def test_maps_to_handoff_put(self):
        broker = FakeBroker(reply={"ok": True, "handoff": {"id": 1}})
        deps = make_deps(broker)
        result = tools.call_tool(
            "handoff",
            {
                "agent_id": "a1",
                "goal": "ship #18",
                "context_ptr": "/notes",
                "owned_files": ["a.py"],
                "verification_status": "passing",
            },
            deps,
        )
        self.assertTrue(result["ok"])
        op = broker.ops[0]
        self.assertEqual(op["op"], "handoff_put")
        self.assertEqual(op["agent_id"], "a1")
        self.assertEqual(op["goal"], "ship #18")
        self.assertEqual(op["owned_files"], ["a.py"])
        self.assertEqual(op["verification_status"], "passing")

    def test_missing_goal(self):
        result = tools.call_tool("handoff", {"agent_id": "a1"}, make_deps())
        self.assertFalse(result["ok"])


class TestSendMessage(unittest.TestCase):
    def test_maps_to_send(self):
        broker = FakeBroker(reply={"ok": True, "id": 7})
        deps = make_deps(broker)
        result = tools.call_tool(
            "send_message", {"to": "reviewer", "from": "a1", "body": "please review"}, deps
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["id"], 7)
        self.assertEqual(
            broker.ops[0], {"op": "send", "to": "reviewer", "from": "a1", "body": "please review"}
        )

    def test_missing_body(self):
        result = tools.call_tool("send_message", {"to": "x", "from": "y"}, make_deps())
        self.assertFalse(result["ok"])


class TestStatus(unittest.TestCase):
    def test_maps_to_handoff_get(self):
        broker = FakeBroker(reply={"ok": True, "handoff": None})
        deps = make_deps(broker)
        result = tools.call_tool("status", {"agent_id": "a1"}, deps)
        self.assertTrue(result["ok"])
        self.assertEqual(broker.ops[0], {"op": "handoff_get", "agent_id": "a1"})

    def test_with_goal_scope(self):
        broker = FakeBroker(reply={"ok": True, "handoff": {"id": 3}})
        deps = make_deps(broker)
        tools.call_tool("status", {"agent_id": "a1", "goal": "g"}, deps)
        self.assertEqual(broker.ops[0], {"op": "handoff_get", "agent_id": "a1", "goal": "g"})


class TestListAgents(unittest.TestCase):
    def test_maps_to_query_no_filters(self):
        broker = FakeBroker(reply={"ok": True, "agents": [], "count": 0})
        deps = make_deps(broker)
        result = tools.call_tool("list_agents", {}, deps)
        self.assertTrue(result["ok"])
        self.assertEqual(broker.ops[0], {"op": "query"})

    def test_maps_to_query_with_filters(self):
        broker = FakeBroker(reply={"ok": True, "agents": [], "count": 0})
        deps = make_deps(broker)
        tools.call_tool("list_agents", {"role": "impl", "alive": True, "capability": "git"}, deps)
        self.assertEqual(
            broker.ops[0], {"op": "query", "role": "impl", "alive": True, "capability": "git"}
        )


class TestTeamTasks(unittest.TestCase):
    def _history(self):
        # Oldest→newest, as broker handoff_history returns (task:T1 pending→
        # completed, task:T2 pending). A non-task goal must be ignored.
        return {
            "ok": True,
            "handoffs": [
                {"id": 1, "goal": "task:T1", "verification_status": "pending"},
                {"id": 2, "goal": "task:T2", "verification_status": "pending"},
                {"id": 3, "goal": "task:T1", "verification_status": "completed"},
                {"id": 4, "goal": "note:misc", "verification_status": "x"},
            ],
            "count": 4,
        }

    def test_maps_to_handoff_history_and_groups_by_task(self):
        broker = FakeBroker(reply=self._history())
        deps = make_deps(broker)
        result = tools.call_tool("team_tasks", {"team": "team:session-ab12cd34"}, deps)
        self.assertTrue(result["ok"])
        # It read handoff_history for the given team key (no new broker op).
        self.assertEqual(
            broker.ops[0], {"op": "handoff_history", "agent_id": "team:session-ab12cd34"}
        )
        self.assertEqual(result["team"], "team:session-ab12cd34")
        self.assertEqual(result["count"], 2)  # T1 and T2; note:misc dropped
        by_task = {t["task"]: t for t in result["tasks"]}
        # T1's append-only lifecycle is pending→completed; status is the latest.
        self.assertEqual([h["verification_status"] for h in by_task["T1"]["history"]],
                         ["pending", "completed"])
        self.assertEqual(by_task["T1"]["status"], "completed")
        # T2 is still pending (single row).
        self.assertEqual(by_task["T2"]["status"], "pending")
        self.assertEqual(len(by_task["T2"]["history"]), 1)

    def test_derives_team_key_from_session_id(self):
        # A raw session id is derived to team:session-<sid8>, matching the bridge.
        broker = FakeBroker(reply={"ok": True, "handoffs": [], "count": 0})
        deps = make_deps(broker)
        result = tools.call_tool("team_tasks", {"team": "ab12cd34-9999-0000"}, deps)
        self.assertEqual(broker.ops[0]["agent_id"], "team:session-ab12cd34")
        self.assertEqual(result["team"], "team:session-ab12cd34")

    def test_missing_team_is_bad_request(self):
        result = tools.call_tool("team_tasks", {}, make_deps())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_request")

    def test_broker_error_is_passed_through(self):
        broker = FakeBroker(reply={"ok": False, "error": "not_found"})
        deps = make_deps(broker)
        result = tools.call_tool("team_tasks", {"team": "t"}, deps)
        self.assertFalse(result["ok"])


class TestReadMessages(unittest.TestCase):
    def _mailbox(self):
        return {
            "ok": True,
            "messages": [
                {"id": 1, "from": "a", "to": "impl-1", "body": "m1"},
                {"id": 2, "from": "a", "to": "impl-1", "body": "m2"},
                {"id": 3, "from": "b", "to": "impl-1", "body": "m3"},
            ],
            "count": 3,
        }

    def test_polls_without_since_and_never_acks(self):
        broker = FakeBroker(reply=self._mailbox())
        deps = make_deps(broker)
        result = tools.call_tool("read_messages", {"agent": "impl-1"}, deps)
        self.assertTrue(result["ok"])
        # Composed over plain poll (no 'since' pushed to the broker) and NO ack.
        self.assertEqual(broker.ops, [{"op": "poll", "agent": "impl-1"}])
        self.assertEqual(result["count"], 3)  # since defaults to 0

    def test_since_returns_only_the_delta(self):
        broker = FakeBroker(reply=self._mailbox())
        deps = make_deps(broker)
        result = tools.call_tool("read_messages", {"agent": "impl-1", "since": 2}, deps)
        self.assertTrue(result["ok"])
        self.assertEqual([m["id"] for m in result["messages"]], [3])
        self.assertEqual(result["since"], 2)
        # Still just a poll; the offset filter is client-side, no ack ever.
        self.assertEqual(broker.ops, [{"op": "poll", "agent": "impl-1"}])

    def test_missing_agent_is_bad_request(self):
        result = tools.call_tool("read_messages", {}, make_deps())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "bad_request")

    def test_bad_since_type_is_bad_request(self):
        for bad in ("2", -1, True):
            result = tools.call_tool("read_messages", {"agent": "a", "since": bad}, make_deps())
            self.assertFalse(result["ok"], f"since={bad!r} should be rejected")


class TestHelp(unittest.TestCase):
    def test_returns_guide_text(self):
        # The help tool needs no broker/spawn; it reads AGENT_GUIDE.md.
        result = tools.call_tool("help", {}, make_deps())
        self.assertTrue(result["ok"])
        self.assertIn("guide", result)
        self.assertGreater(len(result["guide"]), 0)
        # It is the real guide (a title + the "everything is a flag" contract).
        self.assertIn("it2agent", result["guide"])
        self.assertIn("it2agent-flag", result["guide"])

    def test_reads_the_single_source_file(self):
        # No duplication: the returned text is byte-for-byte AGENT_GUIDE.md.
        result = tools.call_tool("help", {}, make_deps())
        self.assertEqual(result["guide"], tools.GUIDE_PATH.read_text(encoding="utf-8"))

    def test_ignores_arguments(self):
        result = tools.call_tool("help", {"unexpected": 1}, make_deps())
        self.assertTrue(result["ok"])


class TestRegistry(unittest.TestCase):
    def test_all_tools_present(self):
        self.assertEqual(
            set(tools.TOOLS),
            {"spawn", "assign", "handoff", "send_message", "status", "list_agents",
             "team_tasks", "read_messages", "help"},
        )

    def test_descriptors_have_valid_schemas(self):
        for desc in tools.tool_descriptors():
            self.assertIn("name", desc)
            self.assertIn("description", desc)
            schema = desc["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertIn("properties", schema)
            self.assertIsInstance(schema["properties"], dict)
            self.assertIsInstance(schema["required"], list)
            # Every required key must be a declared property.
            for req in schema["required"]:
                self.assertIn(req, schema["properties"])

    def test_unknown_tool(self):
        result = tools.call_tool("nope", {}, make_deps())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "unknown_tool")

    def test_purity_no_iterm2(self):
        self.assertNotIn("iterm2", sys.modules)


if __name__ == "__main__":
    unittest.main()

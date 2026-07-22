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
            {"spawn", "assign", "handoff", "send_message", "status", "list_agents", "help"},
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

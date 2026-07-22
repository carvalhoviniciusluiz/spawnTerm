#!/usr/bin/env python3
"""Tests for the pure JSON-RPC / MCP dispatch layer (#18).

Feeds request dicts / raw lines to the transport-free dispatcher and asserts the
exact JSON-RPC replies: the initialize handshake, tools/list schemas, tools/call
mapping (via a mock broker), notifications (no reply), and malformed requests →
JSON-RPC errors (never a crash).
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rpc  # noqa: E402
from tools import Deps  # noqa: E402


class FakeBroker:
    def __init__(self, reply=None, raises=None):
        self.reply = reply if reply is not None else {"ok": True}
        self.raises = raises
        self.ops = []

    def request(self, op):
        self.ops.append(op)
        if self.raises is not None:
            raise self.raises
        return self.reply


def make_deps(broker=None):
    return Deps(
        broker=broker if broker is not None else FakeBroker(),
        spawn=lambda arguments, command, plan: {"launched": True},
    )


def req(method, params=None, req_id=1):
    m = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        m["params"] = params
    return m


class TestInitialize(unittest.TestCase):
    def test_handshake(self):
        resp = rpc.handle_request(req("initialize", {"protocolVersion": "2024-11-05"}), make_deps())
        self.assertEqual(resp["jsonrpc"], "2.0")
        self.assertEqual(resp["id"], 1)
        result = resp["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        self.assertEqual(result["serverInfo"]["name"], "it2agent")

    def test_echoes_requested_version(self):
        resp = rpc.handle_request(req("initialize", {"protocolVersion": "2025-06-18"}), make_deps())
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")

    def test_default_version_when_absent(self):
        resp = rpc.handle_request(req("initialize", {}), make_deps())
        self.assertEqual(resp["result"]["protocolVersion"], rpc.PROTOCOL_VERSION)


class TestToolsList(unittest.TestCase):
    def test_lists_tools_with_schemas(self):
        resp = rpc.handle_request(req("tools/list"), make_deps())
        tool_list = resp["result"]["tools"]
        self.assertEqual(len(tool_list), 7)
        names = [t["name"] for t in tool_list]
        self.assertEqual(
            names,
            ["spawn", "assign", "handoff", "send_message", "status", "list_agents", "help"],
        )
        for t in tool_list:
            self.assertEqual(t["inputSchema"]["type"], "object")
            # Each schema must be JSON-serializable (valid JSON schema doc).
            json.dumps(t["inputSchema"])


class TestToolsCall(unittest.TestCase):
    def test_call_maps_to_broker_op(self):
        broker = FakeBroker(reply={"ok": True, "id": 42})
        resp = rpc.handle_request(
            req("tools/call", {"name": "send_message", "arguments": {"to": "b", "from": "a", "body": "hi"}}),
            make_deps(broker),
        )
        result = resp["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["id"], 42)
        # content is a text block whose JSON round-trips to the payload.
        text = result["content"][0]["text"]
        self.assertEqual(json.loads(text)["ok"], True)
        self.assertEqual(broker.ops[0]["op"], "send")

    def test_broker_error_response_is_tool_error(self):
        broker = FakeBroker(reply={"ok": False, "error": "not_found"})
        resp = rpc.handle_request(
            req("tools/call", {"name": "status", "arguments": {"agent_id": "x"}}),
            make_deps(broker),
        )
        self.assertTrue(resp["result"]["isError"])

    def test_broker_unreachable_is_tool_error_not_crash(self):
        broker = FakeBroker(raises=OSError("connection refused"))
        resp = rpc.handle_request(
            req("tools/call", {"name": "list_agents", "arguments": {}}),
            make_deps(broker),
        )
        result = resp["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["error"], "backend_unavailable")

    def test_unknown_tool_is_tool_error(self):
        resp = rpc.handle_request(
            req("tools/call", {"name": "frobnicate", "arguments": {}}), make_deps()
        )
        self.assertTrue(resp["result"]["isError"])
        self.assertEqual(resp["result"]["structuredContent"]["error"], "unknown_tool")

    def test_missing_tool_name(self):
        resp = rpc.handle_request(req("tools/call", {"arguments": {}}), make_deps())
        self.assertTrue(resp["result"]["isError"])

    def test_bad_arguments_is_validation_error(self):
        resp = rpc.handle_request(
            req("tools/call", {"name": "assign", "arguments": {}}), make_deps()
        )
        self.assertTrue(resp["result"]["isError"])
        self.assertEqual(resp["result"]["structuredContent"]["error"], "bad_request")


class TestHelpAndResources(unittest.TestCase):
    def test_help_tool_returns_guide_text(self):
        resp = rpc.handle_request(
            req("tools/call", {"name": "help", "arguments": {}}), make_deps()
        )
        result = resp["result"]
        self.assertFalse(result["isError"])
        self.assertIn("it2agent", result["structuredContent"]["guide"])

    def test_initialize_advertises_resources_and_instructions(self):
        resp = rpc.handle_request(req("initialize", {}), make_deps())
        result = resp["result"]
        self.assertIn("resources", result["capabilities"])
        self.assertIn("help", result["instructions"])

    def test_resources_list_has_the_guide(self):
        resp = rpc.handle_request(req("resources/list"), make_deps())
        resources = resp["result"]["resources"]
        self.assertEqual(len(resources), 1)
        self.assertEqual(resources[0]["uri"], rpc.tools.GUIDE_URI)

    def test_resources_read_returns_guide_text(self):
        resp = rpc.handle_request(
            req("resources/read", {"uri": rpc.tools.GUIDE_URI}), make_deps()
        )
        contents = resp["result"]["contents"]
        self.assertEqual(len(contents), 1)
        self.assertIn("it2agent-flag", contents[0]["text"])

    def test_resources_read_unknown_uri_is_empty(self):
        resp = rpc.handle_request(
            req("resources/read", {"uri": "it2agent://nope"}), make_deps()
        )
        self.assertEqual(resp["result"]["contents"], [])


class TestNotificationsAndErrors(unittest.TestCase):
    def test_notification_gets_no_response(self):
        # No "id" => notification => no reply.
        resp = rpc.handle_request(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}, make_deps()
        )
        self.assertIsNone(resp)

    def test_ping(self):
        resp = rpc.handle_request(req("ping"), make_deps())
        self.assertEqual(resp["result"], {})

    def test_unknown_method(self):
        resp = rpc.handle_request(req("does/not/exist"), make_deps())
        self.assertEqual(resp["error"]["code"], rpc.METHOD_NOT_FOUND)

    def test_bad_jsonrpc_version(self):
        resp = rpc.handle_request({"jsonrpc": "1.0", "id": 1, "method": "ping"}, make_deps())
        self.assertEqual(resp["error"]["code"], rpc.INVALID_REQUEST)

    def test_missing_method(self):
        resp = rpc.handle_request({"jsonrpc": "2.0", "id": 1}, make_deps())
        self.assertEqual(resp["error"]["code"], rpc.INVALID_REQUEST)

    def test_non_object_request(self):
        resp = rpc.handle_request([1, 2, 3], make_deps())
        self.assertEqual(resp["error"]["code"], rpc.INVALID_REQUEST)


class TestDispatchLine(unittest.TestCase):
    def test_parse_error_on_bad_json(self):
        line = rpc.dispatch_line("{not json", make_deps())
        obj = json.loads(line)
        self.assertEqual(obj["error"]["code"], rpc.PARSE_ERROR)

    def test_blank_line_is_ignored(self):
        self.assertIsNone(rpc.dispatch_line("   \n", make_deps()))

    def test_notification_line_yields_no_output(self):
        line = rpc.dispatch_line(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}), make_deps()
        )
        self.assertIsNone(line)

    def test_roundtrip_tools_list_line(self):
        line = rpc.dispatch_line(json.dumps(req("tools/list")), make_deps())
        obj = json.loads(line)
        self.assertEqual(len(obj["result"]["tools"]), 7)


if __name__ == "__main__":
    unittest.main()

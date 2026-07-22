#!/usr/bin/env python3
"""Tests for the broker op-dispatch core (#34): routing ping/health, structured
errors for bad/unknown ops, handler-exception isolation, and extensibility."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch  # noqa: E402
import schema  # noqa: E402


class TestDispatch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(db)
        self.ctx = dispatch.BrokerContext(conn=self.conn, db_path=db, sock_path="/tmp/s.sock")

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_ping(self):
        resp = dispatch.handle({"op": "ping"}, self.ctx)
        self.assertTrue(resp["ok"])
        self.assertTrue(resp["pong"])

    def test_ping_echo_round_trips(self):
        resp = dispatch.handle({"op": "ping", "echo": {"n": 7}}, self.ctx)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["echo"], {"n": 7})

    def test_health_reports_version_and_paths(self):
        resp = dispatch.handle({"op": "health"}, self.ctx)
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["schema_version"], schema.SCHEMA_VERSION)
        self.assertEqual(resp["db"], self.ctx.db_path)
        self.assertEqual(resp["sock"], "/tmp/s.sock")
        self.assertIn("ping", resp["ops"])
        self.assertIn("health", resp["ops"])

    def test_unknown_op(self):
        resp = dispatch.handle({"op": "nope"}, self.ctx)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "unknown_op")
        self.assertEqual(resp["error"]["op"], "nope")

    def test_missing_op(self):
        for bad in ({}, {"op": ""}, {"op": 5}):
            resp = dispatch.handle(bad, self.ctx)
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"]["code"], "bad_request")

    def test_non_dict_request(self):
        resp = dispatch.handle([1, 2, 3], self.ctx)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "bad_request")

    def test_handler_exception_becomes_internal_error(self):
        # Register a deliberately-throwing op, prove the server-facing handle()
        # converts it to a structured error instead of propagating.
        @dispatch.register("boom_test_only")
        def _boom(request, ctx):
            raise RuntimeError("kaboom")

        try:
            resp = dispatch.handle({"op": "boom_test_only"}, self.ctx)
            self.assertFalse(resp["ok"])
            self.assertEqual(resp["error"]["code"], "internal")
            self.assertIn("kaboom", resp["error"]["message"])
        finally:
            dispatch.HANDLERS.pop("boom_test_only", None)

    def test_register_is_extensible(self):
        # #35/#36/#37 add ops this way; prove routing picks them up.
        @dispatch.register("send_test_only")
        def _send(request, ctx):
            return dispatch.ok(id=42, to=request.get("to"))

        try:
            resp = dispatch.handle({"op": "send_test_only", "to": "a1"}, self.ctx)
            self.assertTrue(resp["ok"])
            self.assertEqual(resp["id"], 42)
            self.assertEqual(resp["to"], "a1")
        finally:
            dispatch.HANDLERS.pop("send_test_only", None)

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(ValueError):
            dispatch.register("ping")(lambda r, c: dispatch.ok())


if __name__ == "__main__":
    unittest.main()

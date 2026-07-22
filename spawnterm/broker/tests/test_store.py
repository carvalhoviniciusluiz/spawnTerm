#!/usr/bin/env python3
"""Tests for the broker registry + handoff/state store (#36): agent upsert,
query by role/liveness/capability, persistence across a db reopen, append-only
handoff history (latest vs. full history), and structured errors for malformed
input. Pure — a throwaway sqlite file per test, no socket, no sleeps."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch  # noqa: E402
import schema  # noqa: E402
import store  # noqa: E402


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)
        self.ctx = dispatch.BrokerContext(conn=self.conn, db_path=self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    # -- schema ------------------------------------------------------------- #

    def test_v3_creates_agents_and_handoffs(self):
        names = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("agents", names)
        self.assertIn("handoffs", names)
        self.assertGreaterEqual(schema.current_version(self.conn), 3)

    # -- upsert + query ----------------------------------------------------- #

    def test_upsert_then_read_back(self):
        agent = store.upsert_agent(
            self.conn,
            session_id="s1",
            role="coder",
            task="build #36",
            capabilities=["python", "sqlite"],
            alive=True,
            now=100.0,
        )
        self.assertEqual(agent["session_id"], "s1")
        self.assertEqual(agent["role"], "coder")
        self.assertEqual(agent["capabilities"], ["python", "sqlite"])
        self.assertTrue(agent["alive"])
        self.assertEqual(agent["last_seen"], 100.0)

    def test_upsert_is_idempotent_per_session(self):
        store.upsert_agent(self.conn, "s1", role="coder", now=1.0)
        store.upsert_agent(self.conn, "s1", role="reviewer", now=2.0)
        rows = store.query_agents(self.conn)
        self.assertEqual(len(rows), 1)  # replaced, not duplicated
        self.assertEqual(rows[0]["role"], "reviewer")
        self.assertEqual(rows[0]["last_seen"], 2.0)

    def test_query_by_role(self):
        store.upsert_agent(self.conn, "s1", role="coder", now=1.0)
        store.upsert_agent(self.conn, "s2", role="reviewer", now=2.0)
        got = store.query_agents(self.conn, role="reviewer")
        self.assertEqual([a["session_id"] for a in got], ["s2"])

    def test_query_by_liveness(self):
        store.upsert_agent(self.conn, "s1", alive=True, now=1.0)
        store.upsert_agent(self.conn, "s2", alive=False, now=2.0)
        alive = store.query_agents(self.conn, alive=True)
        dead = store.query_agents(self.conn, alive=False)
        self.assertEqual([a["session_id"] for a in alive], ["s1"])
        self.assertEqual([a["session_id"] for a in dead], ["s2"])

    def test_query_by_capability(self):
        store.upsert_agent(self.conn, "s1", capabilities=["python"], now=1.0)
        store.upsert_agent(self.conn, "s2", capabilities=["swift", "ui"], now=2.0)
        got = store.query_agents(self.conn, capability="ui")
        self.assertEqual([a["session_id"] for a in got], ["s2"])
        self.assertEqual(store.query_agents(self.conn, capability="rust"), [])

    def test_query_combines_filters_and_orders_by_last_seen(self):
        store.upsert_agent(self.conn, "s1", role="coder", capabilities=["python"], alive=True, now=1.0)
        store.upsert_agent(self.conn, "s2", role="coder", capabilities=["python"], alive=True, now=3.0)
        store.upsert_agent(self.conn, "s3", role="coder", capabilities=["python"], alive=False, now=2.0)
        got = store.query_agents(self.conn, role="coder", alive=True, capability="python")
        # both alive coders, most-recently-seen first
        self.assertEqual([a["session_id"] for a in got], ["s2", "s1"])

    def test_touch_updates_liveness(self):
        store.upsert_agent(self.conn, "s1", alive=True, now=1.0)
        updated = store.touch_agent(self.conn, "s1", alive=False, now=9.0)
        self.assertIsNotNone(updated)
        self.assertFalse(updated["alive"])
        self.assertEqual(updated["last_seen"], 9.0)
        self.assertIsNone(store.touch_agent(self.conn, "nope"))

    # -- persistence -------------------------------------------------------- #

    def test_registry_survives_db_reopen(self):
        store.upsert_agent(self.conn, "s1", role="coder", capabilities=["python"], now=5.0)
        self.conn.close()
        # A brand-new connection to the same file (simulates a broker restart).
        conn2 = schema.init_db(self.db)
        self.addCleanup(conn2.close)
        got = store.query_agents(conn2, role="coder")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["session_id"], "s1")
        self.assertEqual(got[0]["capabilities"], ["python"])


class TestHandoffs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)
        self.ctx = dispatch.BrokerContext(conn=self.conn, db_path=self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_put_appends_and_get_returns_latest(self):
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-1",
                          owned_files=["f1"], verification_status="pending", now=1.0)
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-2",
                          owned_files=["f1", "f2"], verification_status="verified", now=2.0)
        latest = store.get_handoff(self.conn, "a1", goal="goalA")
        self.assertEqual(latest["context_ptr"], "ctx-2")
        self.assertEqual(latest["owned_files"], ["f1", "f2"])
        self.assertEqual(latest["verification_status"], "verified")

    def test_history_returns_all_in_order(self):
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-1", now=1.0)
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-2", now=2.0)
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-3", now=3.0)
        hist = store.handoff_history(self.conn, "a1", goal="goalA")
        self.assertEqual([h["context_ptr"] for h in hist], ["ctx-1", "ctx-2", "ctx-3"])
        # monotonic, strictly increasing ids
        ids = [h["id"] for h in hist]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), len(ids))

    def test_get_latest_across_goals(self):
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="A1", now=1.0)
        store.put_handoff(self.conn, "a1", "goalB", context_ptr="B1", now=2.0)
        # No goal scope -> newest across all goals for the agent.
        self.assertEqual(store.get_handoff(self.conn, "a1")["context_ptr"], "B1")
        # Scoped reads stay isolated per goal.
        self.assertEqual(store.get_handoff(self.conn, "a1", goal="goalA")["context_ptr"], "A1")

    def test_get_missing_returns_none(self):
        self.assertIsNone(store.get_handoff(self.conn, "ghost"))
        self.assertEqual(store.handoff_history(self.conn, "ghost"), [])

    def test_handoffs_survive_db_reopen(self):
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-1", now=1.0)
        store.put_handoff(self.conn, "a1", "goalA", context_ptr="ctx-2", now=2.0)
        self.conn.close()
        conn2 = schema.init_db(self.db)
        self.addCleanup(conn2.close)
        self.assertEqual(len(store.handoff_history(conn2, "a1")), 2)
        self.assertEqual(store.get_handoff(conn2, "a1")["context_ptr"], "ctx-2")


class TestOps(unittest.TestCase):
    """Exercise the @register handlers through dispatch.handle (the server path)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)
        self.ctx = dispatch.BrokerContext(conn=self.conn, db_path=self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_register_and_query_ops(self):
        r = dispatch.handle(
            {"op": "register", "session_id": "s1", "role": "coder",
             "task": "t", "capabilities": ["python"], "alive": True},
            self.ctx,
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["agent"]["session_id"], "s1")

        q = dispatch.handle({"op": "query", "role": "coder", "capability": "python"}, self.ctx)
        self.assertTrue(q["ok"])
        self.assertEqual(q["count"], 1)
        self.assertEqual(q["agents"][0]["session_id"], "s1")

    def test_touch_op_not_found(self):
        r = dispatch.handle({"op": "touch", "session_id": "ghost"}, self.ctx)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "not_found")

    def test_handoff_ops_roundtrip(self):
        for ctx_ptr in ("c1", "c2"):
            put = dispatch.handle(
                {"op": "handoff_put", "agent_id": "a1", "goal": "g",
                 "context_ptr": ctx_ptr, "owned_files": ["f"], "verification_status": "ok"},
                self.ctx,
            )
            self.assertTrue(put["ok"])

        got = dispatch.handle({"op": "handoff_get", "agent_id": "a1", "goal": "g"}, self.ctx)
        self.assertTrue(got["ok"])
        self.assertEqual(got["handoff"]["context_ptr"], "c2")

        hist = dispatch.handle({"op": "handoff_history", "agent_id": "a1"}, self.ctx)
        self.assertTrue(hist["ok"])
        self.assertEqual(hist["count"], 2)
        self.assertEqual([h["context_ptr"] for h in hist["handoffs"]], ["c1", "c2"])

    def test_handoff_get_missing_is_ok_with_null(self):
        got = dispatch.handle({"op": "handoff_get", "agent_id": "ghost"}, self.ctx)
        self.assertTrue(got["ok"])
        self.assertIsNone(got["handoff"])

    # -- malformed input -> structured error -------------------------------- #

    def test_register_requires_session_id(self):
        for bad in ({"op": "register"}, {"op": "register", "session_id": ""}, {"op": "register", "session_id": 5}):
            r = dispatch.handle(bad, self.ctx)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"]["code"], "bad_request")

    def test_register_rejects_non_bool_alive(self):
        r = dispatch.handle({"op": "register", "session_id": "s1", "alive": "yes"}, self.ctx)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "bad_request")

    def test_query_rejects_wrong_types(self):
        r = dispatch.handle({"op": "query", "alive": "true"}, self.ctx)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "bad_request")

    def test_handoff_put_requires_agent_and_goal(self):
        for bad in ({"op": "handoff_put", "goal": "g"}, {"op": "handoff_put", "agent_id": "a1"}):
            r = dispatch.handle(bad, self.ctx)
            self.assertFalse(r["ok"])
            self.assertEqual(r["error"]["code"], "bad_request")


if __name__ == "__main__":
    unittest.main()

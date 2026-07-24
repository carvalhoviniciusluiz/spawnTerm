#!/usr/bin/env python3
"""Retention + vacuum tests for the broker (#133).

Headless and deterministic (fixed timestamps, no sleeps). Covers:

* **retention** prunes only *acked-old* messages and preserves un-acked messages
  (any age), acked-*recent* messages, the ack cursors, and exactly-once delivery;
* the ``prune`` op validates its args and honors the handoff cap;
* **vacuum** actually shrinks the on-disk file after a large prune;
* the **v5 migration** upgrades an older db in place (retention index added,
  existing rows preserved).
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch  # noqa: E402
import maintenance  # noqa: E402
import schema  # noqa: E402
from mailbox import (  # noqa: E402
    ack_cursor,
    ack_messages,
    poll_messages,
    send_message,
)

_DAY = 86400
_NOW = 1_000_000.0


class TestRetention(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _seed(self):
        """Seed four rows for recipient 'a1' with controlled age + ack state.

        Insert order fixes ids (AUTOINCREMENT):
          id1  acked  + old     -> PRUNABLE
          id2  acked  + recent  -> kept (too new)
          id3  unacked + old    -> kept (never prune un-acked)
          id4  unacked + recent -> kept
        Acking up to id2 acks id1+id2 only (id3/id4 keep their higher ids).
        """
        old = _NOW - 30 * _DAY
        recent = _NOW - 1 * _DAY
        id1 = send_message(self.conn, "boss", "a1", "acked-old", created_at=old)
        id2 = send_message(self.conn, "boss", "a1", "acked-recent", created_at=recent)
        id3 = send_message(self.conn, "boss", "a1", "unacked-old", created_at=old)
        id4 = send_message(self.conn, "boss", "a1", "unacked-recent", created_at=recent)
        ack_messages(self.conn, "a1", id2, now=_NOW)  # acks id1 + id2
        return id1, id2, id3, id4

    def _ids(self):
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT id FROM messages WHERE recipient='a1' ORDER BY id"
            ).fetchall()
        ]

    def test_prune_removes_only_acked_old(self):
        id1, id2, id3, id4 = self._seed()
        removed = maintenance.prune_acked_messages(
            self.conn, max_age_days=7, now=_NOW
        )
        self.assertEqual(removed, 1)
        self.assertEqual(self._ids(), [id2, id3, id4])  # only acked-old gone

    def test_prune_preserves_unacked_cursor_and_exactly_once(self):
        id1, id2, id3, id4 = self._seed()
        cursor_before = ack_cursor(self.conn, "a1")
        maintenance.prune_acked_messages(self.conn, max_age_days=7, now=_NOW)

        # Cursor is untouched (exactly-once bookkeeping preserved).
        self.assertEqual(ack_cursor(self.conn, "a1"), cursor_before)

        # poll replays exactly the un-acked messages — the pruned acked-old row is
        # not resurrected, and the acked-recent row is (still) never re-delivered.
        polled = poll_messages(self.conn, "a1")
        self.assertEqual([m["id"] for m in polled], [id3, id4])
        self.assertEqual(
            [m["body"] for m in polled], ["unacked-old", "unacked-recent"]
        )

        # A brand-new send still gets a strictly-higher id (AUTOINCREMENT), so a
        # future ack can never mis-hit a recycled id.
        id5 = send_message(self.conn, "boss", "a1", "new", created_at=_NOW)
        self.assertGreater(id5, id4)

    def test_prune_never_removes_recent_acked(self):
        # Everything acked but recent -> nothing pruned.
        recent = _NOW - 1 * _DAY
        i = send_message(self.conn, "boss", "a1", "x", created_at=recent)
        ack_messages(self.conn, "a1", i, now=_NOW)
        removed = maintenance.prune_acked_messages(self.conn, max_age_days=7, now=_NOW)
        self.assertEqual(removed, 0)
        self.assertEqual(self._ids(), [i])

    def test_prune_op_validates_and_reports(self):
        # The op uses the real wall clock, so seed relative to real "now": one
        # acked row 30 days old (prunable), one acked row 1 day old (kept).
        real_now = time.time()
        old_id = send_message(
            self.conn, "boss", "a1", "old", created_at=real_now - 30 * _DAY
        )
        send_message(
            self.conn, "boss", "a1", "recent", created_at=real_now - 1 * _DAY
        )
        ack_messages(self.conn, "a1", old_id + 1, now=real_now)  # ack both

        ctx = dispatch.BrokerContext(conn=self.conn, db_path=self.db)
        # Bad arg -> structured bad_request.
        bad = dispatch.handle({"op": "prune", "max_age_days": -1}, ctx)
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "bad_request")
        # Good call prunes only the acked row older than the 7-day window.
        good = dispatch.handle({"op": "prune", "max_age_days": 7}, ctx)
        self.assertTrue(good["ok"])
        self.assertEqual(good["pruned_messages"], 1)
        self.assertEqual(good["pruned_handoffs"], 0)
        self.assertEqual(good["max_age_days"], 7.0)


class TestHandoffCap(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def _put(self, agent, goal, ptr):
        from store import put_handoff

        return put_handoff(self.conn, agent_id=agent, goal=goal, context_ptr=ptr)

    def test_cap_keeps_last_k_per_agent_goal(self):
        for n in range(5):
            self._put("a1", "g1", f"v{n}")
        for n in range(3):
            self._put("a1", "g2", f"w{n}")
        self._put("a2", "g1", "z0")

        removed = maintenance.cap_handoff_history(self.conn, keep=2)
        # a1/g1: 5 -> keep 2 (drop 3); a1/g2: 3 -> keep 2 (drop 1); a2/g1: 1 kept.
        self.assertEqual(removed, 4)

        from store import handoff_history

        g1 = handoff_history(self.conn, "a1", "g1")
        self.assertEqual([h["context_ptr"] for h in g1], ["v3", "v4"])  # newest 2
        g2 = handoff_history(self.conn, "a1", "g2")
        self.assertEqual([h["context_ptr"] for h in g2], ["w1", "w2"])
        self.assertEqual(len(handoff_history(self.conn, "a2", "g1")), 1)

    def test_cap_zero_is_noop(self):
        for n in range(3):
            self._put("a1", "g1", f"v{n}")
        self.assertEqual(maintenance.cap_handoff_history(self.conn, keep=0), 0)


class TestVacuum(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_vacuum_shrinks_file_after_large_prune(self):
        old = _NOW - 30 * _DAY
        big_body = "x" * 1024  # 1 KiB each
        last = 0
        for _ in range(600):  # ~600 KiB of soon-to-be-dead rows
            last = send_message(self.conn, "boss", "a1", big_body, created_at=old)
        ack_messages(self.conn, "a1", last, now=_NOW)
        pruned = maintenance.prune_acked_messages(self.conn, max_age_days=7, now=_NOW)
        self.assertEqual(pruned, 600)

        # Fold the WAL back so the pre-vacuum size reflects the deletes' freelist.
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size_before = os.path.getsize(self.db)
        stats = maintenance.vacuum(self.conn)
        size_after = os.path.getsize(self.db)

        self.assertLess(stats["pages_after"], stats["pages_before"])
        self.assertLess(size_after, size_before)

    def test_vacuum_op_returns_page_counts(self):
        ctx = dispatch.BrokerContext(conn=self.conn, db_path=self.db)
        resp = dispatch.handle({"op": "vacuum"}, ctx)
        self.assertTrue(resp["ok"])
        self.assertIn("pages_before", resp)
        self.assertIn("pages_after", resp)


class TestV5Migration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")

    def tearDown(self):
        self._tmp.cleanup()

    def _build_v4_db(self, conn):
        """Materialize a pre-#133 (v4) db: migrations 1..4 only, no retention index."""
        conn.execute(schema._META_DDL)
        for version in (1, 2, 3, 4):
            for statement in schema.MIGRATIONS[version]:
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (version, time.time()),
            )
        conn.commit()

    def test_old_db_upgrades_in_place_and_adds_retention_index(self):
        conn = schema.open_db(self.db)
        self.addCleanup(conn.close)
        self._build_v4_db(conn)
        self.assertEqual(schema.current_version(conn), 4)
        # Seed a row on the old schema; it must survive the upgrade untouched.
        conn.execute(
            "INSERT INTO messages(sender, recipient, body, created_at, state) "
            "VALUES (?, ?, ?, ?, ?)",
            ("boss", "a1", "legacy", 1.0, "acked"),
        )
        conn.commit()

        indexes_before = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        self.assertNotIn("idx_messages_state_created_at", indexes_before)

        # Upgrade to current.
        self.assertEqual(schema.apply_schema(conn), schema.SCHEMA_VERSION)
        self.assertGreaterEqual(schema.SCHEMA_VERSION, 5)

        indexes_after = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        self.assertIn("idx_messages_state_created_at", indexes_after)
        row = conn.execute(
            "SELECT body FROM messages WHERE recipient='a1'"
        ).fetchone()
        self.assertEqual(row["body"], "legacy")

        # Idempotent: re-running is a no-op.
        self.assertEqual(schema.apply_schema(conn), schema.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()

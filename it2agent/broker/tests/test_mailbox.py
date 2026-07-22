#!/usr/bin/env python3
"""Tests for the durable per-agent mailbox (#35): send→poll→ack happy path,
replay of un-acked messages, strict FIFO ordering, cursor advance, durability
across a db reopen, and structured errors for malformed requests.

Pure layer only (throwaway sqlite db, no socket). The op handlers are exercised
through ``dispatch.handle`` so the same routing the server uses is covered."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dispatch  # noqa: E402
import mailbox  # noqa: E402
import schema  # noqa: E402


class TestMailboxPure(unittest.TestCase):
    """The pure functions that take a live connection (no dispatch, no socket)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(self.db)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_v2_schema_present(self):
        # The mailbox needs at least v2 applied; later migrations (e.g. #36's v3)
        # may raise the current version further, so assert >= 2, not == 2.
        self.assertGreaterEqual(schema.current_version(self.conn), 2)
        tables = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("messages", tables)
        self.assertIn("ack_cursors", tables)

    def test_send_returns_monotonic_ids(self):
        first = mailbox.send_message(self.conn, "a", "b", "one")
        second = mailbox.send_message(self.conn, "a", "b", "two")
        self.assertEqual(second, first + 1)

    def test_send_poll_ack_happy_path(self):
        mid = mailbox.send_message(self.conn, "sender", "agent1", "hello")
        polled = mailbox.poll_messages(self.conn, "agent1")
        self.assertEqual(len(polled), 1)
        msg = polled[0]
        self.assertEqual(msg["id"], mid)
        self.assertEqual(msg["from"], "sender")
        self.assertEqual(msg["to"], "agent1")
        self.assertEqual(msg["body"], "hello")
        self.assertEqual(msg["state"], mailbox.DELIVERED)
        # Ack it; a subsequent poll returns nothing.
        result = mailbox.ack_messages(self.conn, "agent1", mid)
        self.assertEqual(result["acked"], 1)
        self.assertEqual(result["cursor"], mid)
        self.assertEqual(mailbox.poll_messages(self.conn, "agent1"), [])

    def test_poll_marks_pending_delivered(self):
        mid = mailbox.send_message(self.conn, "a", "agent1", "x")
        state = self.conn.execute(
            "SELECT state FROM messages WHERE id=?", (mid,)
        ).fetchone()[0]
        self.assertEqual(state, mailbox.PENDING)
        mailbox.poll_messages(self.conn, "agent1")
        state = self.conn.execute(
            "SELECT state FROM messages WHERE id=?", (mid,)
        ).fetchone()[0]
        self.assertEqual(state, mailbox.DELIVERED)

    def test_replay_of_unacked_on_repoll(self):
        mid = mailbox.send_message(self.conn, "a", "agent1", "replay-me")
        first = mailbox.poll_messages(self.conn, "agent1")
        second = mailbox.poll_messages(self.conn, "agent1")
        # Un-acked (delivered) message replays on the next poll, unchanged id.
        self.assertEqual([m["id"] for m in first], [mid])
        self.assertEqual([m["id"] for m in second], [mid])
        # After ack it stops replaying.
        mailbox.ack_messages(self.conn, "agent1", mid)
        self.assertEqual(mailbox.poll_messages(self.conn, "agent1"), [])

    def test_strict_fifo_ordering(self):
        ids = [mailbox.send_message(self.conn, "a", "agent1", str(n)) for n in range(5)]
        polled = mailbox.poll_messages(self.conn, "agent1")
        self.assertEqual([m["id"] for m in polled], ids)
        self.assertEqual([m["body"] for m in polled], ["0", "1", "2", "3", "4"])

    def test_per_recipient_isolation(self):
        a = mailbox.send_message(self.conn, "s", "agentA", "for-a")
        b = mailbox.send_message(self.conn, "s", "agentB", "for-b")
        pa = mailbox.poll_messages(self.conn, "agentA")
        pb = mailbox.poll_messages(self.conn, "agentB")
        self.assertEqual([m["id"] for m in pa], [a])
        self.assertEqual([m["id"] for m in pb], [b])

    def test_since_cursor_pages_forward(self):
        ids = [mailbox.send_message(self.conn, "a", "agent1", str(n)) for n in range(3)]
        # since = the first id -> only the two later messages come back.
        polled = mailbox.poll_messages(self.conn, "agent1", since=ids[0])
        self.assertEqual([m["id"] for m in polled], ids[1:])

    def test_ack_up_to_cursor_acks_all_below(self):
        ids = [mailbox.send_message(self.conn, "a", "agent1", str(n)) for n in range(4)]
        mailbox.poll_messages(self.conn, "agent1")
        # Ack up to the 3rd message: first three go, last remains.
        result = mailbox.ack_messages(self.conn, "agent1", ids[2])
        self.assertEqual(result["acked"], 3)
        self.assertEqual(result["cursor"], ids[2])
        remaining = mailbox.poll_messages(self.conn, "agent1")
        self.assertEqual([m["id"] for m in remaining], [ids[3]])

    def test_ack_cursor_never_rewinds(self):
        ids = [mailbox.send_message(self.conn, "a", "agent1", str(n)) for n in range(3)]
        mailbox.ack_messages(self.conn, "agent1", ids[2])
        self.assertEqual(mailbox.ack_cursor(self.conn, "agent1"), ids[2])
        # A lower ack must not move the cursor backwards.
        result = mailbox.ack_messages(self.conn, "agent1", ids[0])
        self.assertEqual(result["cursor"], ids[2])
        self.assertEqual(mailbox.ack_cursor(self.conn, "agent1"), ids[2])

    def test_ack_is_idempotent(self):
        mid = mailbox.send_message(self.conn, "a", "agent1", "x")
        first = mailbox.ack_messages(self.conn, "agent1", mid)
        second = mailbox.ack_messages(self.conn, "agent1", mid)
        self.assertEqual(first["acked"], 1)
        self.assertEqual(second["acked"], 0)  # nothing new to ack
        self.assertEqual(second["cursor"], mid)

    def test_ack_cursor_defaults_zero(self):
        self.assertEqual(mailbox.ack_cursor(self.conn, "never-seen"), 0)

    def test_durability_across_reopen(self):
        mid = mailbox.send_message(self.conn, "a", "agent1", "survive-restart")
        mailbox.poll_messages(self.conn, "agent1")
        self.conn.close()
        # Reopen the same file — simulate a broker restart.
        self.conn = schema.init_db(self.db)
        replayed = mailbox.poll_messages(self.conn, "agent1")
        self.assertEqual([m["id"] for m in replayed], [mid])
        self.assertEqual(replayed[0]["body"], "survive-restart")
        # Ack persists across another reopen too.
        mailbox.ack_messages(self.conn, "agent1", mid)
        self.conn.close()
        self.conn = schema.init_db(self.db)
        self.assertEqual(mailbox.poll_messages(self.conn, "agent1"), [])
        self.assertEqual(mailbox.ack_cursor(self.conn, "agent1"), mid)


class TestMailboxOps(unittest.TestCase):
    """The @register handlers, routed through dispatch.handle (as the server does)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db = os.path.join(self._tmp.name, "broker.db")
        self.conn = schema.init_db(db)
        self.ctx = dispatch.BrokerContext(conn=self.conn, db_path=db, sock_path=None)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def test_ops_are_registered(self):
        for op in ("send", "poll", "fetch", "ack"):
            self.assertIn(op, dispatch.HANDLERS)

    def test_send_poll_ack_over_dispatch(self):
        sent = dispatch.handle(
            {"op": "send", "to": "agent1", "from": "boss", "body": "do-it"}, self.ctx
        )
        self.assertTrue(sent["ok"])
        mid = sent["id"]
        polled = dispatch.handle({"op": "poll", "agent": "agent1"}, self.ctx)
        self.assertTrue(polled["ok"])
        self.assertEqual(polled["count"], 1)
        self.assertEqual(polled["messages"][0]["id"], mid)
        self.assertEqual(polled["messages"][0]["body"], "do-it")
        acked = dispatch.handle({"op": "ack", "agent": "agent1", "msg_id": mid}, self.ctx)
        self.assertTrue(acked["ok"])
        self.assertEqual(acked["acked"], 1)
        self.assertEqual(acked["cursor"], mid)
        again = dispatch.handle({"op": "poll", "agent": "agent1"}, self.ctx)
        self.assertEqual(again["count"], 0)

    def test_fetch_is_alias_for_poll(self):
        dispatch.handle({"op": "send", "to": "a1", "from": "s", "body": "hi"}, self.ctx)
        fetched = dispatch.handle({"op": "fetch", "agent": "a1"}, self.ctx)
        self.assertTrue(fetched["ok"])
        self.assertEqual(fetched["count"], 1)

    def test_send_missing_fields_error(self):
        for bad in (
            {"op": "send", "from": "s", "body": "b"},          # no to
            {"op": "send", "to": "a", "body": "b"},            # no from
            {"op": "send", "to": "a", "from": "s"},            # no body
            {"op": "send", "to": "", "from": "s", "body": "b"},  # empty to
            {"op": "send", "to": "a", "from": "s", "body": 5},   # non-string body
        ):
            resp = dispatch.handle(bad, self.ctx)
            self.assertFalse(resp["ok"], bad)
            self.assertEqual(resp["error"]["code"], "bad_request", bad)

    def test_poll_missing_agent_error(self):
        resp = dispatch.handle({"op": "poll"}, self.ctx)
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "bad_request")

    def test_poll_bad_since_error(self):
        for bad_since in (-1, "5", True, 1.5):
            resp = dispatch.handle(
                {"op": "poll", "agent": "a1", "since": bad_since}, self.ctx
            )
            self.assertFalse(resp["ok"], bad_since)
            self.assertEqual(resp["error"]["code"], "bad_request", bad_since)

    def test_ack_bad_msg_id_error(self):
        for bad_id in (None, "3", -2, True, 2.0):
            req = {"op": "ack", "agent": "a1"}
            if bad_id is not None:
                req["msg_id"] = bad_id
            resp = dispatch.handle(req, self.ctx)
            self.assertFalse(resp["ok"], bad_id)
            self.assertEqual(resp["error"]["code"], "bad_request", bad_id)


if __name__ == "__main__":
    unittest.main()

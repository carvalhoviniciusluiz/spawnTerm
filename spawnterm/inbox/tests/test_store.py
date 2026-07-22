#!/usr/bin/env python3
"""Tests for the durable store (#17) against the in-memory broker double:
enqueue assigns a monotonic id, pending reconciliation is order-independent,
decisions are recorded, compaction acks only the resolved prefix, and a broker
that raises degrades to BrokerUnavailable (graceful degradation)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from model import DecisionRecord, InboxRequest, Verdict  # noqa: E402
from store import (BrokerUnavailable, InboxStore, InMemoryBroker,  # noqa: E402
                   REQUESTS_ADDR)


class _RaisingBroker:
    """A broker double whose socket is 'down' — every request raises OSError."""

    def request(self, message):
        raise OSError("connection refused")


class TestInboxStore(unittest.TestCase):
    def setUp(self):
        self.store = InboxStore(InMemoryBroker())

    def test_enqueue_assigns_monotonic_ids(self):
        a = self.store.enqueue(InboxRequest("git.push"))
        b = self.store.enqueue(InboxRequest("git.pull"))
        self.assertEqual(b.id, a.id + 1)

    def test_pending_lists_undecided_requests(self):
        a = self.store.enqueue(InboxRequest("a"))
        b = self.store.enqueue(InboxRequest("b"))
        pending = self.store.list_pending()
        self.assertEqual([r.id for r in pending], [a.id, b.id])

    def test_decision_removes_from_pending_out_of_order(self):
        a = self.store.enqueue(InboxRequest("a"))
        b = self.store.enqueue(InboxRequest("b"))
        c = self.store.enqueue(InboxRequest("c"))
        # Decide the MIDDLE one first (out of order): up-to-cursor ack must not
        # wrongly resolve a or c.
        self.store.record_decision(DecisionRecord(b.id, Verdict.APPROVED.value))
        pending = [r.id for r in self.store.list_pending()]
        self.assertEqual(pending, [a.id, c.id])

    def test_round_trips_request_fields(self):
        self.store.enqueue(InboxRequest("deploy", reversible=True, scope="repo",
                                        cost=3.0, session="s9", agent="ag1",
                                        summary="ship it"))
        got = self.store.list_pending()[0]
        self.assertEqual(got.action, "deploy")
        self.assertTrue(got.reversible)
        self.assertEqual(got.scope, "repo")
        self.assertEqual(got.cost, 3.0)
        self.assertEqual(got.session, "s9")
        self.assertEqual(got.agent, "ag1")
        self.assertEqual(got.summary, "ship it")

    def test_get_returns_decided_request_too(self):
        a = self.store.enqueue(InboxRequest("a"))
        self.store.record_decision(DecisionRecord(a.id, Verdict.REJECTED.value),
                                   compact=False)
        got = self.store.get(a.id)
        self.assertIsNotNone(got)
        self.assertEqual(got.action, "a")

    def test_compact_acks_only_contiguous_resolved_prefix(self):
        a = self.store.enqueue(InboxRequest("a"))
        b = self.store.enqueue(InboxRequest("b"))
        c = self.store.enqueue(InboxRequest("c"))
        # Resolve a and c but NOT b. Prefix stops at a.
        self.store.record_decision(DecisionRecord(a.id, Verdict.APPROVED.value),
                                   compact=False)
        self.store.record_decision(DecisionRecord(c.id, Verdict.APPROVED.value),
                                   compact=False)
        watermark = self.store.compact()
        self.assertEqual(watermark, a.id)
        # a is acked away; b (pending) and c (decided, id>watermark) still present.
        remaining = {m["id"] for m in self.store._poll(REQUESTS_ADDR)}
        self.assertNotIn(a.id, remaining)
        self.assertIn(b.id, remaining)
        self.assertIn(c.id, remaining)
        # Pending is still correct after compaction.
        self.assertEqual([r.id for r in self.store.list_pending()], [b.id])

    def test_notify_agent_sends_to_agent_mailbox(self):
        rec = DecisionRecord(1, Verdict.APPROVED.value, decided_by="human")
        self.store.notify_agent("agent-77", rec)
        # The agent can poll its own mailbox and see the decision.
        reply = self.store.broker.request({"op": "poll", "agent": "agent-77"})
        self.assertEqual(reply["count"], 1)

    def test_graceful_degradation_broker_down(self):
        down = InboxStore(_RaisingBroker())
        with self.assertRaises(BrokerUnavailable):
            down.enqueue(InboxRequest("x"))
        with self.assertRaises(BrokerUnavailable):
            down.list_pending()

    def test_broker_ok_false_is_unavailable(self):
        class _Rejecter:
            def request(self, message):
                return {"ok": False, "error": {"code": "boom", "message": "nope"}}
        with self.assertRaises(BrokerUnavailable):
            InboxStore(_Rejecter()).enqueue(InboxRequest("x"))


if __name__ == "__main__":
    unittest.main()

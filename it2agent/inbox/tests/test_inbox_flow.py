#!/usr/bin/env python3
"""Tests for the inbox workflow glue (#17): the intake -> policy -> queue /
attention -> decision flow with a mock broker + recording emitter, and the
gate-off no-op. No services, no subprocess, no iTerm2."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attention import RecordingEmitter  # noqa: E402
from config import default_config  # noqa: E402
from inbox import Inbox  # noqa: E402
from model import Decision, InboxRequest, Verdict  # noqa: E402
from policy import PolicyConfig  # noqa: E402
from store import InboxStore, InMemoryBroker  # noqa: E402


def make_inbox(config=None, no_gate=True):
    emitter = RecordingEmitter()
    store = InboxStore(InMemoryBroker())
    inbox = Inbox(store, config=config or default_config(), emitter=emitter, no_gate=no_gate)
    return inbox, emitter


class TestIntakeFlow(unittest.TestCase):
    def test_needs_human_enqueues_pending_and_raises_attention(self):
        inbox, emitter = make_inbox()
        out = inbox.submit(InboxRequest("git.push", scope="repo", session="s1",
                                        summary="push"))
        self.assertEqual(out.result.decision, Decision.NEEDS_HUMAN)
        self.assertTrue(out.routed)
        self.assertEqual(len(emitter.routes), 1)
        self.assertEqual(emitter.routes[0].session, "s1")
        # It is pending until a human decides.
        self.assertEqual([r.id for r in inbox.pending()], [out.request.id])

    def test_auto_approve_resolves_without_attention(self):
        inbox, emitter = make_inbox()
        out = inbox.submit(InboxRequest("git.status", reversible=True, scope="read",
                                        agent="ag1"))
        self.assertEqual(out.result.decision, Decision.AUTO_APPROVE)
        self.assertFalse(out.routed)
        self.assertEqual(emitter.routes, [])          # no human paged
        self.assertEqual(inbox.pending(), [])          # already resolved
        # The requesting agent was notified back.
        reply = inbox.store.broker.request({"op": "poll", "agent": "ag1"})
        self.assertEqual(reply["count"], 1)

    def test_block_resolves_without_attention(self):
        inbox, emitter = make_inbox()
        out = inbox.submit(InboxRequest("fs.rm", reversible=False, scope="system",
                                        agent="ag2"))
        self.assertEqual(out.result.decision, Decision.BLOCK)
        self.assertFalse(out.routed)
        self.assertEqual(emitter.routes, [])
        self.assertEqual(inbox.pending(), [])
        # Agent notified with a blocked verdict.
        reply = inbox.store.broker.request({"op": "poll", "agent": "ag2"})
        self.assertEqual(reply["count"], 1)

    def test_human_approve_clears_pending_and_notifies_agent(self):
        inbox, _ = make_inbox()
        out = inbox.submit(InboxRequest("git.push", scope="repo", agent="ag9"))
        self.assertEqual(inbox.pending()[0].id, out.request.id)
        inbox.decide(out.request.id, Verdict.APPROVED, note="lgtm")
        self.assertEqual(inbox.pending(), [])
        reply = inbox.store.broker.request({"op": "poll", "agent": "ag9"})
        self.assertEqual(reply["count"], 1)

    def test_human_edit_carries_edited_descriptor(self):
        inbox, _ = make_inbox()
        out = inbox.submit(InboxRequest("git.push", scope="repo"))
        rec = inbox.decide(out.request.id, Verdict.EDITED,
                           edited_request={"action": "git.push", "scope": "repo",
                                           "cost": 0.0})
        self.assertEqual(rec.verdict, Verdict.EDITED.value)
        self.assertEqual(rec.edited_request["action"], "git.push")
        self.assertEqual(inbox.pending(), [])

    def test_human_reject_clears_pending(self):
        inbox, _ = make_inbox()
        out = inbox.submit(InboxRequest("db.drop", scope="repo"))
        inbox.decide(out.request.id, Verdict.REJECTED, note="too risky")
        self.assertEqual(inbox.pending(), [])

    def test_show_explains_policy(self):
        inbox, _ = make_inbox()
        out = inbox.submit(InboxRequest("git.push", scope="repo"))
        res = inbox.classify(inbox.get(out.request.id))
        self.assertEqual(res.decision, Decision.NEEDS_HUMAN)
        self.assertEqual(res.rule, "default_deny")


class TestGateOff(unittest.TestCase):
    def test_submit_is_noop_when_gated_off(self):
        # no_gate=False and the flag defaults OFF (IT2AGENT_FORCE unset by the
        # test runner) => the inbox does nothing and touches no broker state.
        os.environ.pop("IT2AGENT_FORCE", None)
        inbox, emitter = make_inbox(no_gate=False)
        out = inbox.submit(InboxRequest("git.push", scope="repo", session="s1"))
        self.assertTrue(out.gated_off)
        self.assertIsNone(out.request)
        self.assertEqual(emitter.routes, [])
        # Nothing was enqueued.
        self.assertEqual(inbox.store.broker.request(
            {"op": "poll", "agent": "agent.inbox.requests"})["count"], 0)

    def test_force_env_opens_gate(self):
        os.environ["IT2AGENT_FORCE"] = "1"
        try:
            inbox, _ = make_inbox(no_gate=False)
            out = inbox.submit(InboxRequest("git.status", reversible=True, scope="read"))
            self.assertFalse(out.gated_off)
            self.assertEqual(out.result.decision, Decision.AUTO_APPROVE)
        finally:
            os.environ.pop("IT2AGENT_FORCE", None)


class TestConfigurableAllowList(unittest.TestCase):
    def test_operator_widened_allow_list_auto_approves(self):
        cfg = PolicyConfig(allow_list=frozenset({"deploy.preview"}),
                           auto_scopes=frozenset({"read", "workspace"}),
                           require_reversible_for_auto=False)
        inbox, emitter = make_inbox(config=cfg)
        out = inbox.submit(InboxRequest("deploy.preview", scope="workspace"))
        self.assertEqual(out.result.decision, Decision.AUTO_APPROVE)
        self.assertEqual(emitter.routes, [])


if __name__ == "__main__":
    unittest.main()

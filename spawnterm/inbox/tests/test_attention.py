#!/usr/bin/env python3
"""Tests for attention routing (#17): the pure route decision (which session,
what message) and that only NEEDS_HUMAN pages a human. No subprocess is spawned
(the emit side-effect is exercised via a recording emitter)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attention import RecordingEmitter, build_message, route_attention  # noqa: E402
from model import Decision, InboxRequest  # noqa: E402
from policy import PolicyResult  # noqa: E402


def result(decision):
    return PolicyResult(decision, "rule", "reason")


class TestRouteAttention(unittest.TestCase):
    def test_needs_human_routes_to_session_with_message(self):
        req = InboxRequest("git.push", scope="repo", cost=0.0, session="w0t1p2",
                           summary="push to origin")
        req.id = 7
        route = route_attention(req, result(Decision.NEEDS_HUMAN))
        self.assertIsNotNone(route)
        self.assertEqual(route.session, "w0t1p2")
        self.assertIn("push to origin", route.message)
        self.assertIn("req 7", route.message)
        self.assertIn("scope repo", route.message)

    def test_auto_approve_does_not_route(self):
        req = InboxRequest("git.status", scope="read")
        self.assertIsNone(route_attention(req, result(Decision.AUTO_APPROVE)))

    def test_block_does_not_route(self):
        req = InboxRequest("fs.rm", scope="system")
        self.assertIsNone(route_attention(req, result(Decision.BLOCK)))

    def test_missing_session_still_routes(self):
        req = InboxRequest("deploy", scope="repo", session=None)
        route = route_attention(req, result(Decision.NEEDS_HUMAN))
        self.assertIsNotNone(route)
        self.assertIsNone(route.session)

    def test_message_falls_back_to_action_when_no_summary(self):
        req = InboxRequest("db.migrate", scope="repo")
        req.id = 3
        self.assertIn("db.migrate", build_message(req))

    def test_recording_emitter_captures_route(self):
        emitter = RecordingEmitter()
        req = InboxRequest("x", session="s1")
        req.id = 1
        route = route_attention(req, result(Decision.NEEDS_HUMAN))
        self.assertTrue(emitter.raise_attention(route))
        self.assertEqual(len(emitter.routes), 1)
        self.assertEqual(emitter.routes[0].session, "s1")


if __name__ == "__main__":
    unittest.main()

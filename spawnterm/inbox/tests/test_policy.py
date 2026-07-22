#!/usr/bin/env python3
"""Tests for the pure policy engine (#17): reversibility x scope x cost x
allow-list combinations -> auto-approve / needs-human / block. No I/O."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import default_config  # noqa: E402
from model import Decision, InboxRequest  # noqa: E402
from policy import PolicyConfig, classify  # noqa: E402


def req(action, reversible=False, scope="workspace", cost=0.0):
    return InboxRequest(action=action, reversible=reversible, scope=scope, cost=cost)


class TestPolicyDefaults(unittest.TestCase):
    def setUp(self):
        self.cfg = default_config()

    def test_allowlisted_readonly_auto_approves(self):
        r = classify(req("git.status", reversible=True, scope="read"), self.cfg)
        self.assertEqual(r.decision, Decision.AUTO_APPROVE)
        self.assertEqual(r.rule, "allow_list")

    def test_unknown_action_needs_human_deny_by_default(self):
        r = classify(req("git.push", scope="repo"), self.cfg)
        self.assertEqual(r.decision, Decision.NEEDS_HUMAN)
        self.assertEqual(r.rule, "default_deny")

    def test_allowlisted_but_out_of_scope_needs_human(self):
        # git.diff is allow-listed, but 'workspace' is not an auto scope.
        r = classify(req("git.diff", reversible=True, scope="workspace"), self.cfg)
        self.assertEqual(r.decision, Decision.NEEDS_HUMAN)
        self.assertEqual(r.rule, "scope_guard")

    def test_irreversible_system_action_blocks(self):
        r = classify(req("fs.rm", reversible=False, scope="system"), self.cfg)
        self.assertEqual(r.decision, Decision.BLOCK)
        self.assertEqual(r.rule, "block_scope")


class TestPolicyGuards(unittest.TestCase):
    def test_cost_guard_downgrades_allowlisted(self):
        cfg = PolicyConfig(allow_list=frozenset({"api.read"}), auto_scopes=frozenset({"read"}),
                           max_auto_cost=1.0)
        cheap = classify(req("api.read", reversible=True, scope="read", cost=0.5), cfg)
        self.assertEqual(cheap.decision, Decision.AUTO_APPROVE)
        pricey = classify(req("api.read", reversible=True, scope="read", cost=2.0), cfg)
        self.assertEqual(pricey.decision, Decision.NEEDS_HUMAN)
        self.assertEqual(pricey.rule, "cost_guard")

    def test_reversible_guard(self):
        cfg = PolicyConfig(allow_list=frozenset({"cache.write"}),
                           auto_scopes=frozenset({"read", "workspace"}),
                           require_reversible_for_auto=True)
        r = classify(req("cache.write", reversible=False, scope="workspace"), cfg)
        self.assertEqual(r.decision, Decision.NEEDS_HUMAN)
        self.assertEqual(r.rule, "reversible_guard")
        # Turn the reversibility requirement off -> auto-approve.
        cfg2 = PolicyConfig(allow_list=frozenset({"cache.write"}),
                            auto_scopes=frozenset({"read", "workspace"}),
                            require_reversible_for_auto=False)
        r2 = classify(req("cache.write", reversible=False, scope="workspace"), cfg2)
        self.assertEqual(r2.decision, Decision.AUTO_APPROVE)

    def test_block_list_beats_allow_list(self):
        cfg = PolicyConfig(allow_list=frozenset({"danger"}), block_list=frozenset({"danger"}))
        r = classify(req("danger", reversible=True, scope="read"), cfg)
        self.assertEqual(r.decision, Decision.BLOCK)
        self.assertEqual(r.rule, "block_list")

    def test_block_cost_hard_ceiling(self):
        cfg = PolicyConfig(block_cost=10.0)
        r = classify(req("api.big", cost=50.0), cfg)
        self.assertEqual(r.decision, Decision.BLOCK)
        self.assertEqual(r.rule, "block_cost")

    def test_reversible_avoids_block_scope(self):
        # A reversible action in a block scope is NOT blocked (only irreversible is).
        cfg = default_config()
        r = classify(req("svc.toggle", reversible=True, scope="system"), cfg)
        self.assertNotEqual(r.decision, Decision.BLOCK)
        self.assertEqual(r.decision, Decision.NEEDS_HUMAN)

    def test_classify_is_pure_total_over_axes(self):
        cfg = default_config()
        seen = set()
        for reversible in (True, False):
            for scope in ("read", "workspace", "system"):
                for cost in (0.0, 5.0):
                    r = classify(req("git.status", reversible, scope, cost), cfg)
                    self.assertIn(r.decision, Decision)
                    seen.add(r.decision)
        # Every axis combination resolves to one of the three decisions.
        self.assertTrue(seen.issubset(set(Decision)))


if __name__ == "__main__":
    unittest.main()

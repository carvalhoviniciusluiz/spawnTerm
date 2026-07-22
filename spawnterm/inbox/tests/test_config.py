#!/usr/bin/env python3
"""Tests for the allow-list config loader (#17): conservative defaults, TOML
merge over defaults, and robustness to a missing/garbage file."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from model import Decision, InboxRequest  # noqa: E402
from policy import classify  # noqa: E402


class TestConfig(unittest.TestCase):
    def test_defaults_are_conservative_readonly(self):
        cfg = config.default_config()
        self.assertIn("git.status", cfg.allow_list)
        self.assertNotIn("git.push", cfg.allow_list)   # mutating -> not auto
        self.assertEqual(cfg.auto_scopes, frozenset({"read"}))
        self.assertEqual(cfg.max_auto_cost, 0.0)
        self.assertTrue(cfg.require_reversible_for_auto)

    def test_missing_file_yields_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nope.toml")
            cfg = config.load_config(path=__import__("pathlib").Path(path))
            self.assertEqual(cfg.allow_list, config.default_config().allow_list)

    def test_toml_merges_over_defaults(self):
        body = (
            "[policy]\n"
            'allow_list = ["deploy.staging"]\n'
            'block_list = ["fs.rm_rf"]\n'
            'auto_scopes = ["read", "workspace"]\n'
            "max_auto_cost = 2.5\n"
            "block_cost = 100.0\n"
            "require_reversible_for_auto = false\n"
        )
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "inbox.toml")
            with open(p, "w") as f:
                f.write(body)
            cfg = config.load_config(path=__import__("pathlib").Path(p))
        self.assertEqual(cfg.allow_list, frozenset({"deploy.staging"}))
        self.assertEqual(cfg.block_list, frozenset({"fs.rm_rf"}))
        self.assertEqual(cfg.auto_scopes, frozenset({"read", "workspace"}))
        self.assertEqual(cfg.max_auto_cost, 2.5)
        self.assertEqual(cfg.block_cost, 100.0)
        self.assertFalse(cfg.require_reversible_for_auto)
        # And the merged config actually classifies as configured.
        r = classify(InboxRequest("deploy.staging", reversible=False, scope="workspace",
                                  cost=1.0), cfg)
        self.assertEqual(r.decision, Decision.AUTO_APPROVE)

    def test_garbage_file_falls_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "inbox.toml")
            with open(p, "w") as f:
                f.write("this is : not [ valid toml ===\n")
            cfg = config.load_config(path=__import__("pathlib").Path(p))
        self.assertEqual(cfg.allow_list, config.default_config().allow_list)

    def test_config_path_env_override(self):
        os.environ["SPAWNTERM_INBOX_CONFIG"] = "/tmp/custom/inbox.toml"
        try:
            self.assertEqual(str(config.config_path()), "/tmp/custom/inbox.toml")
        finally:
            del os.environ["SPAWNTERM_INBOX_CONFIG"]


if __name__ == "__main__":
    unittest.main()

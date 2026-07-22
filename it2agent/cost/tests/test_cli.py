#!/usr/bin/env python3
"""Tests for the cost CLI/I/O layer + feature-flag gate (it2agent #16).

Proves: the gate is default-OFF (no-op, exit 0, nothing on stdout), the
``--no-gate``/``IT2AGENT_FORCE`` bypasses work, the source is configurable and
files are discovered/parsed end to end, and the rendered table + JSON reflect
the fixture numbers. Uses an isolated ``IT2AGENT_CONFIG`` so it never reads a
real ~/.config.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost_cli  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class GateTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cfg = os.path.join(self._tmp.name, "config.toml")
        self._old_cfg = os.environ.get("IT2AGENT_CONFIG")
        os.environ["IT2AGENT_CONFIG"] = self._cfg
        self._old_force = os.environ.pop("IT2AGENT_FORCE", None)
        self._old_src = os.environ.pop("IT2AGENT_COST_SOURCE", None)
        self._old_prices = os.environ.pop("IT2AGENT_COST_PRICES", None)

    def tearDown(self):
        for key, val in (
            ("IT2AGENT_CONFIG", self._old_cfg),
            ("IT2AGENT_FORCE", self._old_force),
            ("IT2AGENT_COST_SOURCE", self._old_src),
            ("IT2AGENT_COST_PRICES", self._old_prices),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        self._tmp.cleanup()

    def _write_flag(self, value: str) -> None:
        with open(self._cfg, "w", encoding="utf-8") as handle:
            handle.write('[features]\n"agent.cost_dashboard" = %s\n' % value)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cost_cli.main(argv)
        return rc, out.getvalue(), err.getvalue()


class TestGate(GateTestBase):
    def test_no_config_is_noop(self):
        rc, out, err = self._run(["--source", str(FIXTURES)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")  # nothing on stdout when gated OFF
        self.assertIn("is OFF", err)

    def test_flag_false_is_noop(self):
        self._write_flag("false")
        rc, out, _ = self._run(["--source", str(FIXTURES)])
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")

    def test_flag_true_renders(self):
        self._write_flag("true")
        rc, out, _ = self._run(["--source", str(FIXTURES)])
        self.assertEqual(rc, 0)
        self.assertIn("alpha", out)
        self.assertIn("TOTAL", out)

    def test_no_gate_bypasses(self):
        rc, out, _ = self._run(["--no-gate", "--source", str(FIXTURES)])
        self.assertEqual(rc, 0)
        self.assertIn("TOTAL", out)

    def test_force_env_bypasses(self):
        os.environ["IT2AGENT_FORCE"] = "1"
        rc, out, _ = self._run(["--source", str(FIXTURES)])
        self.assertEqual(rc, 0)
        self.assertIn("TOTAL", out)


class TestRendering(GateTestBase):
    def test_json_reflects_fixture_totals(self):
        rc, out, _ = self._run(["--no-gate", "--source", str(FIXTURES), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertTrue(data["cost_is_estimate"])
        agents = {a["agent"]: a for a in data["agents"]}
        # alpha + beta from the two agent fixtures; gamma from malformed.jsonl.
        self.assertIn("alpha", agents)
        self.assertIn("beta", agents)
        self.assertEqual(agents["alpha"]["tokens"]["total"], 8950)
        self.assertEqual(agents["beta"]["tokens"]["total"], 17000)

    def test_idle_burn_surfaced_in_json(self):
        rc, out, _ = self._run(["--no-gate", "--source", str(FIXTURES), "--json"])
        data = json.loads(out)
        idle_agents = {i["agent"] for i in data["idle_burn"]}
        self.assertIn("alpha", idle_agents)

    def test_soft_cap_breach_warns_on_stderr(self):
        rc, out, err = self._run(
            ["--no-gate", "--source", str(FIXTURES), "--cap-total", "0.01"]
        )
        self.assertEqual(rc, 0)
        self.assertIn("soft cap breached", err)

    def test_price_override_changes_cost(self):
        prices = str(FIXTURES / "prices_override.json")
        rc, out, _ = self._run(
            ["--no-gate", "--source", str(FIXTURES), "--prices", prices, "--json"]
        )
        data = json.loads(out)
        agents = {a["agent"]: a for a in data["agents"]}
        # Opus override is cheaper (10/30/12/1 vs 15/75/18.75/1.5), so alpha's
        # estimated cost drops below the default 0.11325.
        self.assertLess(agents["alpha"]["cost_usd_estimate"], 0.11325)


class TestDiscovery(GateTestBase):
    def test_single_file_source(self):
        rc, out, _ = self._run(
            ["--no-gate", "--source", str(FIXTURES / "agent_beta.jsonl"), "--json"]
        )
        data = json.loads(out)
        self.assertEqual([a["agent"] for a in data["agents"]], ["beta"])

    def test_missing_source_is_empty_not_error(self):
        rc, out, _ = self._run(["--no-gate", "--source", "/no/such/dir", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["agents"], [])
        self.assertEqual(data["total"]["tokens"]["total"], 0)


if __name__ == "__main__":
    unittest.main()

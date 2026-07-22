#!/usr/bin/env python3
"""Tests for the pure cost status-bar formatter (spawnTerm #16, #29 pattern).

No ``iterm2`` dependency — proves the formatting core runs in plain CI and that
importing ``cost_board`` never pulls in ``iterm2``. Covers the compact status
line for the plain case, the idle-burn flag, and the soft-cap flag.
"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cost_board  # noqa: E402
import costlib  # noqa: E402
from cost_board import format_money, format_status_line  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _agg():
    entries = []
    for name in ("agent_alpha.jsonl", "agent_beta.jsonl"):
        with (FIXTURES / name).open("r", encoding="utf-8") as handle:
            entries.extend(costlib.iter_entries(handle))
    return entries, costlib.aggregate(entries, costlib.DEFAULT_PRICES, group_by="cwd")


class TestPurity(unittest.TestCase):
    def test_import_does_not_pull_in_iterm2(self):
        self.assertIn("cost_board", sys.modules)
        self.assertNotIn("iterm2", sys.modules)


class TestMoney(unittest.TestCase):
    def test_small_and_large(self):
        self.assertEqual(format_money(12.34), "$12.34")
        self.assertEqual(format_money(1500), "$1.5k")


class TestStatusLine(unittest.TestCase):
    def test_plain(self):
        _, agg = _agg()
        line = format_status_line(agg)
        self.assertTrue(line.startswith("Σ $0.16"))
        self.assertIn("2 agents", line)
        self.assertNotIn("idle", line)

    def test_idle_flag(self):
        entries, agg = _agg()
        idle = costlib.detect_idle_burn(entries, costlib.DEFAULT_PRICES, group_by="cwd")
        line = format_status_line(agg, idle_burn=idle)
        self.assertIn("⚠", line)
        self.assertIn("idle", line)

    def test_cap_flag(self):
        _, agg = _agg()
        breaches = costlib.evaluate_soft_caps(agg, total=0.01)
        line = format_status_line(agg, breaches=breaches)
        self.assertIn("over-cap", line)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for the pure cost core (spawnTerm #16).

No ``iterm2`` and no network. Reads small Claude-JSONL fixture files, then
exercises: line parsing + malformed-line tolerance, per-agent + total
aggregation, cost math against a known price table (+ overrides), the
agent-association grouping heuristic, idle-burn detection, and soft-cap
evaluation. All assertions are against numbers derived only from the fixtures.
"""

import math
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import costlib  # noqa: E402
from costlib import (  # noqa: E402
    DEFAULT_PRICES,
    Price,
    TokenCounts,
    aggregate,
    compute_cost,
    detect_idle_burn,
    evaluate_soft_caps,
    iter_entries,
    load_price_table,
    parse_line,
    resolve_price,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def read_entries(name, project=None):
    with (FIXTURES / name).open("r", encoding="utf-8") as handle:
        return list(iter_entries(handle, project=project))


class TestPurity(unittest.TestCase):
    def test_import_does_not_pull_in_iterm2(self):
        self.assertIn("costlib", sys.modules)
        self.assertNotIn("iterm2", sys.modules)


class TestParsing(unittest.TestCase):
    def test_parses_assistant_usage_line(self):
        entries = read_entries("agent_alpha.jsonl")
        # user + summary + malformed skipped -> 3 usage-bearing assistant turns.
        self.assertEqual(len(entries), 3)
        first = entries[0]
        self.assertEqual(first.tokens.input, 1000)
        self.assertEqual(first.tokens.output, 500)
        self.assertEqual(first.tokens.cache_creation, 2000)
        self.assertEqual(first.tokens.cache_read, 4000)
        self.assertEqual(first.model, "claude-opus-4-8")
        self.assertEqual(first.cwd, "/work/alpha")
        self.assertEqual(first.git_branch, "feat/alpha")
        self.assertEqual(first.session_id, "sess-alpha")
        self.assertIsNotNone(first.epoch)

    def test_malformed_lines_are_tolerated(self):
        # Garbage JSON, empty line, null, a JSON array (non-dict), and a user
        # turn are all skipped; only the two gamma assistant turns survive.
        entries = read_entries("malformed.jsonl")
        self.assertEqual(len(entries), 2)
        # First gamma turn: clean.
        self.assertEqual(entries[0].tokens.input, 10)
        self.assertEqual(entries[0].tokens.output, 5)
        # Second: input "bad" -> 0, output null -> 0, cache_read 100 kept,
        # timestamp unparseable -> epoch None (but the row still counts).
        self.assertEqual(entries[1].tokens.input, 0)
        self.assertEqual(entries[1].tokens.output, 0)
        self.assertEqual(entries[1].tokens.cache_read, 100)
        self.assertIsNone(entries[1].epoch)

    def test_parse_line_returns_none_for_non_usage(self):
        self.assertIsNone(parse_line('{"type":"user","message":{"role":"user"}}'))
        self.assertIsNone(parse_line("not json"))
        self.assertIsNone(parse_line(""))
        self.assertIsNone(parse_line("[1,2,3]"))


class TestCostMath(unittest.TestCase):
    def test_compute_cost_opus_known_values(self):
        # Opus default: 15 / 75 / 18.75 / 1.50 per 1M.
        tokens = TokenCounts(input=1000, output=500, cache_creation=2000, cache_read=4000)
        price = DEFAULT_PRICES["claude-opus"]
        cost = compute_cost(tokens, price)
        expected = (1000 * 15 + 500 * 75 + 2000 * 18.75 + 4000 * 1.50) / 1_000_000
        self.assertTrue(math.isclose(cost, expected))
        self.assertTrue(math.isclose(cost, 0.096))

    def test_resolve_price_longest_match(self):
        self.assertEqual(resolve_price("claude-opus-4-8", DEFAULT_PRICES), DEFAULT_PRICES["claude-opus"])
        self.assertEqual(resolve_price("claude-sonnet-4-5", DEFAULT_PRICES), DEFAULT_PRICES["claude-sonnet"])
        # Unknown model -> default fallback.
        self.assertEqual(resolve_price("gpt-4o", DEFAULT_PRICES), DEFAULT_PRICES["default"])
        self.assertEqual(resolve_price(None, DEFAULT_PRICES), DEFAULT_PRICES["default"])

    def test_price_override_merges_onto_defaults(self):
        with (FIXTURES / "prices_override.json").open("r", encoding="utf-8") as handle:
            import json

            overrides = json.load(handle)
        table = load_price_table(overrides)
        # Opus overridden.
        self.assertEqual(table["claude-opus"], Price(input=10.0, output=30.0, cache_write=12.0, cache_read=1.0))
        # A brand-new key is added.
        self.assertEqual(table["custom-model"], Price(input=1.0, output=2.0, cache_write=0.0, cache_read=0.0))
        # Untouched keys survive from defaults.
        self.assertEqual(table["claude-sonnet"], DEFAULT_PRICES["claude-sonnet"])


class TestAggregation(unittest.TestCase):
    def setUp(self):
        self.entries = read_entries("agent_alpha.jsonl") + read_entries("agent_beta.jsonl")

    def test_per_agent_and_total_by_cwd(self):
        agg = aggregate(self.entries, DEFAULT_PRICES, group_by="cwd")
        self.assertEqual(set(agg.agents), {"alpha", "beta"})

        alpha = agg.agents["alpha"]
        self.assertEqual(alpha.entries, 3)
        self.assertEqual(alpha.tokens.input, 1300)
        self.assertEqual(alpha.tokens.output, 650)
        self.assertEqual(alpha.tokens.cache_creation, 2000)
        self.assertEqual(alpha.tokens.cache_read, 5000)
        self.assertEqual(alpha.tokens.total, 8950)
        self.assertTrue(math.isclose(alpha.cost_usd, 0.11325))

        beta = agg.agents["beta"]
        self.assertEqual(beta.entries, 1)
        self.assertEqual(beta.tokens.total, 17000)
        self.assertTrue(math.isclose(beta.cost_usd, 0.048))

        self.assertEqual(agg.total.entries, 4)
        self.assertEqual(agg.total.tokens.total, 25950)
        self.assertTrue(math.isclose(agg.total.cost_usd, 0.16125))

    def test_group_by_branch_and_session(self):
        by_branch = aggregate(self.entries, DEFAULT_PRICES, group_by="branch")
        self.assertEqual(set(by_branch.agents), {"feat/alpha", "feat/beta"})
        by_session = aggregate(self.entries, DEFAULT_PRICES, group_by="session")
        self.assertEqual(set(by_session.agents), {"sess-alpha", "sess-beta"})

    def test_sorted_agents_by_cost_desc(self):
        agg = aggregate(self.entries, DEFAULT_PRICES, group_by="cwd")
        ordered = [a.agent for a in agg.sorted_agents()]
        # alpha (0.113) > beta (0.048)
        self.assertEqual(ordered, ["alpha", "beta"])


class TestIdleBurn(unittest.TestCase):
    def test_detects_gap_after_idle(self):
        entries = read_entries("agent_alpha.jsonl")
        # Turns at 10:00, 10:01 (60s, active) and 10:11 (600s gap -> idle burst).
        flagged = detect_idle_burn(entries, DEFAULT_PRICES, group_by="cwd", idle_gap_seconds=300)
        self.assertIn("alpha", flagged)
        burn = flagged["alpha"]
        self.assertEqual(burn.idle_bursts, 1)
        self.assertEqual(burn.idle_tokens, 300)  # 200 input + 100 output
        self.assertTrue(math.isclose(burn.idle_cost_usd, (200 * 15 + 100 * 75) / 1_000_000))

    def test_no_idle_burn_when_gap_large_threshold(self):
        entries = read_entries("agent_alpha.jsonl")
        flagged = detect_idle_burn(entries, DEFAULT_PRICES, group_by="cwd", idle_gap_seconds=3600)
        self.assertNotIn("alpha", flagged)

    def test_status_map_flags_idle_agent(self):
        entries = read_entries("agent_beta.jsonl")  # single turn, no gaps
        flagged = detect_idle_burn(
            entries, DEFAULT_PRICES, group_by="cwd", idle_gap_seconds=300, status_by_agent={"beta": "idle"}
        )
        self.assertIn("beta", flagged)
        self.assertTrue(flagged["beta"].status_idle)


class TestSoftCaps(unittest.TestCase):
    def setUp(self):
        entries = read_entries("agent_alpha.jsonl") + read_entries("agent_beta.jsonl")
        self.agg = aggregate(entries, DEFAULT_PRICES, group_by="cwd")

    def test_total_cap_breach(self):
        breaches = evaluate_soft_caps(self.agg, total=0.10)  # total ~0.161
        scopes = [b.scope for b in breaches]
        self.assertIn("total", scopes)

    def test_per_agent_cap_breach(self):
        # alpha ~0.113 exceeds 0.05; beta ~0.048 does not.
        breaches = evaluate_soft_caps(self.agg, per_agent=0.05)
        scopes = [b.scope for b in breaches]
        self.assertIn("agent:alpha", scopes)
        self.assertNotIn("agent:beta", scopes)

    def test_no_breach_under_cap(self):
        self.assertEqual(evaluate_soft_caps(self.agg, per_agent=1.0, total=1.0), [])


if __name__ == "__main__":
    unittest.main()

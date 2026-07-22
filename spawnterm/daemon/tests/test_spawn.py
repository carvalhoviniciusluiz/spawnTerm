#!/usr/bin/env python3
"""Tests for the pure spawn-plan module (Tier 1.2, #27).

Pure Python / unittest, no deps, no ``iterm2``. Covers cwd resolution
(inherit / --dir / --home / mutual-exclusion error) and the identity set-var
plan (dot-free names, correct order, empty-value skipping, and gate-off
omitting identity entirely).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spawn  # noqa: E402


class TestPurity(unittest.TestCase):
    def test_importing_spawn_does_not_pull_in_iterm2(self):
        self.assertNotIn("iterm2", sys.modules)


class TestResolveCwd(unittest.TestCase):
    def test_default_inherits_spawner_cwd(self):
        self.assertEqual(spawn.resolve_cwd("/work/proj"), "/work/proj")

    def test_dir_override(self):
        self.assertEqual(
            spawn.resolve_cwd("/work/proj", dir_override="/other/dir"),
            "/other/dir",
        )

    def test_home_uses_provided_home(self):
        self.assertEqual(
            spawn.resolve_cwd("/work/proj", use_home=True, home="/Users/me"),
            "/Users/me",
        )

    def test_home_takes_precedence_is_impossible_with_dir(self):
        # --home and --dir are mutually exclusive rather than precedence-ordered.
        with self.assertRaises(spawn.SpawnPlanError):
            spawn.resolve_cwd(
                "/work/proj", dir_override="/other", use_home=True, home="/Users/me"
            )

    def test_home_without_home_value_errors(self):
        with self.assertRaises(spawn.SpawnPlanError):
            spawn.resolve_cwd("/work/proj", use_home=True, home="")

    def test_empty_dir_override_falls_through_to_inherit(self):
        self.assertEqual(spawn.resolve_cwd("/work/proj", dir_override=""), "/work/proj")


class TestIdentityVariables(unittest.TestCase):
    def test_dot_free_names(self):
        variables = spawn.build_identity_variables(
            agent_id="a1", role="worker", task="build", status="busy"
        )
        for name, _ in variables:
            self.assertTrue(name.startswith("user."))
            suffix = name[len("user."):]
            self.assertNotIn(".", suffix, "user-var suffix must be dot-free (#23)")

    def test_expected_names(self):
        variables = spawn.build_identity_variables(
            agent_id="a1", role="worker", task="build", status="busy"
        )
        names = [name for name, _ in variables]
        self.assertEqual(
            names,
            ["user.agent_id", "user.agent_role", "user.agent_task", "user.agent_status"],
        )

    def test_correct_order_and_values(self):
        variables = spawn.build_identity_variables(
            agent_id="a1", role="worker", task="build #10", status="busy"
        )
        self.assertEqual(
            variables,
            [
                ("user.agent_id", "a1"),
                ("user.agent_role", "worker"),
                ("user.agent_task", "build #10"),
                ("user.agent_status", "busy"),
            ],
        )

    def test_empty_values_are_skipped(self):
        variables = spawn.build_identity_variables(role="worker", status="idle")
        self.assertEqual(
            variables,
            [("user.agent_role", "worker"), ("user.agent_status", "idle")],
        )

    def test_gate_off_omits_all_identity(self):
        variables = spawn.build_identity_variables(
            agent_id="a1", role="worker", task="build", status="busy",
            tag_identity=False,
        )
        self.assertEqual(variables, [])

    def test_unknown_status_errors(self):
        with self.assertRaises(spawn.SpawnPlanError):
            spawn.build_identity_variables(status="frobnicate")

    def test_agent_var_name_helper(self):
        self.assertEqual(spawn.agent_var_name("id"), "user.agent_id")
        self.assertEqual(spawn.agent_var_name("status"), "user.agent_status")


class TestBuildSpawnPlan(unittest.TestCase):
    def test_default_plan_inherits_cwd_and_tags(self):
        plan = spawn.build_spawn_plan(
            spawner_cwd="/work/proj", role="worker", task="build", agent_id="a1"
        )
        self.assertEqual(plan.cwd, "/work/proj")
        self.assertTrue(plan.tagged)
        # status defaults to busy, so it is present in the plan.
        self.assertEqual(
            plan.variables,
            [
                ("user.agent_id", "a1"),
                ("user.agent_role", "worker"),
                ("user.agent_task", "build"),
                ("user.agent_status", "busy"),
            ],
        )

    def test_dir_override_plan(self):
        plan = spawn.build_spawn_plan(spawner_cwd="/work", dir_override="/x/y")
        self.assertEqual(plan.cwd, "/x/y")

    def test_home_plan(self):
        plan = spawn.build_spawn_plan(
            spawner_cwd="/work", use_home=True, home="/Users/me"
        )
        self.assertEqual(plan.cwd, "/Users/me")

    def test_mutual_exclusion_error(self):
        with self.assertRaises(spawn.SpawnPlanError):
            spawn.build_spawn_plan(
                spawner_cwd="/work", dir_override="/x", use_home=True, home="/Users/me"
            )

    def test_gate_off_spawns_untagged(self):
        plan = spawn.build_spawn_plan(
            spawner_cwd="/work/proj",
            role="worker",
            task="build",
            agent_id="a1",
            tag_identity=False,
        )
        # cwd still resolved (the tab spawns); identity omitted (gate OFF).
        self.assertEqual(plan.cwd, "/work/proj")
        self.assertFalse(plan.tagged)
        self.assertEqual(plan.variables, [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Regression test for the spawn profile customization (#81, part of #73).

``adapter.spawn_agent`` used to call ``LocalWriteOnlyProfile.set_working_directory``,
which does not exist — every daemon ``spawn`` died with an ``AttributeError`` and
opened no tab. The fix builds the customization via
``adapter.build_spawn_customizations``, which uses the real API
(``set_custom_directory`` + ``set_initial_directory_mode(..._CUSTOM)``).

This test drives that helper exactly as ``spawn_agent`` does, with ``plan.cwd``
set, against the *installed* ``iterm2`` library, asserting it does NOT raise
``AttributeError`` (i.e. the two methods and the enum member exist and accept the
args). It skips when ``iterm2`` is unavailable (e.g. CI).

Purity note: ``iterm2`` is NEVER imported at this module's top level — the skip
check uses ``importlib.util.find_spec`` (which does not execute the module) and
each test imports ``iterm2`` lazily inside its own method. That keeps the sibling
purity tests (test_daemon_gate.py / test_spawn.py, which assert ``iterm2`` is not
in ``sys.modules``) green regardless of test-discovery order.

The full live check (a tab actually opens in cwd + ``launched:true`` via MCP) is
deferred to a live run with the Python API on.
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import spawn  # noqa: E402
import adapter  # noqa: E402

# find_spec only locates the module; it does not import/execute it, so this does
# not pollute sys.modules and cannot trip the purity tests.
HAVE_ITERM2 = importlib.util.find_spec("iterm2") is not None


@unittest.skipUnless(HAVE_ITERM2, "iterm2 package not installed")
class TestBuildSpawnCustomizations(unittest.TestCase):
    def tearDown(self):
        # These tests deliberately import iterm2 (to verify against the real
        # lib), but the sibling purity tests assert iterm2 is not in sys.modules.
        # Since this module sorts first in discovery, drop any iterm2* entries so
        # those tests stay green whatever the run order.
        for name in [n for n in sys.modules if n == "iterm2" or n.startswith("iterm2.")]:
            del sys.modules[name]

    def test_cwd_set_does_not_raise_attributeerror(self):
        # The exact plan shape spawn_agent receives, with a cwd set.
        plan = spawn.build_spawn_plan(spawner_cwd="/work/proj")
        self.assertEqual(plan.cwd, "/work/proj")
        try:
            customizations = adapter.build_spawn_customizations(plan)
        except AttributeError as exc:  # the #81 regression
            self.fail("build_spawn_customizations raised AttributeError: %s" % exc)
        self.assertIsNotNone(customizations)

    def test_uses_real_localwriteonlyprofile_api(self):
        # The methods the fix relies on must exist on the installed lib, and the
        # CUSTOM enum member must resolve.
        import iterm2  # lazy: don't pollute sys.modules at import time.

        profile = iterm2.LocalWriteOnlyProfile()
        self.assertTrue(hasattr(profile, "set_custom_directory"))
        self.assertTrue(hasattr(profile, "set_initial_directory_mode"))
        self.assertIsNotNone(
            iterm2.InitialWorkingDirectory.INITIAL_WORKING_DIRECTORY_CUSTOM
        )
        # And the old, broken method is genuinely absent (documents the bug).
        self.assertFalse(hasattr(profile, "set_working_directory"))

    def test_empty_cwd_skips_directory_customization(self):
        # No cwd -> still returns a usable profile, no directory calls, no raise.
        plan = spawn.SpawnPlan(cwd="")
        customizations = adapter.build_spawn_customizations(plan)
        self.assertIsNotNone(customizations)


class TestBuildLaunchCommand(unittest.TestCase):
    """#85: a `command` override defeats the profile's custom dir, so spawn_agent
    bakes the cd into the command. build_launch_command is pure (no iterm2), so
    these run everywhere. The end-to-end cases prove the quoting: executed by a
    shell, the launch string must cd into cwd before exec'ing the agent."""

    def test_no_cwd_returns_command_unchanged(self):
        self.assertEqual(adapter.build_launch_command("", "/bin/zsh"), "/bin/zsh")

    def test_cwd_wraps_with_cd_and_exec(self):
        out = adapter.build_launch_command("/work/proj", "claude --resume")
        self.assertTrue(out.startswith("/bin/sh -lc "))
        self.assertIn("cd ", out)
        self.assertIn("exec claude --resume", out)

    def test_runs_in_cwd_end_to_end(self):
        # `pwd` stands in for the agent; the launch string must print cwd.
        # `sh -c "$launch"` emulates iTerm tokenizing `command` into argv.
        real = os.path.realpath(tempfile.mkdtemp())
        launch = adapter.build_launch_command(real, "pwd")
        out = subprocess.run(
            ["/bin/sh", "-c", launch], capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(os.path.realpath(out), real)

    def test_cwd_with_spaces_is_quoted(self):
        real = os.path.realpath(tempfile.mkdtemp(prefix="has space "))
        launch = adapter.build_launch_command(real, "pwd")
        out = subprocess.run(
            ["/bin/sh", "-c", launch], capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(os.path.realpath(out), real)


if __name__ == "__main__":
    unittest.main()

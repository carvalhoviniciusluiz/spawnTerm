#!/usr/bin/env python3
"""Headless unit tests for live_smoke.py (#124).

These cover EVERYTHING in the harness that does NOT need a live iTerm2: the
preflight gate (every missing permutation), --only selection, command
construction, the lsof + tmux-output parsers, the MCP launched extractor, the
--json summary shape, the exit-code logic, the temp repo/worktree create+remove
cleanup path, and a real end-to-end headless run of the ccstatus surface (which
needs no API and asserts the exact OSC 21337 bytes).

The live surfaces (spawn/tmux/mcp) are intentionally NOT exercised here — they
need the app. This suite fakes NOTHING about them: it asserts they come back
SKIP (never a fabricated PASS) when the preflight is not satisfied.

Run:  python3 it2agent/tests/test_live_smoke.py
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("live_smoke", HERE / "live_smoke.py")
assert _spec and _spec.loader
ls = importlib.util.module_from_spec(_spec)
# Register before exec so dataclass field-type introspection can resolve the
# module's namespace (dataclasses looks up cls.__module__ in sys.modules).
sys.modules["live_smoke"] = ls
_spec.loader.exec_module(ls)


LIVE_ENV = {"TERM_PROGRAM_VERSION": "3.7.20240101-nightly"}


def _preflight(**over):
    """Build a preflight with everything satisfied unless overridden."""
    kw = dict(env=LIVE_ENV, iterm2_importable=True, api_reader=lambda: "1\n",
              socket_present=True)
    kw.update(over)
    return ls.detect_preflight(**kw)


class PreflightTests(unittest.TestCase):
    def test_all_present_live_ok(self):
        pf = _preflight()
        self.assertTrue(pf.live_ok)
        self.assertTrue(pf.is_37)
        self.assertEqual(pf.missing(), [])

    def test_missing_iterm2_module(self):
        pf = _preflight(iterm2_importable=False)
        self.assertFalse(pf.live_ok)
        self.assertTrue(any("iterm2" in m for m in pf.missing()))

    def test_api_server_off(self):
        pf = _preflight(api_reader=lambda: "0\n")
        self.assertFalse(pf.live_ok)
        self.assertTrue(any("API server is OFF" in m for m in pf.missing()))

    def test_api_reader_empty_is_off(self):
        pf = _preflight(api_reader=lambda: "")
        self.assertFalse(pf.api_enabled)
        self.assertFalse(pf.live_ok)

    def test_not_37_build(self):
        pf = _preflight(env={"TERM_PROGRAM_VERSION": "3.5.0"})
        self.assertFalse(pf.is_37)
        self.assertFalse(pf.live_ok)

    def test_socket_is_informational_only(self):
        # Socket absent must NOT by itself block live_ok (the real connect is
        # authoritative); it is recorded for evidence.
        pf = _preflight(socket_present=False)
        self.assertTrue(pf.live_ok)
        self.assertFalse(pf.socket_present)

    def test_to_dict_shape(self):
        d = _preflight().to_dict()
        for key in ("version", "is_37", "iterm2_importable", "api_enabled",
                    "socket_present", "live_ok", "missing"):
            self.assertIn(key, d)


class SelectSurfacesTests(unittest.TestCase):
    def test_default_is_all_in_order(self):
        self.assertEqual(ls.select_surfaces(None), ls.SURFACE_ORDER)
        self.assertEqual(ls.select_surfaces("all"), ls.SURFACE_ORDER)

    def test_single(self):
        self.assertEqual(ls.select_surfaces("spawn"), ["spawn"])

    def test_comma_list_reordered_to_canonical(self):
        self.assertEqual(ls.select_surfaces("ccstatus,spawn"), ["spawn", "ccstatus"])

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            ls.select_surfaces("bogus")


class CommandConstructionTests(unittest.TestCase):
    def test_daemon_spawn_cmd(self):
        cmd = ls.build_daemon_spawn_cmd("py", "/repo", "aid-1", "/bin/zsh",
                                        role="backend", task="t", status="busy")
        self.assertEqual(cmd[0], "py")
        self.assertIn("spawn", cmd)
        self.assertIn("--no-gate", cmd)
        self.assertEqual(cmd[cmd.index("--dir") + 1], "/repo")
        self.assertEqual(cmd[cmd.index("--id") + 1], "aid-1")
        self.assertEqual(cmd[cmd.index("--role") + 1], "backend")
        self.assertEqual(cmd[cmd.index("--status") + 1], "busy")
        # command comes after the terminating --
        self.assertEqual(cmd[-2:], ["--", "/bin/zsh"])

    def test_ccstatus_cmd(self):
        cmd = ls.build_ccstatus_cmd("py", "busy", "--detail", "x")
        self.assertEqual(cmd[0], "py")
        self.assertEqual(cmd[-3:], ["busy", "--detail", "x"])
        self.assertIn("ccstatus", cmd)

    def test_validate_tmux_cmd(self):
        cmd = ls.build_validate_tmux_cmd("py", "it2smoke-abc")
        self.assertEqual(cmd[cmd.index("--session") + 1], "it2smoke-abc")


class ParserTests(unittest.TestCase):
    def test_parse_lsof_cwd(self):
        out = "p12345\nfcwd\nn/private/tmp/it2smoke-xyz\n"
        self.assertEqual(ls.parse_lsof_cwd(out), "/private/tmp/it2smoke-xyz")

    def test_parse_lsof_cwd_absent(self):
        self.assertIsNone(ls.parse_lsof_cwd("p1\nfcwd\n"))

    SAMPLE = (
        "\n=== iTerm2 Python API over tmux -CC — measured results ===\n"
        "  async_set/get_variable                       : PASS\n"
        "  async_get_screen_contents                    : PASS\n"
        "  custom_escape_sequence (raw)                 : FAIL — needs tmux passthrough wrapping\n"
        "  custom_escape_sequence (tmux-passthrough)    : PASS\n"
        "  NewSessionMonitor attaches                   : PASS (fire = manual: open a new tmux window)\n"
        "==========================================================\n"
        "Paste this table into it2agent/tmux/README.md (Findings). Do not\n"
    )

    def test_parse_tmux_validate_results(self):
        parsed = ls.parse_tmux_validate_results(self.SAMPLE)
        self.assertEqual(parsed["async_set/get_variable"], "PASS")
        self.assertTrue(parsed["custom_escape_sequence (raw)"].startswith("FAIL"))
        self.assertEqual(parsed["custom_escape_sequence (tmux-passthrough)"], "PASS")
        # Prose lines are ignored.
        self.assertNotIn("Paste this table into it2agent/tmux/README.md (Findings). Do not", parsed)

    def test_tmux_surfaces_pass_passthrough_counts(self):
        parsed = ls.parse_tmux_validate_results(self.SAMPLE)
        ok, ev = ls.tmux_surfaces_pass(parsed)
        self.assertTrue(ok)  # raw failed but passthrough passed -> surface 2 OK
        self.assertTrue(any("surface 5" in e for e in ev))

    def test_tmux_surfaces_fail_when_var_fails(self):
        sample = self.SAMPLE.replace("async_set/get_variable                       : PASS",
                                     "async_set/get_variable                       : FAIL (got None)")
        ok, _ = ls.tmux_surfaces_pass(ls.parse_tmux_validate_results(sample))
        self.assertFalse(ok)

    def test_tmux_surfaces_fail_when_empty(self):
        ok, ev = ls.tmux_surfaces_pass({})
        self.assertFalse(ok)

    def test_mcp_launched_true(self):
        line = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {
            "structuredContent": {"launch": {"launched": True, "returncode": 0}}}})
        launched, detail = ls._mcp_launched(line + "\n")
        self.assertTrue(launched)
        self.assertIn("launched", detail)

    def test_mcp_launched_false(self):
        line = json.dumps({"jsonrpc": "2.0", "id": 2, "result": {
            "structuredContent": {"launch": {"launched": False, "error": "No module named 'iterm2'"}}}})
        launched, _ = ls._mcp_launched(line + "\n")
        self.assertFalse(launched)

    def test_mcp_launched_no_response(self):
        launched, detail = ls._mcp_launched("garbage\n{}\n")
        self.assertFalse(launched)


class TmuxMarkerTests(unittest.TestCase):
    """The #127 hardening: a UNIQUE per-run token replaces the basename matcher,
    the match is exact + tty-gated, >1 match fails loudly, and the wait timeout is
    configurable. All the decision logic is pure and covered here; the live poll
    itself stays untested (needs the app)."""

    def test_run_token_shape_and_uniqueness(self):
        a = ls.make_run_token()
        b = ls.make_run_token()
        self.assertTrue(a.startswith("it2smoke-"))
        self.assertIn(str(os.getpid()), a)
        self.assertNotEqual(a, b)  # entropy tail differs per call
        # Only [a-z0-9-] so it survives intact inside a basename / tmux name.
        import re
        self.assertRegex(a, r"^[a-z0-9-]+$")

    def test_harness_mints_unique_token(self):
        h1 = ls.Harness()
        h2 = ls.Harness()
        try:
            self.assertTrue(h1.run_token.startswith("it2smoke-"))
            self.assertNotEqual(h1.run_token, h2.run_token)
        finally:
            h1.cleanup()
            h2.cleanup()

    def test_resolve_tmux_timeout_default(self):
        self.assertEqual(ls.resolve_tmux_timeout(None, env={}), ls.DEFAULT_TMUX_TIMEOUT)

    def test_resolve_tmux_timeout_cli_wins(self):
        self.assertEqual(
            ls.resolve_tmux_timeout(60.0, env={ls.TMUX_TIMEOUT_ENV: "5"}), 60.0)

    def test_resolve_tmux_timeout_env(self):
        self.assertEqual(
            ls.resolve_tmux_timeout(None, env={ls.TMUX_TIMEOUT_ENV: "45"}), 45.0)

    def test_resolve_tmux_timeout_bad_env_falls_back(self):
        self.assertEqual(
            ls.resolve_tmux_timeout(None, env={ls.TMUX_TIMEOUT_ENV: "notanumber"}),
            ls.DEFAULT_TMUX_TIMEOUT)
        self.assertEqual(
            ls.resolve_tmux_timeout(None, env={ls.TMUX_TIMEOUT_ENV: "-3"}),
            ls.DEFAULT_TMUX_TIMEOUT)

    def test_harness_honors_timeout(self):
        h = ls.Harness(tmux_timeout=12.5)
        try:
            self.assertEqual(h.tmux_timeout, 12.5)
        finally:
            h.cleanup()

    def test_is_tmux_cc_tty(self):
        for good in (None, "", "None"):
            self.assertTrue(ls._is_tmux_cc_tty(good))
        for bad in ("/dev/ttys003", "/dev/ttys000"):
            self.assertFalse(ls._is_tmux_cc_tty(bad))

    def test_select_single_match(self):
        cands = [
            ("it2smoke-1234-abcdef-tmp99", None),   # our tmux session (no tty)
            ("some-other-window", "/dev/ttys003"),  # a normal terminal
        ]
        self.assertEqual(
            ls.select_tmux_session(cands, "it2smoke-1234-abcdef"),
            "it2smoke-1234-abcdef-tmp99")

    def test_select_no_match_returns_none(self):
        cands = [("unrelated", None), ("editor", "/dev/ttys004")]
        self.assertIsNone(ls.select_tmux_session(cands, "it2smoke-9999-zzzzzz"))

    def test_select_ignores_matching_name_with_real_tty(self):
        # Same name substring but a REAL tty -> not the integrated tmux session.
        cands = [("it2smoke-1-aa-x", "/dev/ttys005")]
        self.assertIsNone(ls.select_tmux_session(cands, "it2smoke-1-aa"))

    def test_select_case_insensitive(self):
        cands = [("IT2SMOKE-7-BEEF-run", None)]
        self.assertEqual(
            ls.select_tmux_session(cands, "it2smoke-7-beef"),
            "IT2SMOKE-7-BEEF-run")

    def test_select_multiple_matches_fails_loudly(self):
        cands = [
            ("it2smoke-1-aa-first", None),
            ("it2smoke-1-aa-second", ""),  # both look like tmux-CC sessions
        ]
        with self.assertRaises(ls.TmuxSessionMatchError) as ctx:
            ls.select_tmux_session(cands, "it2smoke-1-aa")
        msg = str(ctx.exception)
        self.assertIn("2 iTerm2 sessions matched", msg)
        self.assertIn("refusing to guess", msg)

    def test_select_empty_matcher_never_matches(self):
        cands = [("anything", None)]
        self.assertIsNone(ls.select_tmux_session(cands, ""))


class SummaryAndExitTests(unittest.TestCase):
    def _results(self, *pairs):
        return [ls.SurfaceResult(n, s) for n, s in pairs]

    def test_exit_zero_only_when_all_pass(self):
        self.assertEqual(ls.overall_exit_code(self._results(("ccstatus", ls.PASS))), 0)
        self.assertEqual(ls.overall_exit_code(
            self._results(("spawn", ls.PASS), ("ccstatus", ls.PASS))), 0)

    def test_skip_yields_nonzero(self):
        self.assertNotEqual(ls.overall_exit_code(
            self._results(("spawn", ls.SKIP), ("ccstatus", ls.PASS))), 0)

    def test_fail_yields_nonzero(self):
        self.assertNotEqual(ls.overall_exit_code(
            self._results(("spawn", ls.FAIL))), 0)

    def test_empty_is_nonzero(self):
        self.assertNotEqual(ls.overall_exit_code([]), 0)

    def test_json_summary_shape(self):
        pf = _preflight()
        results = self._results(("spawn", ls.SKIP), ("ccstatus", ls.PASS))
        summary = ls.build_json_summary(pf, results, 1)
        self.assertEqual(summary["ok"], False)
        self.assertEqual(summary["exit_code"], 1)
        self.assertIn("preflight", summary)
        self.assertEqual(len(summary["surfaces"]), 2)
        for s in summary["surfaces"]:
            self.assertIn("surface", s)
            self.assertIn("status", s)
            self.assertIn("evidence", s)
            self.assertIn("reason", s)


class SkipNeverFakesTests(unittest.TestCase):
    """When the preflight is not satisfied, the live surfaces must come back
    SKIP with a clear reason — never a fabricated PASS."""

    def test_live_surfaces_skip_without_api(self):
        pf = _preflight(iterm2_importable=False)
        h = ls.Harness()
        try:
            for name in ("spawn", "tmux", "mcp"):
                r = h.run_surface(name, pf)
                self.assertEqual(r.status, ls.SKIP, f"{name} should SKIP without a live API")
                self.assertIn("not faking", r.reason)
        finally:
            h.cleanup()

    def test_ccstatus_runs_without_api(self):
        # ccstatus needs no live API; it must actually run and PASS headless.
        pf = _preflight(iterm2_importable=False, api_reader=lambda: "0")
        h = ls.Harness()
        try:
            r = h.run_surface("ccstatus", pf)
        finally:
            h.cleanup()
        self.assertEqual(r.status, ls.PASS, r.reason)
        self.assertTrue(any("busy" in e for e in r.evidence))


class CcstatusBytesTests(unittest.TestCase):
    """The ccstatus surface really runs the emitter and asserts exact bytes."""

    def test_golden_matches_emitter(self):
        env = dict(os.environ)
        env["IT2AGENT_FORCE"] = "1"
        out = subprocess.run(
            [sys.executable, str(ls.EMIT_CLI), "ccstatus", "busy", "--detail", "x"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(out.stdout, ls.GOLDEN_CCSTATUS_BUSY)

    def test_golden_clear_matches_emitter(self):
        env = dict(os.environ)
        env["IT2AGENT_FORCE"] = "1"
        out = subprocess.run(
            [sys.executable, str(ls.EMIT_CLI), "ccstatus", "clear"],
            env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(out.stdout, ls.GOLDEN_CCSTATUS_CLEAR)


class CleanupTests(unittest.TestCase):
    def test_temp_repo_created_and_removed(self):
        h = ls.Harness()
        repo = h.temp_repo()
        self.assertTrue(os.path.isdir(os.path.join(repo, ".git")))
        self.assertTrue(os.path.isdir(h.workdir))
        h.cleanup()
        self.assertFalse(os.path.exists(repo))
        self.assertFalse(os.path.exists(h.workdir))

    def test_cleanup_is_idempotent(self):
        h = ls.Harness()
        h.temp_repo()
        first = h.cleanup()
        second = h.cleanup()  # must not raise or re-do work
        self.assertEqual(second, [])
        self.assertTrue(any("temp repo" in line for line in first))

    def test_worktree_create_and_remove(self):
        h = ls.Harness()
        repo = h.temp_repo()
        wt = ls.make_temp_worktree(repo, "w1")
        h._worktrees.append((repo, wt))
        self.assertTrue(os.path.isdir(wt))
        # The worktree is registered with git.
        listing = subprocess.run(["git", "-C", repo, "worktree", "list"],
                                  capture_output=True, text=True)
        self.assertIn(wt, listing.stdout)
        h.cleanup()
        self.assertFalse(os.path.exists(wt))

    def test_no_cleanup_leaves_resources(self):
        h = ls.Harness(no_cleanup=True)
        repo = h.temp_repo()
        log = h.cleanup()
        self.assertTrue(os.path.exists(repo))  # left in place
        self.assertTrue(any("cleanup skipped" in line for line in log))
        # Clean up manually so the test leaves nothing behind.
        import shutil
        shutil.rmtree(repo, ignore_errors=True)
        shutil.rmtree(h.workdir, ignore_errors=True)

    def test_agent_id_is_tracked_and_unique(self):
        h = ls.Harness()
        try:
            a = h.agent_id("spawn")
            b = h.agent_id("mcp")
            self.assertNotEqual(a, b)
            self.assertIn(a, h._spawned_agent_ids)
            self.assertIn(b, h._spawned_agent_ids)
        finally:
            h.cleanup()


class EndToEndHeadlessTests(unittest.TestCase):
    """Drive run() for the one surface that works headless, in both output
    modes, and confirm it exits 0 and never fakes the live surfaces."""

    def test_only_ccstatus_json_exit_zero(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ls.run(["--only", "ccstatus", "--json"])
        self.assertEqual(rc, 0)
        summary = json.loads(buf.getvalue())
        self.assertEqual(summary["exit_code"], 0)
        self.assertEqual(len(summary["surfaces"]), 1)
        self.assertEqual(summary["surfaces"][0]["surface"], "ccstatus")
        self.assertEqual(summary["surfaces"][0]["status"], "PASS")
        self.assertIn("cleanup", summary)

    def test_only_ccstatus_human(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ls.run(["--only", "ccstatus"])
        self.assertEqual(rc, 0)
        self.assertIn("[PASS] ccstatus", buf.getvalue())

    def test_bad_only_exits_2(self):
        import io
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ls.run(["--only", "nope"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)

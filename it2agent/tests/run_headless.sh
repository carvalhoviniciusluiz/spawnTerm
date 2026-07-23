#!/usr/bin/env bash
# run_headless.sh — run every HEADLESS it2agent test suite and fail on any red.
#
# WHAT THIS COVERS (the "pure" layer)
# -----------------------------------
# The it2agent tools split into a pure/gate layer (argument parsing, protocol,
# flags, dispatch, path math, serialization — no running iTerm2 required) and a
# LIVE layer that only works when driving a real iTerm2 3.7.dev with the Python
# API on. This runner exercises the PURE layer only: every `test_*.py`
# (unittest) and `test_*.sh` suite under `it2agent/**/tests/`, plus the one live
# surface that needs no API — `live_smoke.py --only ccstatus` (exact OSC 21337
# bytes). It is what hosted CI (.github/workflows/headless-tests.yml) runs on
# push/PR.
#
# WHAT THIS DELIBERATELY DOES NOT COVER (the LIVE layer)
# -----------------------------------------------------
# The full live gate — `python3 it2agent/tests/live_smoke.py --json` (spawn /
# tmux / mcp surfaces) and the `*_live.sh` suites (e.g.
# spawn/tests/test_spawn_delivery_live.sh) — needs a real iTerm2 3.7.dev + the
# Python API and a GUI session. Hosted GitHub runners have none of that, so
# those surfaces run ONLY on a self-hosted macOS runner or by hand on the Mac.
# This runner excludes them by the `*_live.py` / `*_live.sh` filename suffix.
#
# HERMETICITY NOTE (read before "fixing" a flaky emit/flags/inbox failure)
# ------------------------------------------------------------------------
# Several suites assert the fail-safe "feature-flag OFF/absent -> gated off"
# path, which only holds when the flags read as OFF by default. Two things can
# leak a dev machine's real state into the run:
#   1. A real `it2agent-flag` wrapper on PATH (from `it2agent install`, in
#      ~/.local/bin). Hosted CI never runs `it2agent install`, so PATH is clean
#      there. This runner does NOT install wrappers — keep it that way.
#   2. The operator's real ~/.config/it2agent/config.toml, which typically has
#      flags like `agent.inbox` turned ON — that makes the "defaults OFF" tests
#      (e.g. inbox/tests/test_inbox_flow.py) go red locally though they are
#      green on a fresh CI runner that has no such file.
# To stay faithful to CI on ANY machine, this runner isolates the flag config to
# an empty temp file via IT2AGENT_CONFIG (an empty config == every flag OFF,
# exactly like a clean CI host) and clears IT2AGENT_FORCE. Suites that need a
# flag ON set it themselves (their own IT2AGENT_CONFIG / IT2AGENT_FORCE /
# --no-gate), which overrides this default per subprocess.
#
# DISCOVERY
# ---------
# Suites are discovered dynamically (`find … -path '*/tests/test_*'`) so a new
# `test_*.py` / `test_*.sh` under any `it2agent/**/tests/` dir is picked up with
# no edit here. Python suites are self-contained (each does its own sys.path
# insert and either calls unittest.main() or its own main()), so each runs in
# its own `python3 <file>` process for isolation. iTerm2-dependent cases inside
# the pure suites are guarded with `skipUnless(HAVE_ITERM2)`, so they SKIP (not
# fail) when the `iterm2` package is absent, which it is on hosted CI.
#
# Exit status: 0 iff every suite passed; nonzero if any suite failed.

set -u

HERE="$(cd "$(dirname "$0")" && pwd)"      # it2agent/tests
ROOT="$(cd "$HERE/../.." && pwd)"          # repo root (contains it2agent/)

cd "$ROOT"

# Isolate the feature-flag config so flags read OFF by default, matching a fresh
# CI host (see HERMETICITY NOTE above). An empty file == all flags OFF.
ISOLATED_CFG="$(mktemp -t it2agent-headless-config.XXXXXX)"
export IT2AGENT_CONFIG="$ISOLATED_CFG"
unset IT2AGENT_FORCE 2>/dev/null || true
cleanup() { rm -f "$ISOLATED_CFG"; }
trap cleanup EXIT

pass=0
fail=0
failed_suites=""

bold() { printf '\033[1m%s\033[0m\n' "$1"; }

run_suite() {
	local label="$1"; shift
	printf '\n\033[1m::: %s\033[0m\n' "$label"
	if "$@"; then
		pass=$((pass + 1))
		printf '\033[32mOK\033[0m   %s\n' "$label"
	else
		fail=$((fail + 1))
		failed_suites="$failed_suites\n  - $label"
		printf '\033[31mFAIL\033[0m %s\n' "$label"
	fi
}

# --- Python unittest suites (self-contained; run each in its own process) -----
# Sorted for stable, reproducible order. Excludes *_live.py (LIVE layer).
while IFS= read -r t; do
	run_suite "py  $t" python3 "$t"
done < <(find it2agent -path '*/tests/test_*.py' | grep -v '_live\.py$' | sort)

# --- Shell suites -------------------------------------------------------------
# Excludes *_live.sh (LIVE layer, e.g. test_spawn_delivery_live.sh).
#
# IT2AGENT_SKIP_OSACOMPILE escape hatch: a couple of suites (spawn/tmux) include
# an AppleScript SYNTAX check that osacompiles `tell application "iTerm2"`.
# osacompile resolves that terminology via LaunchServices, so it only works on a
# host that has iTerm2 installed. On a hosted CI runner without iTerm2 that
# check cannot pass, so the workflow sets IT2AGENT_SKIP_OSACOMPILE=1 and this
# runner skips the suites that carry it (detected by an `osacompile` reference)
# rather than red the job on an environment limitation. Those suites still run
# in full on a dev Mac or self-hosted runner (flag unset) — same tier as the
# live surfaces. This is a coarse suite-level skip, not a way to fake the check.
skip_osacompile="${IT2AGENT_SKIP_OSACOMPILE:-}"
while IFS= read -r t; do
	if [ -n "$skip_osacompile" ] && grep -q 'osacompile' "$t"; then
		printf '\n\033[33mSKIP\033[0m sh  %s (osacompile iTerm2-terminology check; run on a Mac with iTerm2)\n' "$t"
		continue
	fi
	run_suite "sh  $t" bash "$t"
done < <(find it2agent -path '*/tests/test_*.sh' | grep -v '_live\.sh$' | sort)

# --- live_smoke.py ccstatus surface (headless-capable, needs no live API) -----
# The only live_smoke surface that runs without iTerm2: exact OSC 21337 bytes.
run_suite "smoke live_smoke.py --only ccstatus" \
	python3 it2agent/tests/live_smoke.py --only ccstatus

# --- Summary ------------------------------------------------------------------
echo
bold "================ headless summary ================"
printf 'suites passed: %d   failed: %d\n' "$pass" "$fail"
if [ "$fail" -ne 0 ]; then
	printf 'FAILED SUITES:%b\n' "$failed_suites"
	bold "=================================================="
	exit 1
fi
bold "=================================================="
exit 0

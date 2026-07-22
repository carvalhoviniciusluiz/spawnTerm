#!/usr/bin/env bash
# Behavior tests for spawnterm-janitor (Phase 3, #15).
#
# Covers:
#   1. GATE CONFIG (pure): explicit --config TOML parses the [gate] table in
#      file order; auto-detect finds npm scripts, Makefile targets, python tools;
#      a repo-local spawnterm-gate.sh becomes the whole gate; empty gate.
#   2. RESOLVE (pure): reuses the #13 allocator so branch/worktree match
#      `spawnterm-worktree plan`; base auto-detects to main.
#   3. GATE-OFF: check is a no-op that says "disabled" and exits 0 (fail-safe);
#      ON via the real flag helper lets it run.
#   4. check --dry-run: prints the exact `(cd <worktree> && <cmd>)` lines,
#      executes nothing.
#   5. AGGREGATION in a throwaway repo: all checks pass -> overall=ok, exit 0;
#      any check fails -> overall=blocked, exit 1, failing output surfaced to
#      stdout AND to a per-check log + summary.txt.
#   6. OWNERSHIP predicate: a diff within the agent's owned globs -> eligible;
#      a file outside them -> not eligible; empty diff -> not eligible.
#   7. exit codes / usage.
#
# All real-git work happens in a private tmpdir and is torn down at the end.
# Run: bash spawnterm/janitor/tests/test_janitor.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
JAN_DIR="$(dirname "$HERE")"
JN="$JAN_DIR/spawnterm-janitor"
WT="$JAN_DIR/../spawn/spawnterm-worktree"
FLAG="$JAN_DIR/../flags/spawnterm-flag"

pass=0
fail=0
green() { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
red() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

assert_contains() {
	case "$3" in
		*"$2"*) green "$1" ;;
		*)      red "$1 (missing: $2)" ;;
	esac
}
assert_not_contains() {
	case "$3" in
		*"$2"*) red "$1 (unexpectedly present: $2)" ;;
		*)      green "$1" ;;
	esac
}
assert_eq() {
	if [ "$2" = "$3" ]; then green "$1"; else red "$1 (want '$2', got '$3')"; fi
}
assert_exit() {
	local label="$1" want="$2"; shift 2
	"$@" >/dev/null 2>&1
	local got=$?
	if [ "$got" = "$want" ]; then green "$label (exit $got)"; else red "$label (want $want, got $got)"; fi
}
val() { printf '%s\n' "$2" | sed -n "s/^$1=//p" | head -1; }

echo "=== spawnterm-janitor behavior tests ==="
echo "tool: $JN"

# Throwaway git repo + worktree root so we exercise real git without touching
# the host repo. Canonicalize TMP (macOS /var -> /private/var).
TMP="$(cd "$(mktemp -d)" && pwd -P)"
REPO="$TMP/repo"
export SPAWNTERM_WORKTREE_ROOT="$TMP/wt"
mkdir -p "$REPO"
git -C "$REPO" init -q
git -C "$REPO" symbolic-ref HEAD refs/heads/main
git -C "$REPO" config user.email t@example.com
git -C "$REPO" config user.name test
printf 'hello\n' > "$REPO/README.md"
git -C "$REPO" add README.md
git -C "$REPO" commit -qm "init"

echo
echo "--- 1. gate config (pure) ---"

# 1a. explicit --config TOML parses the [gate] table, in file order.
CFG="$TMP/gate.toml"
{
	printf '# a project gate\n'
	printf '[gate]\n'
	printf 'lint = "echo linting"\n'
	printf 'typecheck = "echo typing"\n'
	printf 'test = "echo testing"\n'
	printf '[other]\n'
	printf 'ignored = "nope"\n'
} > "$CFG"
cfg_out="$(sh "$JN" config --repo "$REPO" --config "$CFG")"
assert_contains "config reports the explicit source"    "gate-source=config:$CFG" "$cfg_out"
assert_contains "config parses the lint command"        "check=lint cmd=echo linting" "$cfg_out"
assert_contains "config parses the typecheck command"   "check=typecheck cmd=echo typing" "$cfg_out"
assert_contains "config parses the test command"        "check=test cmd=echo testing" "$cfg_out"
assert_not_contains "config ignores keys outside [gate]" "ignored" "$cfg_out"
assert_eq "config counts three checks" "3" "$(val checks "$cfg_out")"

# 1b. auto-detect npm scripts from package.json.
NREPO="$TMP/node"; mkdir -p "$NREPO"; git -C "$NREPO" init -q
printf '{\n  "scripts": {\n    "lint": "eslint .",\n    "test": "jest"\n  }\n}\n' > "$NREPO/package.json"
node_out="$(sh "$JN" config --repo "$NREPO")"
assert_contains "auto-detect reports npm source"      "gate-source=auto:npm" "$node_out"
assert_contains "auto-detect maps lint to npm run lint" "check=lint cmd=npm run lint" "$node_out"
assert_contains "auto-detect maps test to npm test"   "check=test cmd=npm test" "$node_out"
assert_not_contains "auto-detect omits an absent typecheck script" "typecheck" "$node_out"

# 1c. auto-detect Makefile targets (no package.json present).
MREPO="$TMP/make"; mkdir -p "$MREPO"; git -C "$MREPO" init -q
printf 'lint:\n\ttrue\ntest:\n\ttrue\n' > "$MREPO/Makefile"
make_out="$(sh "$JN" config --repo "$MREPO")"
assert_contains "auto-detect reports make source"    "gate-source=auto:make" "$make_out"
assert_contains "auto-detect maps lint to make lint" "check=lint cmd=make lint" "$make_out"

# 1d. a repo-local spawnterm-gate.sh becomes the whole gate.
SREPO="$TMP/script"; mkdir -p "$SREPO"; git -C "$SREPO" init -q
printf '#!/bin/sh\nexit 0\n' > "$SREPO/spawnterm-gate.sh"; chmod +x "$SREPO/spawnterm-gate.sh"
script_out="$(sh "$JN" config --repo "$SREPO")"
assert_contains "repo-local script becomes the gate source" "gate-source=script:$SREPO/spawnterm-gate.sh" "$script_out"
assert_contains "repo-local script is the single 'gate' check" "check=gate cmd=$SREPO/spawnterm-gate.sh" "$script_out"

# 1e. empty gate (nothing configured / detected).
EREPO="$TMP/empty"; mkdir -p "$EREPO"; git -C "$EREPO" init -q
empty_out="$(sh "$JN" config --repo "$EREPO")"
assert_contains "empty repo reports gate-source=none" "gate-source=none" "$empty_out"
assert_eq "empty repo has zero checks" "0" "$(val checks "$empty_out")"

echo
echo "--- 2. resolve (pure): reuses #13 allocator; base auto-detects to main ---"
res="$(sh "$JN" resolve build15 --role worker --repo "$REPO")"
plan="$(sh "$WT" plan --repo "$REPO" --id build15 --role worker)"
assert_eq "resolved branch == #13 plan branch" "$(val branch "$plan")" "$(val branch "$res")"
assert_eq "resolved worktree == #13 plan worktree" "$(val worktree "$plan")" "$(val worktree "$res")"
assert_eq "base auto-detected to main" "main" "$(val base "$res")"

echo
echo "--- 3. gate: OFF => check no-op (exit 0) + says disabled; ON => runs ---"
export SPAWNTERM_CONFIG="$TMP/flags.toml"   # absent file => flag OFF
off_out="$(sh "$JN" check build15 --role worker --repo "$REPO" 2>&1)"
assert_contains "gate OFF: check announces disabled" "is disabled" "$off_out"
assert_exit "gate OFF: check exits 0 (fail-safe)" 0 sh "$JN" check build15 --role worker --repo "$REPO"
"$FLAG" enable spawnterm.janitor >/dev/null 2>&1
assert_contains "gate ON via config: flag listed on" "spawnterm.janitor              on" "$("$FLAG" list)"
unset SPAWNTERM_CONFIG

echo
echo "--- 4. check --dry-run: prints the exact (cd <worktree> && <cmd>) lines ---"
# Give this agent a worktree + a gate config so dry-run has something to print.
sh "$WT" create --repo "$REPO" --id dry15 --role worker --no-gate --no-probe >/dev/null 2>&1
dwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id dry15 --role worker)")"
dry="$(sh "$JN" check dry15 --role worker --repo "$REPO" --config "$CFG" --dry-run --no-gate)"
assert_contains "dry-run prints the worktree-scoped lint command" "would-run: (cd $dwt && echo linting)   [check: lint]" "$dry"
assert_contains "dry-run reports the gate source" "gate-source=config:$CFG" "$dry"
if ls "$dwt/.spawnterm-janitor/summary.txt" >/dev/null 2>&1; then
	red "dry-run wrote a summary (should not)"
else
	green "dry-run wrote nothing"
fi

echo
echo "--- 5. aggregation (real tiny gate in a throwaway worktree) ---"

# 5a. ALL PASS -> overall=ok, exit 0.
sh "$WT" create --repo "$REPO" --id ok15 --role worker --no-gate --no-probe >/dev/null 2>&1
okwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id ok15 --role worker)")"
PASSCFG="$TMP/pass.toml"
{ printf '[gate]\n'; printf 'lint = "true"\n'; printf 'test = "true"\n'; } > "$PASSCFG"
ok_out="$(sh "$JN" check ok15 --role worker --repo "$REPO" --config "$PASSCFG" --no-gate)"
assert_contains "all-pass reports lint pass"  "check=lint status=pass" "$ok_out"
assert_contains "all-pass reports test pass"  "check=test status=pass" "$ok_out"
assert_contains "all-pass overall=ok"         "overall=ok" "$ok_out"
assert_exit "all-pass check exits 0 (mergeable)" 0 \
	sh "$JN" check ok15 --role worker --repo "$REPO" --config "$PASSCFG" --no-gate

# 5b. A FAILING check -> overall=blocked, exit 1, failing output surfaced.
sh "$WT" create --repo "$REPO" --id bad15 --role worker --no-gate --no-probe >/dev/null 2>&1
badwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id bad15 --role worker)")"
FAILCFG="$TMP/fail.toml"
{
	printf '[gate]\n'
	printf 'lint = "true"\n'
	printf 'test = "echo BOOM-the-tests-failed >&2; false"\n'
} > "$FAILCFG"
bad_out="$(sh "$JN" check bad15 --role worker --repo "$REPO" --config "$FAILCFG" --no-gate 2>&1)"
assert_contains "failing gate marks the test check failed" "check=test status=fail" "$bad_out"
assert_contains "failing gate reports overall=blocked"     "overall=blocked" "$bad_out"
assert_contains "failing output is surfaced to stdout"     "BOOM-the-tests-failed" "$bad_out"
assert_contains "failing output is fenced for the reviewer" "----- FAILED: test" "$bad_out"
assert_exit "failing gate exits 1 (blocks merge)" 1 \
	sh "$JN" check bad15 --role worker --repo "$REPO" --config "$FAILCFG" --no-gate
# The machine-readable summary + per-check log are persisted for the review UI.
if grep -q "overall=blocked" "$badwt/.spawnterm-janitor/summary.txt" 2>/dev/null; then
	green "summary.txt persisted with overall=blocked"
else
	red "summary.txt missing or wrong"
fi
if grep -q "BOOM-the-tests-failed" "$badwt/.spawnterm-janitor/test.log" 2>/dev/null; then
	green "failing check output captured to test.log"
else
	red "per-check log missing the failing output"
fi

# 5c. empty gate -> overall=empty, exit 0 (nothing to verify, not a failure).
sh "$WT" create --repo "$REPO" --id empty15 --role worker --no-gate --no-probe >/dev/null 2>&1
EMPTYCFG="$TMP/emptygate.toml"; printf '[gate]\n' > "$EMPTYCFG"
empty_chk="$(sh "$JN" check empty15 --role worker --repo "$REPO" --config "$EMPTYCFG" --no-gate 2>&1)"
assert_contains "empty gate reports overall=empty" "overall=empty" "$empty_chk"
assert_exit "empty gate exits 0" 0 \
	sh "$JN" check empty15 --role worker --repo "$REPO" --config "$EMPTYCFG" --no-gate

echo
echo "--- 6. ownership predicate (optional auto-merge eligibility) ---"
MAP="$TMP/ownership.toml"
{
	printf '[ownership]\n'
	printf '"src/api/**" = "backend"\n'
	printf '"docs/**" = "docs-bot"\n'
} > "$MAP"

# 6a. diff entirely within owned globs -> eligible (exit 0). --files bypasses git.
own_ok="$(sh "$JN" owns backend --repo "$REPO" --map "$MAP" --files "src/api/users.js src/api/db/pool.js")"
assert_contains "owns lists the agent's globs" "owned-globs=src/api/**" "$own_ok"
assert_contains "diff within owned globs is eligible" "eligible=yes" "$own_ok"
assert_exit "eligible ownership exits 0" 0 \
	sh "$JN" owns backend --repo "$REPO" --map "$MAP" --files "src/api/users.js"

# 6b. a file outside the owned globs -> NOT eligible (exit 1), names the file.
own_bad="$(sh "$JN" owns backend --repo "$REPO" --map "$MAP" --files "src/api/users.js README.md" 2>&1)"
assert_contains "an unowned file blocks eligibility" "eligible=no" "$own_bad"
assert_contains "the unowned file is named" "unowned=README.md" "$own_bad"
assert_exit "ineligible ownership exits 1" 1 \
	sh "$JN" owns backend --repo "$REPO" --map "$MAP" --files "README.md"

# 6c. empty diff -> not eligible (nothing to merge).
assert_exit "empty diff is not eligible (exit 1)" 1 \
	sh "$JN" owns backend --repo "$REPO" --map "$MAP" --files ""

echo
echo "--- 7. exit codes / usage ---"
assert_exit "--help exits 0"                      0 sh "$JN" --help
assert_exit "missing command exits 2"             2 sh "$JN"
assert_exit "unknown command exits 2"             2 sh "$JN" bogus
assert_exit "config with a missing file exits 2"  2 sh "$JN" config --repo "$REPO" --config "$TMP/nope.toml"
assert_exit "check without a target exits 2"      2 sh "$JN" check --repo "$REPO" --no-gate
assert_exit "owns without an id exits 2"          2 sh "$JN" owns --repo "$REPO" --map "$MAP" --files "x"

rm -rf "$TMP"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]

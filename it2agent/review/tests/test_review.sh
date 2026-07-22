#!/usr/bin/env bash
# Behavior tests for it2agent-review (Phase 2, #14).
#
# Covers:
#   1. resolve (PURE): reuses the #13 allocator so the derived branch/worktree
#      match `it2agent-worktree plan`, and base auto-detects to main.
#   2. GATE-OFF: show/approve/request-changes are no-ops that say "disabled" and
#      exit 0 (fail-safe). Gate-ON via the real flag helper lets them run.
#   3. show --dry-run: builds the right `git diff <base>...<branch>` invocation
#      (--stat summary + full patch), executes nothing.
#   4. approve (real throwaway repo): merges a clean/mergeable branch; refuses a
#      DIRTY agent worktree; refuses a CONFLICTING merge. Exit codes checked.
#   5. request-changes: routes to the broker when a socket is reachable, falls
#      back to a worktree note file when not; the broker payload is well-formed.
#   6. exit codes / usage.
#
# All real-git work happens in a private tmpdir and is torn down at the end.
# Run: bash it2agent/review/tests/test_review.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REVIEW_DIR="$(dirname "$HERE")"
RV="$REVIEW_DIR/it2agent-review"
NOTIFY="$REVIEW_DIR/review_notify.py"
WT="$REVIEW_DIR/../spawn/it2agent-worktree"
FLAG="$REVIEW_DIR/../flags/it2agent-flag"

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

echo "=== it2agent-review behavior tests ==="
echo "tool: $RV"

# Throwaway git repo + worktree root so we exercise real git without touching
# the host repo. Canonicalize TMP (macOS /var -> /private/var).
TMP="$(cd "$(mktemp -d)" && pwd -P)"
REPO="$TMP/repo"
export IT2AGENT_WORKTREE_ROOT="$TMP/wt"
mkdir -p "$REPO"
git -C "$REPO" init -q
git -C "$REPO" symbolic-ref HEAD refs/heads/main
git -C "$REPO" config user.email t@example.com
git -C "$REPO" config user.name test
printf 'hello\n' > "$REPO/README.md"
git -C "$REPO" add README.md
git -C "$REPO" commit -qm "init"

echo
echo "--- 1. resolve (pure): reuses #13 allocator; base auto-detects to main ---"
res="$(sh "$RV" resolve build13 --role worker --repo "$REPO")"
plan="$(sh "$WT" plan --repo "$REPO" --id build13 --role worker)"
assert_eq "resolved branch == #13 plan branch" "$(val branch "$plan")" "$(val branch "$res")"
assert_eq "resolved worktree == #13 plan worktree" "$(val worktree "$plan")" "$(val worktree "$res")"
assert_eq "base auto-detected to main" "main" "$(val base "$res")"

echo
echo "--- 2. gate: OFF => actions no-op (exit 0) + say disabled; ON => run ---"
CFG="$TMP/config.toml"
export IT2AGENT_CONFIG="$CFG"     # absent file => flag OFF
off_out="$(sh "$RV" show build13 --role worker --repo "$REPO" 2>&1)"
assert_contains "gate OFF: show announces disabled" "is disabled" "$off_out"
assert_exit "gate OFF: show exits 0 (fail-safe)" 0 sh "$RV" show build13 --role worker --repo "$REPO"
assert_exit "gate OFF: approve exits 0" 0 sh "$RV" approve build13 --role worker --repo "$REPO"
assert_exit "gate OFF: request-changes exits 0" 0 sh "$RV" request-changes build13 "fix it" --role worker --repo "$REPO"
# Turn the flag ON via the real helper; show must now produce diff output.
"$FLAG" enable agent.review >/dev/null 2>&1
assert_contains "gate ON via config: flag listed on" "agent.review                   on" "$("$FLAG" list)"
unset IT2AGENT_CONFIG

echo
echo "--- 3. show --dry-run: builds git diff <base>...<branch> (--stat + patch) ---"
dry="$(sh "$RV" show build13 --role worker --repo "$REPO" --dry-run --no-gate)"
br="$(val branch "$plan")"
assert_contains "dry-run shows --stat summary invocation" "would-run: git -C $REPO --no-pager diff --stat main...$br" "$dry"
assert_contains "dry-run shows full-patch invocation for the same range" "diff main...$br" "$dry"
assert_contains "dry-run reports a renderer" "renderer=" "$dry"

echo
echo "--- 4. approve: merges clean; refuses dirty worktree; refuses conflict ---"

# 4a. CLEAN mergeable branch -> merges into main (exit 0), main gains the commit.
sh "$WT" create --repo "$REPO" --id clean1 --role worker --no-gate --no-probe >/dev/null 2>&1
cwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id clean1 --role worker)")"
cbr="$(val branch "$(sh "$WT" plan --repo "$REPO" --id clean1 --role worker)")"
printf 'feature\n' > "$cwt/feature.txt"
git -C "$cwt" add feature.txt
git -C "$cwt" commit -qm "add feature"
assert_exit "approve merges a clean/mergeable branch (exit 0)" 0 \
	sh "$RV" approve clean1 --role worker --repo "$REPO" --base main --no-gate
if git -C "$REPO" ls-tree main --name-only | grep -qx feature.txt; then
	green "main now contains the merged file"
else
	red "merged file missing from main"
fi
if git -C "$REPO" log --oneline main | grep -q "it2agent-review: merge $cbr into main"; then
	green "merge commit recorded (--no-ff default)"
else
	red "no merge commit found"
fi

# 4b. DIRTY agent worktree -> refuse (exit 1), merge nothing.
sh "$WT" create --repo "$REPO" --id dirty1 --role worker --no-gate --no-probe >/dev/null 2>&1
dwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id dirty1 --role worker)")"
printf 'work\n' > "$dwt/wip.txt"
git -C "$dwt" add wip.txt
git -C "$dwt" commit -qm "committed work"
printf 'scratch\n' > "$dwt/uncommitted.txt"   # now dirty (untracked)
refuse_dirty="$(sh "$RV" approve dirty1 --role worker --repo "$REPO" --base main --no-gate 2>&1)"
assert_contains "approve refuses a DIRTY agent worktree" "uncommitted/untracked" "$refuse_dirty"
assert_exit "approve on dirty worktree exits 1" 1 \
	sh "$RV" approve dirty1 --role worker --repo "$REPO" --base main --no-gate

# 4c. CONFLICTING branch -> refuse (exit 1). Both sides edit README.md.
sh "$WT" create --repo "$REPO" --id conf1 --role worker --no-gate --no-probe >/dev/null 2>&1
xwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id conf1 --role worker)")"
printf 'agent-version\n' > "$xwt/README.md"
git -C "$xwt" add README.md
git -C "$xwt" commit -qm "agent edits README"
# Diverge main on the same file/line.
printf 'main-version\n' > "$REPO/README.md"
git -C "$REPO" add README.md
git -C "$REPO" commit -qm "main edits README"
refuse_conf="$(sh "$RV" approve conf1 --role worker --repo "$REPO" --base main --no-gate 2>&1)"
assert_contains "approve refuses a CONFLICTING merge" "conflicts with main" "$refuse_conf"
assert_exit "approve on conflict exits 1" 1 \
	sh "$RV" approve conf1 --role worker --repo "$REPO" --base main --no-gate

echo
echo "--- 5. request-changes: broker when reachable, file fallback when not ---"

# 5a. broker payload is well-formed (pure request builder, no server needed).
payload="$(python3 "$NOTIFY" --to build13 --from lead --note 'tighten error handling' --dry-run)"
assert_contains "notify builds an op:send request"        '"op": "send"'  "$payload"
assert_contains "notify addresses the recipient agent"    '"to": "build13"' "$payload"
assert_contains "notify prefixes the review note in body" 'changes requested' "$payload"

# 5b. dry-run routes to the broker when a real socket is present.
BSOCK="$TMP/broker.sock"
python3 -c 'import socket,sys; socket.socket(socket.AF_UNIX).bind(sys.argv[1])' "$BSOCK"
export IT2AGENT_BROKER_SOCK="$BSOCK"
route_on="$(sh "$RV" request-changes build13 "please fix" --role worker --repo "$REPO" --dry-run --no-gate)"
assert_contains "dry-run picks broker route when socket reachable" "route=broker" "$route_on"
assert_contains "dry-run shows the broker send command" "would-run: python3" "$route_on"
unset IT2AGENT_BROKER_SOCK

# 5c. dry-run falls back to a note file when no broker socket exists.
export IT2AGENT_BROKER_SOCK="$TMP/nonexistent.sock"
route_off="$(sh "$RV" request-changes build13 "please fix" --role worker --repo "$REPO" --dry-run --no-gate)"
assert_contains "dry-run falls back to file when broker unreachable" "route=file-fallback" "$route_off"
unset IT2AGENT_BROKER_SOCK

# 5d. --no-broker forces the fallback and REALLY writes a note into the worktree.
sh "$WT" create --repo "$REPO" --id note1 --role worker --no-gate --no-probe >/dev/null 2>&1
nwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id note1 --role worker)")"
out_fb="$(sh "$RV" request-changes note1 "add tests for the parser" --role worker --repo "$REPO" --no-broker --no-gate 2>&1)"
assert_contains "no-broker fallback reports the note file path" ".it2agent-review/CHANGES-REQUESTED-" "$out_fb"
if ls "$nwt/.it2agent-review/"CHANGES-REQUESTED-*.md >/dev/null 2>&1; then
	green "fallback wrote a note file into the agent worktree"
	if grep -q "add tests for the parser" "$nwt/.it2agent-review/"CHANGES-REQUESTED-*.md; then
		green "note file contains the reviewer's message"
	else
		red "note file missing the message"
	fi
else
	red "fallback did not write a note file"
fi

echo
echo "--- 6. exit codes / usage ---"
assert_exit "--help exits 0"                     0 sh "$RV" --help
assert_exit "missing command exits 2"            2 sh "$RV"
assert_exit "unknown command exits 2"            2 sh "$RV" bogus
assert_exit "resolve without a target exits 2"   2 sh "$RV" resolve --repo "$REPO"
assert_exit "request-changes without a note exits 2" 2 sh "$RV" request-changes note1 --role worker --repo "$REPO" --no-gate

# pane --dry-run prints AppleScript, runs nothing.
pane_dry="$(sh "$RV" pane build13 --role worker --repo "$REPO" --dry-run --no-gate)"
assert_contains "pane --dry-run emits iTerm2 AppleScript" 'tell application "iTerm2"' "$pane_dry"
assert_contains "pane --dry-run runs this tool's show in the split" 'split vertically' "$pane_dry"

rm -rf "$TMP"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]

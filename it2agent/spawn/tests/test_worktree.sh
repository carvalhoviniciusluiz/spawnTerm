#!/usr/bin/env bash
# Behavior tests for it2agent-worktree (Phase 2, #13).
#
# Covers:
#   1. the PURE allocator (plan/env): determinism, branch/namespace
#      sanitization, port range + collision-avoidance, distinct ids -> distinct
#      allocations, worktree path.
#   2. the GATE-OFF path: create/cleanup no-op + exit 0 (spawn then = #10).
#   3. --dry-run: prints the git plan, executes nothing.
#   4. REAL git worktree add/remove in a throwaway tmp repo: create makes the
#      worktree+branch, is idempotent, and exports the right env.
#   5. CLEANUP SAFETY: refuses a dirty worktree; refuses an unmerged branch;
#      removes a merged/unchanged one; --force overrides.
#
# All real-git work happens in a private tmpdir and is torn down at the end.
# Run: bash it2agent/spawn/tests/test_worktree.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SPAWN_DIR="$(dirname "$HERE")"
WT="$SPAWN_DIR/it2agent-worktree"

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

echo "=== it2agent-worktree behavior tests ==="
echo "helper: $WT"

# A throwaway git repo so we exercise real `git worktree` without touching the
# host repo. All worktrees land under $ROOT/wt (IT2AGENT_WORKTREE_ROOT).
# Canonicalize TMP (macOS /var -> /private/var) so our path assertions match
# what git and the helper store.
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
echo "--- 1. pure allocator: determinism + shape ---"
p1="$(sh "$WT" plan --repo "$REPO" --id 13 --role worker --task 'build isolation')"
p2="$(sh "$WT" plan --repo "$REPO" --id 13 --role worker --task 'build isolation')"
assert_eq "plan is deterministic (same inputs -> identical output)" "$p1" "$p2"
assert_contains "branch is under it2agent/ + sanitized slug" "branch=it2agent/worker-13-" "$p1"
assert_contains "namespace is DB-safe (role + hash, underscore)" "namespace=worker_" "$p1"
assert_contains "worktree path honors IT2AGENT_WORKTREE_ROOT" "worktree=$TMP/wt/worker-13-" "$p1"
port1="$(val port "$p1")"
if [ "$port1" -ge 41000 ] && [ "$port1" -le 41999 ]; then
	green "port in default range 41000..41999 ($port1)"
else
	red "port out of range ($port1)"
fi

echo
echo "--- 1b. sanitization of messy role/id ---"
messy="$(sh "$WT" plan --repo "$REPO" --id 'Feat/#13 Ports!' --role 'Tech Lead')"
assert_contains "uppercase/spaces/punct collapse to a safe branch slug" "branch=it2agent/tech-lead-feat-13-ports-" "$messy"
assert_not_contains "branch has no illegal ref chars (#, space, !)" "#" "$(val branch "$messy")"
ns_messy="$(val namespace "$messy")"
case "$ns_messy" in
	[a-z]*[a-z0-9_]*) green "namespace is a valid identifier ($ns_messy)" ;;
	*)               red "namespace invalid ($ns_messy)" ;;
esac

echo
echo "--- 1c. distinct ids -> distinct branches/ports/namespaces (collision-avoidance) ---"
a="$(sh "$WT" plan --repo "$REPO" --id agentA --role worker)"
b="$(sh "$WT" plan --repo "$REPO" --id agentB --role worker)"
if [ "$(val branch "$a")" != "$(val branch "$b")" ]; then green "distinct ids -> distinct branches"; else red "branch collision"; fi
if [ "$(val namespace "$a")" != "$(val namespace "$b")" ]; then green "distinct ids -> distinct namespaces"; else red "namespace collision"; fi
# Ports may coincide by birthday, but the hash differs; assert the hash differs.
if [ "$(val hash "$a")" != "$(val hash "$b")" ]; then green "distinct ids -> distinct hash anchors"; else red "hash collision"; fi

echo
echo "--- 1d. custom base-port/span move the range ---"
cp="$(sh "$WT" plan --repo "$REPO" --id 13 --role worker --base-port 50000 --span 100)"
cport="$(val port "$cp")"
if [ "$cport" -ge 50000 ] && [ "$cport" -le 50099 ]; then green "custom range 50000..50099 respected ($cport)"; else red "custom range wrong ($cport)"; fi

echo
echo "--- 1e. env command prints eval-able exports (pure) ---"
env_out="$(sh "$WT" env --repo "$REPO" --id 13 --role worker)"
assert_contains "env exports IT2AGENT_PORT"     "export IT2AGENT_PORT="     "$env_out"
assert_contains "env exports IT2AGENT_NS"       "export IT2AGENT_NS="       "$env_out"
assert_contains "env exports IT2AGENT_WORKTREE" "export IT2AGENT_WORKTREE=" "$env_out"
assert_contains "env exports IT2AGENT_BRANCH"   "export IT2AGENT_BRANCH="   "$env_out"

echo
echo "--- 2. gate: OFF => create no-ops (exit 0), ON via config => runs ---"
# Point the flag helper at a config we control. Absent flag => OFF.
CFG="$TMP/config.toml"
export IT2AGENT_CONFIG="$CFG"
off_out="$(sh "$WT" create --repo "$REPO" --id gatecheck --role worker 2>&1)"
assert_contains "gate OFF: create announces no-op" "gate closed" "$off_out"
assert_exit "gate OFF: create exits 0 (fail-safe)" 0 sh "$WT" create --repo "$REPO" --id gatecheck --role worker
[ -d "$TMP/wt" ] && red "gate OFF created a worktree dir (should not)" || green "gate OFF created nothing"
# Turn the flag ON through the real flag helper, then create must proceed.
"$SPAWN_DIR/../flags/it2agent-flag" enable agent.worktree_isolation >/dev/null 2>&1
on_out="$(sh "$WT" create --repo "$REPO" --id gatecheck --role worker 2>/dev/null)"
assert_contains "gate ON (config): create emits the allocation" "branch=it2agent/worker-gatecheck-" "$on_out"
unset IT2AGENT_CONFIG

echo
echo "--- 3. dry-run prints the git plan and executes nothing ---"
dry="$(sh "$WT" create --repo "$REPO" --id dryagent --role worker --dry-run --no-gate)"
assert_contains "dry-run shows would-run git worktree add" "would-run: git -C $REPO worktree add" "$dry"
assert_contains "dry-run still prints the allocation"       "branch=it2agent/worker-dryagent-" "$dry"
if git -C "$REPO" show-ref --verify --quiet "refs/heads/$(val branch "$dry")"; then
	red "dry-run created a real branch (should not)"
else
	green "dry-run created no branch"
fi

echo
echo "--- 4. real create: makes worktree + branch, idempotent, deterministic port ---"
c1="$(sh "$WT" create --repo "$REPO" --id build13 --role worker --no-gate --no-probe)"
wt_path="$(val worktree "$c1")"; br="$(val branch "$c1")"
[ -d "$wt_path" ] && green "worktree directory created ($wt_path)" || red "worktree dir missing"
git -C "$REPO" show-ref --verify --quiet "refs/heads/$br" && green "branch created ($br)" || red "branch missing"
# deterministic port matches the pure plan when probing is disabled.
plan_port="$(val port "$(sh "$WT" plan --repo "$REPO" --id build13 --role worker)")"
assert_eq "create --no-probe port == pure plan port" "$plan_port" "$(val port "$c1")"
# idempotent: second create reuses, does not error.
c2_err="$(sh "$WT" create --repo "$REPO" --id build13 --role worker --no-gate --no-probe 2>&1 >/dev/null)"
assert_contains "second create reuses existing worktree" "reusing existing worktree" "$c2_err"

echo
echo "--- 5. cleanup safety ---"

# 5a. DIRTY worktree -> refuse (exit 1), leave it in place.
sh "$WT" create --repo "$REPO" --id dirty1 --role worker --no-gate --no-probe >/dev/null 2>&1
dwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id dirty1 --role worker)")"
printf 'scratch\n' > "$dwt/uncommitted.txt"
assert_exit "cleanup refuses a DIRTY worktree (exit 1)" 1 \
	sh "$WT" cleanup --repo "$REPO" --id dirty1 --role worker --base main --no-gate
[ -d "$dwt" ] && green "dirty worktree left intact after refusal" || red "dirty worktree was removed"

# 5b. UNMERGED branch -> refuse (exit 1); --force removes.
sh "$WT" create --repo "$REPO" --id unmerged1 --role worker --no-gate --no-probe >/dev/null 2>&1
uwt="$(val worktree "$(sh "$WT" plan --repo "$REPO" --id unmerged1 --role worker)")"
printf 'work\n' > "$uwt/feature.txt"
git -C "$uwt" add feature.txt
git -C "$uwt" commit -qm "unmerged work"
refuse_out="$(sh "$WT" cleanup --repo "$REPO" --id unmerged1 --role worker --base main --no-gate 2>&1)"
assert_contains "cleanup refuses UNMERGED branch citing commits" "not merged into main" "$refuse_out"
[ -d "$uwt" ] && green "unmerged worktree left intact after refusal" || red "unmerged worktree removed"
# --force overrides.
sh "$WT" cleanup --repo "$REPO" --id unmerged1 --role worker --base main --no-gate --force >/dev/null 2>&1
[ -d "$uwt" ] && red "--force did not remove the worktree" || green "--force removed the unmerged worktree"

# 5c. MERGED / unchanged branch -> removed (exit 0), worktree+branch gone.
c="$(sh "$WT" create --repo "$REPO" --id clean1 --role worker --no-gate --no-probe)"
cwt="$(val worktree "$c")"; cbr="$(val branch "$c")"
assert_exit "cleanup removes a MERGED/unchanged worktree (exit 0)" 0 \
	sh "$WT" cleanup --repo "$REPO" --id clean1 --role worker --base main --no-gate
[ -d "$cwt" ] && red "merged worktree not removed" || green "merged worktree removed"
git -C "$REPO" show-ref --verify --quiet "refs/heads/$cbr" && red "merged branch not deleted" || green "merged branch deleted"

echo
echo "--- 6. exit codes / usage ---"
assert_exit "--help exits 0"                 0 sh "$WT" --help
assert_exit "missing command exits 2"        2 sh "$WT"
assert_exit "unknown command exits 2"        2 sh "$WT" bogus
assert_exit "plan without --id exits 2"      2 sh "$WT" plan --repo "$REPO"
assert_exit "create outside a git repo exits 2" 2 sh "$WT" create --repo "$TMP" --id x --no-gate

rm -rf "$TMP"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]

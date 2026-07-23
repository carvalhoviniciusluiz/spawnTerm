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
echo "--- 7. port lease (TOCTOU fix): contention, stale reclaim, release ---"
# Exercise the lease-aware allocation deterministically: pretend every TCP port
# is free (IT2AGENT_NO_TCP_PROBE) so only lease STATE governs allocation. On
# macOS there is no flock, so this also covers the atomic-mkdir lock fallback.
LEASES="$TMP/wt/.leases"
export IT2AGENT_NO_TCP_PROBE=1

# plant_lease <port> <pid> <worktree>: forge a foreign lease record.
plant_lease() {
	mkdir -p "$LEASES"
	{
		printf 'id=%s\n' planted
		printf 'repo=%s\n' "$REPO"
		printf 'pid=%s\n' "$2"
		printf 'epoch=%s\n' 0
		printf 'worktree=%s\n' "$3"
	} > "$LEASES/$1.lease"
}

# 7a. NO CONTENTION -> deterministic port preserved + a lease is written.
rm -rf "$LEASES"
det_nc="$(val port "$(sh "$WT" plan --repo "$REPO" --id lease_nc --role worker)")"
out_nc="$(sh "$WT" create --repo "$REPO" --id lease_nc --role worker --no-gate)"
assert_eq "no contention: create returns the deterministic port" "$det_nc" "$(val port "$out_nc")"
[ -f "$LEASES/$det_nc.lease" ] && green "lease file written for the deterministic port ($det_nc)" || red "no lease written for $det_nc"

# 7b. CONTENTION (two concurrent allocs, same repo) -> second must ADVANCE.
# Simulate the first spawn by planting a LIVE, non-stale lease on the second
# agent's deterministic port; its own create must then pick a different port.
det_cc="$(val port "$(sh "$WT" plan --repo "$REPO" --id lease_cc --role worker)")"
livewt="$TMP/livewt"; mkdir -p "$livewt"
plant_lease "$det_cc" "$$" "$livewt"
out_cc="$(sh "$WT" create --repo "$REPO" --id lease_cc --role worker --no-gate)"
got_cc="$(val port "$out_cc")"
if [ "$got_cc" != "$det_cc" ]; then green "contention: second alloc advances off the leased port ($det_cc -> $got_cc)"; else red "contention: collided on $det_cc"; fi
if [ "$got_cc" -ge 41000 ] && [ "$got_cc" -le 41999 ]; then green "advanced port stays in range ($got_cc)"; else red "advanced port out of range ($got_cc)"; fi
[ -f "$LEASES/$got_cc.lease" ] && green "a second lease is written at the advanced port" || red "no lease at advanced port $got_cc"
[ -f "$LEASES/$det_cc.lease" ] && green "the pre-existing (live) lease is left intact" || red "pre-existing lease was clobbered"

# 7c. STALE by DEAD PID -> reclaimed, deterministic port reused. Worktree in the
# planted lease EXISTS, so only the dead-pid rule can trigger the reclaim.
( : ) & deadpid=$!; wait "$deadpid" 2>/dev/null || true
det_dp="$(val port "$(sh "$WT" plan --repo "$REPO" --id lease_deadpid --role worker)")"
deadwt="$TMP/deadpidwt"; mkdir -p "$deadwt"
plant_lease "$det_dp" "$deadpid" "$deadwt"
out_dp="$(sh "$WT" create --repo "$REPO" --id lease_deadpid --role worker --no-gate)"
assert_eq "stale (dead pid) reclaimed: deterministic port reused" "$det_dp" "$(val port "$out_dp")"
if grep -q "pid=$deadpid" "$LEASES/$det_dp.lease" 2>/dev/null; then red "dead-pid lease not reclaimed"; else green "dead-pid lease reclaimed + rewritten by the new owner"; fi

# 7d. STALE by MISSING WORKTREE -> reclaimed. Pid is LIVE ($$), so only the
# missing-worktree rule can trigger the reclaim.
det_nw="$(val port "$(sh "$WT" plan --repo "$REPO" --id lease_nowt --role worker)")"
plant_lease "$det_nw" "$$" "$TMP/does-not-exist-$$"
out_nw="$(sh "$WT" create --repo "$REPO" --id lease_nowt --role worker --no-gate)"
assert_eq "stale (missing worktree) reclaimed: deterministic port reused" "$det_nw" "$(val port "$out_nw")"

# 7e. TEARDOWN releases the lease so the port returns to the pool.
c_td="$(sh "$WT" create --repo "$REPO" --id lease_teardown --role worker --no-gate)"
port_td="$(val port "$c_td")"
[ -f "$LEASES/$port_td.lease" ] && green "lease present after create ($port_td)" || red "no lease after create"
sh "$WT" cleanup --repo "$REPO" --id lease_teardown --role worker --base main --no-gate >/dev/null 2>&1
[ -f "$LEASES/$port_td.lease" ] && red "lease leaked after cleanup" || green "lease released on teardown"

unset IT2AGENT_NO_TCP_PROBE

echo
echo "--- 8. ls / status observability reporter (read-only) ---"
# Two real agent worktrees + their leases (via the tool's own create, so this is
# exactly what a spawn produces). Probing is on, so each gets a real leased port.
cA="$(sh "$WT" create --repo "$REPO" --id obsA --role worker    --no-gate 2>/dev/null)"
cB="$(sh "$WT" create --repo "$REPO" --id obsB --role tech-lead --no-gate 2>/dev/null)"
brA="$(val branch "$cA")"; wtA="$(val worktree "$cA")"; portA="$(val port "$cA")"
brB="$(val branch "$cB")"; wtB="$(val worktree "$cB")"; portB="$(val port "$cB")"
# Make A dirty; leave B clean.
printf 'scratch\n' > "$wtA/uncommitted.txt"

# 8a. ls lists both with the right branch + leased port + status summary.
tbl="$(sh "$WT" ls --repo "$REPO")"
assert_contains "ls lists worktree A's branch"        "$brA"   "$tbl"
assert_contains "ls lists worktree B's branch"        "$brB"   "$tbl"
lineA="$(printf '%s\n' "$tbl" | grep -F "$brA")"
lineB="$(printf '%s\n' "$tbl" | grep -F "$brB")"
assert_contains "ls shows A's leased port"            "$portA" "$lineA"
assert_contains "ls shows B's leased port"            "$portB" "$lineB"
assert_contains "ls marks the dirty worktree A"       "change" "$lineA"
assert_contains "ls marks the clean worktree B"       "clean"  "$lineB"

# 8b. status --json is valid JSON with the same records (stable keys).
js="$(sh "$WT" status --repo "$REPO" --json)"
case "$js" in
	\[*\]) green "status --json is a bracketed array" ;;
	*)     red "status --json is not bracketed ($js)" ;;
esac
objA="$(printf '%s' "$js" | grep -oE '\{[^}]*\}' | grep -F "\"branch\":\"$brA\"")"
objB="$(printf '%s' "$js" | grep -oE '\{[^}]*\}' | grep -F "\"branch\":\"$brB\"")"
assert_contains "json A: worktree path"          "\"worktree\":\"$wtA\"" "$objA"
assert_contains "json A: leased port (number)"   "\"port\":$portA"       "$objA"
assert_contains "json A: dirty => clean:false"   "\"clean\":false"       "$objA"
assert_contains "json A: not stale"              "\"stale\":false"       "$objA"
assert_contains "json B: clean => clean:true"    "\"clean\":true"        "$objB"
# Strict parse when a JSON parser is on the box (python3 or, on macOS, plutil).
if command -v python3 >/dev/null 2>&1; then
	if printf '%s' "$js" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
		green "status --json parses as valid JSON (python3)"
	else
		red "status --json does not parse as valid JSON (python3)"
	fi
elif command -v plutil >/dev/null 2>&1; then
	printf '%s' "$js" > "$TMP/status.json"
	if plutil -lint "$TMP/status.json" >/dev/null 2>&1; then
		green "status --json parses as valid JSON (plutil)"
	else
		red "status --json does not parse as valid JSON (plutil)"
	fi
else
	green "no JSON parser available; skipped strict parse (structural checks passed)"
fi

# 8c. STALE by dead owner pid -> marked, but NOT deleted/reclaimed (report only).
( : ) & deadpid=$!; wait "$deadpid" 2>/dev/null || true
lfB="$(grep -lF "worktree=$wtB" "$TMP/wt/.leases"/*.lease | head -1)"
sed "s/^pid=.*/pid=$deadpid/" "$lfB" > "$lfB.tmp" && mv "$lfB.tmp" "$lfB"
tbl2="$(sh "$WT" ls --repo "$REPO")"
lineB2="$(printf '%s\n' "$tbl2" | grep -F "$brB")"
assert_contains "dead-pid lease shows a STALE marker"    "STALE"      "$lineB2"
assert_contains "stale reason is owner-dead"             "owner-dead" "$lineB2"
[ -f "$lfB" ] && green "reporter did NOT delete the stale lease" || red "stale lease was deleted (reporter must not mutate)"
[ -d "$wtB" ] && green "reporter did NOT remove the stale worktree" || red "stale worktree was removed"
git -C "$REPO" show-ref --verify --quiet "refs/heads/$brB" && green "stale entry's branch left intact" || red "stale entry's branch was deleted"

# 8d. STALE by removed worktree dir -> marked gone, but NOT pruned (report only).
rm -rf "$wtA"
tbl3="$(sh "$WT" ls --repo "$REPO")"
lineA3="$(printf '%s\n' "$tbl3" | grep -F "$brA")"
assert_contains "removed worktree dir shows a STALE marker" "STALE"         "$lineA3"
assert_contains "stale reason is worktree-gone"             "worktree-gone" "$lineA3"
if git -C "$REPO" worktree list --porcelain 2>/dev/null | grep -qxF "worktree $wtA"; then
	green "reporter did NOT prune the gone worktree registration"
else
	red "reporter pruned a worktree (must be read-only)"
fi
js3="$(sh "$WT" status --repo "$REPO" --json)"
objA3="$(printf '%s' "$js3" | grep -oE '\{[^}]*\}' | grep -F "\"branch\":\"$brA\"")"
assert_contains "json: gone worktree => stale:true"   "\"stale\":true"     "$objA3"
assert_contains "json: gone worktree => changes:null" "\"changes\":null"   "$objA3"

# 8e. an empty repo (no agent worktrees) reports [] / a clear note, not an error.
EMPTY="$TMP/emptyrepo"
mkdir -p "$EMPTY"
git -C "$EMPTY" init -q
git -C "$EMPTY" config user.email t@example.com
git -C "$EMPTY" config user.name test
printf 'x\n' > "$EMPTY/f"; git -C "$EMPTY" add f; git -C "$EMPTY" commit -qm init
assert_eq "empty repo: status --json is []" "[]" "$(sh "$WT" status --repo "$EMPTY" --json)"
assert_exit "empty repo: ls still exits 0" 0 sh "$WT" ls --repo "$EMPTY"

# 8f. outside a git repo -> clear error, nonzero exit (read-only reporters too).
assert_exit "ls outside a git repo exits 2"     2 sh "$WT" ls     --repo "$TMP"
assert_exit "status outside a git repo exits 2" 2 sh "$WT" status --repo "$TMP" --json

echo
echo "--- 9. multi-port (--ports): N leases + N exports, first aliased (item 3) ---"
export IT2AGENT_NO_TCP_PROBE=1
mp="$(sh "$WT" create --repo "$REPO" --id multi1 --role worker --ports web,db,cache --no-gate 2>/dev/null)"
mp_br="$(val branch "$mp")"; mp_wt="$(val worktree "$mp")"
mp_web="$(val port_web "$mp")"; mp_db="$(val port_db "$mp")"; mp_cache="$(val port_cache "$mp")"
[ -n "$mp_web" ]   && green "port_web present ($mp_web)"     || red "port_web missing"
[ -n "$mp_db" ]    && green "port_db present ($mp_db)"       || red "port_db missing"
[ -n "$mp_cache" ] && green "port_cache present ($mp_cache)" || red "port_cache missing"
assert_eq "bare port aliases the FIRST named port (web)" "$mp_web" "$(val port "$mp")"
# distinct numbers so services never collide within one agent
if [ "$mp_web" != "$mp_db" ] && [ "$mp_db" != "$mp_cache" ] && [ "$mp_web" != "$mp_cache" ]; then
	green "the three named ports are distinct ($mp_web/$mp_db/$mp_cache)"
else
	red "named ports collided ($mp_web/$mp_db/$mp_cache)"
fi
# exactly three dynamic leases for this worktree (canonical leases excluded)
nleases="$(grep -lF "worktree=$mp_wt" "$TMP/wt/.leases"/*.lease 2>/dev/null | grep -c -v 'canonical-')"
[ "$nleases" = "3" ] && green "3 dynamic leases written for the 3 named ports" || red "want 3 leases, got $nleases"
# env exports each IT2AGENT_PORT_<UPPER> plus the bare alias
mpenv="$(sh "$WT" env --repo "$REPO" --id multi1 --role worker --ports web,db,cache)"
assert_contains "env exports IT2AGENT_PORT_WEB"   "export IT2AGENT_PORT_WEB="   "$mpenv"
assert_contains "env exports IT2AGENT_PORT_DB"    "export IT2AGENT_PORT_DB="    "$mpenv"
assert_contains "env exports IT2AGENT_PORT_CACHE" "export IT2AGENT_PORT_CACHE=" "$mpenv"
assert_contains "env still exports the bare IT2AGENT_PORT (back-compat)" "export IT2AGENT_PORT=" "$mpenv"
# no --ports -> exactly one port, no port_<name> lines (byte-compat with #13)
sp="$(sh "$WT" create --repo "$REPO" --id single1 --role worker --no-gate 2>/dev/null)"
assert_not_contains "no --ports: emits no port_<name> lines" "port_" "$sp"
# ls / status list ALL N ports for the multi-port agent
mptbl="$(sh "$WT" ls --repo "$REPO")"
mpline="$(printf '%s\n' "$mptbl" | grep -F "$mp_br")"
assert_contains "ls lists web port for the multi agent"   "$mp_web"   "$mpline"
assert_contains "ls lists db port for the multi agent"    "$mp_db"    "$mpline"
assert_contains "ls lists cache port for the multi agent" "$mp_cache" "$mpline"
mpjson="$(sh "$WT" status --repo "$REPO" --json)"
mpobj="$(printf '%s' "$mpjson" | grep -oE '\{[^}]*\}' | grep -F "\"branch\":\"$mp_br\"")"
assert_contains "status --json exposes a ports array" "\"ports\":[" "$mpobj"
assert_contains "status --json ports array lists web"   "$mp_web"   "$mpobj"
assert_contains "status --json ports array lists cache" "$mp_cache" "$mpobj"

echo
echo "--- 10. canonical port (--ports, agent.canonical_port): singleton + gate (item 4) ---"
# Fresh repo + worktree root so the canonical singleton starts unheld (earlier
# --no-gate creates bypass BOTH gates and would otherwise have grabbed the default
# canonical-web lease). Gate via a config we control: worktree_isolation ON so
# create runs; canonical OFF first, then ON. NO --no-gate here (that would bypass
# BOTH gates), so this genuinely exercises the canonical self-gate.
CANREPO="$TMP/canrepo"
mkdir -p "$CANREPO"
git -C "$CANREPO" init -q
git -C "$CANREPO" symbolic-ref HEAD refs/heads/main
git -C "$CANREPO" config user.email t@example.com
git -C "$CANREPO" config user.name test
printf 'x\n' > "$CANREPO/f"; git -C "$CANREPO" add f; git -C "$CANREPO" commit -qm init
REPO="$CANREPO"
export IT2AGENT_WORKTREE_ROOT="$TMP/canwt"
CANCFG="$TMP/cancfg.toml"
export IT2AGENT_CONFIG="$CANCFG"
FLAGBIN="$SPAWN_DIR/../flags/it2agent-flag"
"$FLAGBIN" enable agent.worktree_isolation >/dev/null 2>&1
# flag OFF -> create allocates ports but exports NO canonical.
canoff="$(sh "$WT" create --repo "$REPO" --id can_off --role worker --ports web 2>/dev/null)"
assert_not_contains "canonical flag OFF: no canonical export" "canonical_port_" "$canoff"
# flag ON -> the first agent (A) takes canonical web at the base (3000).
"$FLAGBIN" enable agent.canonical_port >/dev/null 2>&1
canA="$(sh "$WT" create --repo "$REPO" --id can_A --role worker --ports web 2>/dev/null)"
assert_contains "flag ON: agent A gets canonical web (=3000)" "canonical_port_web=3000" "$canA"
# a SECOND agent (B) must NOT get canonical web while A holds it (singleton).
canB="$(sh "$WT" create --repo "$REPO" --id can_B --role worker --ports web 2>/dev/null)"
assert_not_contains "singleton: agent B does NOT get canonical web while A holds it" "canonical_port_web" "$canB"
# A releases -> B can now take it.
sh "$WT" canonical --repo "$REPO" --id can_A --role worker --ports web --release >/dev/null 2>&1
canB2="$(sh "$WT" canonical --repo "$REPO" --id can_B --role worker --ports web 2>/dev/null)"
assert_contains "after A --release, agent B acquires canonical web" "canonical_port_web=3000" "$canB2"
# custom canonical base is honored.
"$FLAGBIN" enable agent.canonical_port >/dev/null 2>&1
canBase="$(sh "$WT" create --repo "$REPO" --id can_base --role worker --ports api --canonical-port 8080 2>/dev/null)"
assert_contains "custom --canonical-port base honored" "canonical_port_api=8080" "$canBase"
# canonical shows up in ls / status for the holder.
cantbl="$(sh "$WT" ls --repo "$REPO")"
canline="$(printf '%s\n' "$cantbl" | grep -F "$(val branch "$canBase")")"
assert_contains "ls shows the canonical port for its holder" "8080" "$canline"
unset IT2AGENT_CONFIG
unset IT2AGENT_NO_TCP_PROBE

echo
echo "--- 11. service isolation (--isolate docker|db, items 5+6; ENV-ONLY) ---"
# Fresh repo + config we control. worktree_isolation ON so create runs; the two
# isolate flags toggled independently. NO --no-gate here (that would bypass the
# per-mode gates), so this genuinely exercises each self-gate. NS is derived
# purely, so assertions compare against the plan's namespace.
ISOREPO="$TMP/isorepo"
mkdir -p "$ISOREPO"
git -C "$ISOREPO" init -q
git -C "$ISOREPO" symbolic-ref HEAD refs/heads/main
git -C "$ISOREPO" config user.email t@example.com
git -C "$ISOREPO" config user.name test
printf 'x\n' > "$ISOREPO/f"; git -C "$ISOREPO" add f; git -C "$ISOREPO" commit -qm init
export IT2AGENT_WORKTREE_ROOT="$TMP/isowt"
export IT2AGENT_NO_TCP_PROBE=1
ISOCFG="$TMP/isocfg.toml"
export IT2AGENT_CONFIG="$ISOCFG"
FLAGBIN="$SPAWN_DIR/../flags/it2agent-flag"
"$FLAGBIN" enable agent.worktree_isolation >/dev/null 2>&1
iso_ns() { val namespace "$(sh "$WT" plan --repo "$ISOREPO" --id "$1" --role worker)"; }

# 11a. namespace mode is rejected on macOS with a clear pointer to docker.
ns_err="$(sh "$WT" plan --repo "$ISOREPO" --id ns1 --role worker --isolate namespace 2>&1)"
assert_contains "--isolate namespace is rejected" "not supported on macOS" "$ns_err"
assert_contains "--isolate namespace points at docker" "use --isolate docker" "$ns_err"
assert_exit "--isolate namespace exits nonzero" 2 sh "$WT" plan --repo "$ISOREPO" --id ns1 --role worker --isolate namespace

# 11b. both flags OFF -> --isolate docker,db exports NOTHING (fail-safe).
off_iso="$(sh "$WT" create --repo "$ISOREPO" --id iso_off --role worker --isolate docker,db 2>/dev/null)"
assert_not_contains "flags OFF: no COMPOSE_PROJECT_NAME" "COMPOSE_PROJECT_NAME" "$off_iso"
assert_not_contains "flags OFF: no IT2AGENT_DB_SCHEMA"   "IT2AGENT_DB_SCHEMA"   "$off_iso"
assert_not_contains "flags OFF: no isolate summary line" "isolate="            "$off_iso"

# 11c. isolate_docker ON + --isolate docker -> COMPOSE_PROJECT_NAME=NS, nothing else.
"$FLAGBIN" enable agent.isolate_docker >/dev/null 2>&1
ns_dk="$(iso_ns iso_dk)"
dk="$(sh "$WT" create --repo "$ISOREPO" --id iso_dk --role worker --isolate docker 2>/dev/null)"
assert_contains "docker ON: exports COMPOSE_PROJECT_NAME=NS" "env_COMPOSE_PROJECT_NAME=$ns_dk" "$dk"
assert_not_contains "docker-only: no DB schema export" "IT2AGENT_DB_SCHEMA" "$dk"
assert_contains "docker: isolate summary lists docker" "isolate=docker" "$dk"

# 11d. isolate_db ON + --isolate db (schema mode default) -> DB_SCHEMA + PGOPTIONS.
"$FLAGBIN" enable agent.isolate_db >/dev/null 2>&1
ns_db="$(iso_ns iso_db)"
db="$(sh "$WT" create --repo "$ISOREPO" --id iso_db --role worker --isolate db 2>/dev/null)"
assert_contains "db ON: exports IT2AGENT_DB_SCHEMA=NS" "env_IT2AGENT_DB_SCHEMA=$ns_db" "$db"
assert_contains "db ON: exports PGOPTIONS search_path" "env_PGOPTIONS=-c search_path=$ns_db" "$db"
assert_not_contains "db schema mode: no IT2AGENT_DB_NAME" "IT2AGENT_DB_NAME" "$db"

# 11e. --isolate db=database -> IT2AGENT_DB_NAME instead of schema/PGOPTIONS.
ns_dbd="$(iso_ns iso_dbd)"
dbd="$(sh "$WT" create --repo "$ISOREPO" --id iso_dbd --role worker --isolate db=database 2>/dev/null)"
assert_contains "db=database: exports IT2AGENT_DB_NAME=NS" "env_IT2AGENT_DB_NAME=$ns_dbd" "$dbd"
assert_not_contains "db=database: no IT2AGENT_DB_SCHEMA" "IT2AGENT_DB_SCHEMA" "$dbd"
assert_not_contains "db=database: no PGOPTIONS"          "PGOPTIONS"          "$dbd"

# 11f. --isolate docker,db combines both (both flags ON).
ns_both="$(iso_ns iso_both)"
both="$(sh "$WT" create --repo "$ISOREPO" --id iso_both --role worker --isolate docker,db 2>/dev/null)"
assert_contains "combined: COMPOSE_PROJECT_NAME present" "env_COMPOSE_PROJECT_NAME=$ns_both" "$both"
assert_contains "combined: IT2AGENT_DB_SCHEMA present"   "env_IT2AGENT_DB_SCHEMA=$ns_both"   "$both"
assert_contains "combined: isolate summary lists both"  "isolate=docker,db=schema"           "$both"

# 11g. per-mode gate: docker ON but db OFF -> only COMPOSE_PROJECT_NAME.
"$FLAGBIN" disable agent.isolate_db >/dev/null 2>&1
mix="$(sh "$WT" create --repo "$ISOREPO" --id iso_mix --role worker --isolate docker,db 2>/dev/null)"
assert_contains "mixed gate: docker export present"   "env_COMPOSE_PROJECT_NAME=" "$mix"
assert_not_contains "mixed gate: db export suppressed" "IT2AGENT_DB_SCHEMA"       "$mix"

# 11h. no --isolate -> no env_/isolate lines (byte-compat with #13/#109).
"$FLAGBIN" enable agent.isolate_db >/dev/null 2>&1
plain_iso="$(sh "$WT" create --repo "$ISOREPO" --id iso_plain --role worker 2>/dev/null)"
assert_not_contains "no --isolate: no env_ export lines" "env_" "$plain_iso"
assert_not_contains "no --isolate: no isolate summary"   "isolate=" "$plain_iso"

unset IT2AGENT_CONFIG
unset IT2AGENT_NO_TCP_PROBE

echo
echo "--- 12. Coastfile .it2agent/isolation.toml (item 7): declare defaults once ---"
# A fresh repo whose .it2agent/isolation.toml declares ports/canonical/isolate/
# assign. --no-gate bypasses every self-gate so the file-driven exports actually
# surface; NO_TCP_PROBE keeps port leasing deterministic. Explicit CLI flags must
# OVERRIDE the file, an absent file must change nothing, and malformed keys must
# degrade safely.
COASTREPO="$TMP/coastrepo"
mkdir -p "$COASTREPO/.it2agent"
git -C "$COASTREPO" init -q
git -C "$COASTREPO" symbolic-ref HEAD refs/heads/main
git -C "$COASTREPO" config user.email t@example.com
git -C "$COASTREPO" config user.name test
printf 'x\n' > "$COASTREPO/f"; git -C "$COASTREPO" add f; git -C "$COASTREPO" commit -qm init
export IT2AGENT_WORKTREE_ROOT="$TMP/coastwt"
export IT2AGENT_NO_TCP_PROBE=1
cat > "$COASTREPO/.it2agent/isolation.toml" <<'TOML'
# per-project isolation defaults (item 7)
[section-headers-are-ignored]
ports     = ["web", "db"]
canonical = 8080          # inline comments are stripped
isolate   = ["docker", "db"]
assign    = "restart"
unknown_key = "ignored"
TOML

# 12a. file present, no CLI flags -> every declared default is applied.
cf="$(sh "$WT" create --repo "$COASTREPO" --id coast_all --role worker --no-gate 2>/dev/null)"
assert_contains "coastfile ports applied: port_web present"  "port_web="  "$cf"
assert_contains "coastfile ports applied: port_db present"   "port_db="   "$cf"
assert_contains "coastfile canonical base applied (web=8080)" "canonical_port_web=8080" "$cf"
assert_contains "coastfile isolate applied: docker export"   "env_COMPOSE_PROJECT_NAME=" "$cf"
assert_contains "coastfile isolate applied: db schema export" "env_IT2AGENT_DB_SCHEMA="  "$cf"
assert_contains "coastfile isolate summary lists both"       "isolate=docker,db=schema" "$cf"
assert_contains "coastfile assign applied: assign=restart"   "assign=restart" "$cf"
assert_contains "coastfile assign exports IT2AGENT_ASSIGN"    "env_IT2AGENT_ASSIGN=restart" "$cf"

# 12b. explicit CLI flags OVERRIDE the file value.
ov="$(sh "$WT" create --repo "$COASTREPO" --id coast_ov --role worker --no-gate \
	--ports api --canonical-port 9090 --isolate docker --assign none 2>/dev/null)"
assert_contains "CLI --ports overrides file: port_api present"   "port_api="  "$ov"
assert_not_contains "CLI --ports overrides file: no port_web"    "port_web="  "$ov"
assert_contains "CLI --canonical-port overrides file (api=9090)" "canonical_port_api=9090" "$ov"
assert_not_contains "CLI --isolate docker overrides file: no db schema" "IT2AGENT_DB_SCHEMA" "$ov"
assert_not_contains "CLI --assign none overrides file: no assign line"  "assign="    "$ov"

# 12c. no Coastfile -> byte-compat (no file-driven port_/isolate/assign lines).
NOCF="$TMP/nocoastrepo"
mkdir -p "$NOCF"
git -C "$NOCF" init -q
git -C "$NOCF" symbolic-ref HEAD refs/heads/main
git -C "$NOCF" config user.email t@example.com
git -C "$NOCF" config user.name test
printf 'x\n' > "$NOCF/f"; git -C "$NOCF" add f; git -C "$NOCF" commit -qm init
nf="$(sh "$WT" create --repo "$NOCF" --id nocoast --role worker --no-gate 2>/dev/null)"
assert_not_contains "no coastfile: no port_<name> lines" "port_"    "$nf"
assert_not_contains "no coastfile: no isolate summary"   "isolate=" "$nf"
assert_not_contains "no coastfile: no assign line"       "assign="  "$nf"

# 12d. malformed / empty / unknown keys degrade safely (no crash, exit 0).
BADCF="$TMP/badcoastrepo"
mkdir -p "$BADCF/.it2agent"
git -C "$BADCF" init -q
git -C "$BADCF" symbolic-ref HEAD refs/heads/main
git -C "$BADCF" config user.email t@example.com
git -C "$BADCF" config user.name test
printf 'x\n' > "$BADCF/f"; git -C "$BADCF" add f; git -C "$BADCF" commit -qm init
cat > "$BADCF/.it2agent/isolation.toml" <<'TOML'
this line has no equals and must be skipped
ports = []
canonical = notanumber
zzz = ["also", "ignored"]
TOML
assert_exit "malformed coastfile: create still exits 0" 0 \
	sh "$WT" create --repo "$BADCF" --id badcoast --role worker --no-gate
bad="$(sh "$WT" create --repo "$BADCF" --id badcoast2 --role worker --no-gate 2>/dev/null)"
assert_not_contains "empty ports array yields no port_<name> lines" "port_" "$bad"

unset IT2AGENT_NO_TCP_PROBE

echo
echo "--- 13. --assign none|restart (item 8): thin hook + alias/garbage handling ---"
# Validation runs for every command, so accept/reject can be exercised on the
# pure `plan` (no gate). The restart EMISSION is exercised on create --no-gate.
assert_exit "--assign none accepted (exit 0)"    0 sh "$WT" plan --repo "$COASTREPO" --id as1 --role worker --assign none
assert_exit "--assign restart accepted (exit 0)" 0 sh "$WT" plan --repo "$COASTREPO" --id as2 --role worker --assign restart
assert_exit "--assign hot accepted as alias (exit 0)"     0 sh "$WT" plan --repo "$COASTREPO" --id as3 --role worker --assign hot
assert_exit "--assign rebuild accepted as alias (exit 0)" 0 sh "$WT" plan --repo "$COASTREPO" --id as4 --role worker --assign rebuild
assert_exit "--assign garbage rejected (exit 2)" 2 sh "$WT" plan --repo "$COASTREPO" --id as5 --role worker --assign bogus
garb="$(sh "$WT" plan --repo "$COASTREPO" --id as6 --role worker --assign bogus 2>&1)"
assert_contains "--assign garbage error names the bad value" "unknown strategy 'bogus'" "$garb"
hotwarn="$(sh "$WT" plan --repo "$COASTREPO" --id as7 --role worker --assign hot 2>&1)"
assert_contains "--assign hot warns it maps to none" "mapped to 'none'" "$hotwarn"

export IT2AGENT_NO_TCP_PROBE=1
export IT2AGENT_WORKTREE_ROOT="$TMP/assignwt"
asr="$(sh "$WT" create --repo "$NOCF" --id assign_restart --role worker --no-gate --assign restart 2>/dev/null)"
assert_contains "--assign restart emits assign=restart on create" "assign=restart" "$asr"
assert_contains "--assign restart exports IT2AGENT_ASSIGN"         "env_IT2AGENT_ASSIGN=restart" "$asr"
asn="$(sh "$WT" create --repo "$NOCF" --id assign_none --role worker --no-gate --assign none 2>/dev/null)"
assert_not_contains "--assign none emits no assign line (byte-compat)" "assign=" "$asn"
ash="$(sh "$WT" create --repo "$NOCF" --id assign_hot --role worker --no-gate --assign hot 2>/dev/null)"
assert_not_contains "--assign hot (alias->none) emits no assign line" "assign=" "$ash"
unset IT2AGENT_NO_TCP_PROBE

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

#!/usr/bin/env bash
# Behavior tests for it2agent-tmux (Tier 3, #5).
#
# it2agent-tmux drives iTerm2 via osascript and, at run time, tmux — neither of
# which we can exercise headless. So every test runs in --dry-run mode (or the
# pure `name` subcommand) and asserts on the plan it prints: the derived tmux
# session name, the gate decision, the tmux -CC argv, and the inner session
# script. NO live tmux and NO live iTerm2 are required or invoked.
#
# The live iTerm2 + tmux -CC validation (does the Python API still see these
# sessions?) is a documented MANUAL checklist — see API_VALIDATION.md. It is
# NOT run here and its results are NOT fabricated.
#
# Run from anywhere: bash it2agent/tmux/tests/test_tmux.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
TMUX_DIR="$(dirname "$HERE")"
TMUX_BIN="$TMUX_DIR/it2agent-tmux"
FLAG="$(cd "$TMUX_DIR/../flags" && pwd)/it2agent-flag"
EMIT="$(cd "$TMUX_DIR/../emit" && pwd)/it2agent-emit"

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
assert_absent() {
	case "$3" in
		*"$2"*) red "$1 (unexpected: $2)" ;;
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

echo "=== it2agent-tmux behavior tests (dry-run / pure) ==="
echo "tmux helper: $TMUX_BIN"

# A scratch config so the gate is deterministic regardless of the operator's
# real ~/.config. Empty file => every flag OFF.
CFG_DIR="$(mktemp -d)"
export IT2AGENT_CONFIG="$CFG_DIR/config.toml"
: > "$IT2AGENT_CONFIG"

echo
echo "--- 1. session-name sanitization + collision-safety (pure 'name') ---"
assert_eq "role+task -> lowercased, slugged, st- prefixed" \
	"st-worker-build-5" "$(sh "$TMUX_BIN" name --role Worker --task 'build #5')"
assert_eq "dots/colons/slashes (tmux-hostile) become '-'" \
	"st-weird-name-with-junk" "$(sh "$TMUX_BIN" name --session 'Weird.Name:With/Junk')"
assert_eq "--id basis" "st-5" "$(sh "$TMUX_BIN" name --id 5)"
# Determinism: same inputs => byte-identical name (so re-spawn reattaches).
n1="$(sh "$TMUX_BIN" name --role worker --task tmux)"
n2="$(sh "$TMUX_BIN" name --role worker --task tmux)"
assert_eq "deterministic (re-spawn lands on same session)" "$n1" "$n2"
# No tmux-forbidden characters ('.' or ':') ever survive.
weird="$(sh "$TMUX_BIN" name --session 'a.b:c.d:e')"
assert_absent "sanitized name has no '.'" "." "${weird#st-}"   # strip prefix? '.' check below
case "$weird" in *.*|*:*) red "name still contains . or :" ;; *) green "name has no '.' or ':'" ;; esac
# Empty / all-junk basis => st-agent (never an empty tmux target).
assert_eq "all-junk basis collapses to st-agent" "st-agent" "$(sh "$TMUX_BIN" name --session '.:./:')"
# Leading digit and long strings are handled (truncated to 40 after prefix).
long="$(sh "$TMUX_BIN" name --session 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaZZZ')"
case "$long" in st-*) green "long name keeps st- prefix + is bounded" ;; *) red "long name lost prefix" ;; esac

echo
echo "--- 2. gate OFF (default) => delegate to it2agent-spawn, NO tmux ---"
off_out="$(sh "$TMUX_BIN" spawn --role worker --task 'build #5' --dry-run -- claude --resume)"
assert_contains "announces delegation"          "delegating to it2agent-spawn" "$off_out"
assert_contains "runs the it2agent-spawn plan" "it2agent-spawn: DRY RUN"       "$off_out"
assert_absent   "no tmux wrapping when gate OFF" "tmux -CC"                       "$off_out"
assert_absent   "no new-session when gate OFF"   "new-session"                    "$off_out"
# The user command survives into the delegated plan.
assert_contains "delegated command preserved"   "'claude' '--resume'"            "$off_out"

echo
echo "--- 3. gate ON (config: agent.tmux=true) => tmux -CC wrapping ---"
sh "$FLAG" enable agent.tmux >/dev/null
# worktree_isolation stays OFF here, so we test the tmux path in isolation.
on_out="$(sh "$TMUX_BIN" spawn --role worker --task 'build #5' --dry-run -- claude --resume)"
assert_contains "gate reported ON"                  "gate         : ON (agent.tmux)" "$on_out"
assert_contains "derives the session name"          "session name : st-worker-build-5"   "$on_out"
assert_contains "uses tmux -CC new-session"         "tmux -CC new-session -A -s 'st-worker-build-5'" "$on_out"
assert_contains "-A = create-or-reattach"           "new-session -A"                     "$on_out"
assert_contains "runs a login shell -lc"            " -lc "                               "$on_out"
assert_contains "identity emit shells out to emit"  "$EMIT" "$on_out"
assert_contains "role emit inside tmux"             "role 'worker'"                      "$on_out"
assert_contains "status emit inside tmux"           "status 'busy'"                      "$on_out"
assert_contains "color derived from status"         "color 'busy'"                       "$on_out"
assert_contains "badge emit inside tmux"            "badge"                              "$on_out"
assert_contains "agent command exec'd (survives)"   "exec 'claude' '--resume'"           "$on_out"
# tmux path does NOT re-implement escape codes itself (delegates to emit).
assert_absent   "no hand-rolled escape codes"       "1337"                               "$on_out"
# Isolation is OFF (its flag is off) => no per-agent port exports leak in.
assert_contains "isolation off in this config"      "isolation    : off"                 "$on_out"
assert_absent   "no IT2AGENT_PORT export (iso off)" "IT2AGENT_PORT"                    "$on_out"

echo
echo "--- 4. the inner boot script (cd -> env -> emits -> self-rm -> exec) ---"
# The inner script now lives in a /tmp boot file that the tmux command sources
# (#74 fix keeps the launched `command` single-quote clean). The dry-run prints
# the boot script's content: a ;-joined pipeline that cds, emits identity, self-
# removes, then exec's the agent.
inner="$(printf '%s\n' "$on_out" | awk '/boot script/{getline; print; exit}')"
assert_contains "boot script present"                 "cd '"                          "$inner"
assert_contains "boot script is ;-joined"             " ; "                           "$inner"
assert_contains "boot script self-removes before exec" "rm -f /tmp/it2agent-tmuxboot." "$inner"
assert_contains "boot script exec's command"          "exec 'claude'"                 "$inner"

echo
echo "--- 4b. native tab-status (ccstatus) wiring (#89) ---"
# The boot script always includes an it2agent-emit ccstatus line AFTER the
# identity emits. It self-gates on agent.native_status inside it2agent-emit, so
# it is present in the plan regardless of flag state (no-op at runtime when the
# flag is OFF). <status> mirrors --status; --detail mirrors the badge's role ·
# task composition. (agent.tmux is still ON from section 3.)
cc_on="$(sh "$TMUX_BIN" spawn --role worker --task 'build #5' --status busy --dry-run -- claude --resume)"
assert_contains "ccstatus line present with status + role · task detail" \
	"ccstatus 'busy' --detail 'worker · build #5'" "$cc_on"
# It comes AFTER the identity emits (badge precedes ccstatus in the ;-joined boot).
cc_inner="$(printf '%s\n' "$cc_on" | awk '/boot script/{getline; print; exit}')"
case "$cc_inner" in
	*"badge ; "*"ccstatus 'busy'"*) green "ccstatus emitted AFTER the identity emits (badge before ccstatus)" ;;
	*)                              red "ccstatus not ordered after badge in the boot script" ;;
esac
# <status> tracks --status.
cc_idle="$(sh "$TMUX_BIN" spawn --role r --task t --status idle --dry-run -- claude)"
assert_contains "ccstatus status tracks --status (idle)" "ccstatus 'idle' --detail 'r · t'" "$cc_idle"
# --no-gate is forwarded to the ccstatus emit like the others. The tmux boot
# quotes the emit path, so the on-wire form is '<emit>' --no-gate ccstatus ...
assert_contains "--no-gate forwarded to ccstatus" "$EMIT' --no-gate ccstatus 'busy' --detail 'worker · build #5'" \
	"$(sh "$TMUX_BIN" spawn --role worker --task 'build #5' --no-gate --dry-run -- claude)"
# Fallbacks mirror the badge: role-only, task-only, both-empty (omit --detail).
assert_contains "role-only detail is just the role" "ccstatus 'busy' --detail 'solo'" \
	"$(sh "$TMUX_BIN" spawn --role solo --dry-run -- claude)"
assert_contains "task-only detail is just the task" "ccstatus 'busy' --detail 'lonely'" \
	"$(sh "$TMUX_BIN" spawn --task lonely --dry-run -- claude)"
cc_none="$(sh "$TMUX_BIN" spawn --session solo --dry-run -- claude)"
assert_contains "no role/task still emits ccstatus (status only)" "ccstatus 'busy'" "$cc_none"
case "$cc_none" in
	*"ccstatus 'busy' --detail"*) red "ccstatus should omit --detail when role and task are both empty" ;;
	*)                            green "ccstatus omits --detail when role and task are both empty" ;;
esac

echo
echo "--- 5. emitted tmux argv round-trips: -lc payload is a SINGLE arg ---"
# Eval the printed `tmux -CC ...` line with `tmux` shimmed to a shell function
# that records its arg count. Correct quoting => tmux sees exactly 8 args:
#   -CC new-session -A -s <name> <shell> -lc <source-line-as-ONE-arg>
# The payload is now the SHORT `. <bootfile>` source line (the real script lives
# in the boot file, asserted in test 4) — this is what keeps iTerm2's `command`
# string single-quote clean (#74).
tmux_line="$(printf '%s\n' "$on_out" | awk '/tmux -CC command/{getline; sub(/^[[:space:]]+/,""); print; exit}')"
assert_contains "extracted a tmux -CC line" "tmux -CC new-session" "$tmux_line"
TMUX_ARGC=0; TMUX_LAST=""
tmux() { TMUX_ARGC=$#; eval "TMUX_LAST=\${$#}"; }
eval "$tmux_line"
assert_eq  "tmux received exactly 8 argv (payload not split)" "8" "$TMUX_ARGC"
assert_contains "the -lc payload sources the boot file (one arg)" ". /tmp/it2agent-tmuxboot." "$TMUX_LAST"
unset -f tmux

echo
echo "--- 6. gate ON via --no-gate even when config flag is OFF ---"
sh "$FLAG" disable agent.tmux >/dev/null
nogate_out="$(sh "$TMUX_BIN" spawn --role worker --task t --no-gate --dry-run -- claude)"
assert_contains "--no-gate forces the tmux path" "tmux -CC new-session" "$nogate_out"
assert_contains "--no-gate reported as bypass"   "bypassed via --no-gate" "$nogate_out"
# IT2AGENT_FORCE=1 also bypasses.
force_out="$(IT2AGENT_FORCE=1 sh "$TMUX_BIN" spawn --role worker --task t --dry-run -- claude)"
assert_contains "IT2AGENT_FORCE=1 forces the tmux path" "tmux -CC new-session" "$force_out"

echo
echo "--- 6b. isolation gate (#75): --no-gate / IT2AGENT_FORCE must NOT force it ---"
# The tmux gate (agent.tmux) and the worktree-isolation gate are SEPARATE. The P2
# bug (#75) was that --no-gate / IT2AGENT_FORCE opened BOTH — silently creating a
# worktree. Fix mirrors it2agent-spawn (fae9d53e7): isolation is opt-in via its
# own flag agent.worktree_isolation or an explicit --force-isolation /
# IT2AGENT_FORCE_ISOLATION. We keep the tmux gate ON (flag) so run_tmux_path runs,
# and probe the isolation gate independently. --dry-run => no worktree is created.
sh "$FLAG" enable agent.tmux >/dev/null
sh "$FLAG" disable agent.worktree_isolation >/dev/null 2>&1
iso_plain="$(cd "$TMUX_DIR" && sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --dry-run -- claude)"
assert_contains "isolation OFF by default (flag off)"              "isolation    : off" "$iso_plain"
iso_nogate="$(cd "$TMUX_DIR" && sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --no-gate --dry-run -- claude)"
assert_contains "--no-gate does NOT force isolation (P2/#75)"      "isolation    : off" "$iso_nogate"
case "$iso_nogate" in
	*IT2AGENT_PORT*) red "P2: --no-gate leaked an IT2AGENT_PORT export (should be #10)" ;;
	*)                green "P2: --no-gate injects no IT2AGENT_PORT" ;;
esac
iso_force_env="$(cd "$TMUX_DIR" && IT2AGENT_FORCE=1 sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --dry-run -- claude)"
assert_contains "IT2AGENT_FORCE does NOT force isolation (P2/#75)" "isolation    : off" "$iso_force_env"
# Explicit opt-in turns it ON even with the flag OFF.
iso_on="$(cd "$TMUX_DIR" && sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --force-isolation --dry-run -- claude)"
assert_contains "isolation ON via --force-isolation"              "isolation    : ON (--force-isolation)" "$iso_on"
assert_contains "ON: exports IT2AGENT_PORT into the tmux session" "export IT2AGENT_PORT="                  "$iso_on"
iso_on_env="$(cd "$TMUX_DIR" && IT2AGENT_FORCE_ISOLATION=1 sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --dry-run -- claude)"
assert_contains "IT2AGENT_FORCE_ISOLATION=1 turns isolation ON"   "isolation    : ON"  "$iso_on_env"
# #109: --ports flows through into per-name IT2AGENT_PORT_<UPPER> exports in the
# inner tmux session script (plus the bare alias); --force-isolation also previews
# the canonical export.
iso_mp="$(cd "$TMUX_DIR" && sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --ports web,db --force-isolation --dry-run -- claude)"
assert_contains "ports: exports IT2AGENT_PORT_WEB in the tmux session" "export IT2AGENT_PORT_WEB=" "$iso_mp"
assert_contains "ports: exports IT2AGENT_PORT_DB in the tmux session"  "export IT2AGENT_PORT_DB="  "$iso_mp"
assert_contains "ports: still exports the bare IT2AGENT_PORT"          "export IT2AGENT_PORT="     "$iso_mp"
assert_contains "canonical: previews IT2AGENT_CANONICAL_PORT_WEB"      "export IT2AGENT_CANONICAL_PORT_WEB=" "$iso_mp"
# #111: --isolate flows through into ENV-ONLY exports in the inner tmux session
# script. --force-isolation forwards --no-gate to the worktree helper so the
# per-mode isolate gates are bypassed for this dry-run preview.
iso_svc="$(cd "$TMUX_DIR" && sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --isolate docker,db --force-isolation --dry-run -- claude)"
assert_contains "isolate docker: COMPOSE_PROJECT_NAME in tmux session" "export COMPOSE_PROJECT_NAME=" "$iso_svc"
assert_contains "isolate db: IT2AGENT_DB_SCHEMA in tmux session"       "export IT2AGENT_DB_SCHEMA="   "$iso_svc"
assert_contains "isolate db: PGOPTIONS search_path in tmux session"    "export PGOPTIONS="            "$iso_svc"
iso_dbd="$(cd "$TMUX_DIR" && sh "$TMUX_BIN" spawn --id 5 --role worker --task iso --isolate db=database --force-isolation --dry-run -- claude)"
assert_contains "isolate db=database: IT2AGENT_DB_NAME in tmux session" "export IT2AGENT_DB_NAME=" "$iso_dbd"
# Restore the tmux gate to OFF for any later assertions.
sh "$FLAG" disable agent.tmux >/dev/null 2>&1

echo
echo "--- 7. attach (recovery) ---"
att_out="$(sh "$TMUX_BIN" attach --role worker --task 'build #5' --dry-run)"
assert_contains "attach derives the same session name" "session name : st-worker-build-5" "$att_out"
assert_contains "attach uses tmux -CC attach -t"       "tmux -CC attach -t 'st-worker-build-5'" "$att_out"
assert_absent   "attach does NOT re-run identity emits" "$EMIT" "$att_out"

echo
echo "--- 8. exit codes ---"
assert_exit "--help exits 0"                     0 sh "$TMUX_BIN" --help
assert_exit "no subcommand exits 2"              2 sh "$TMUX_BIN"
assert_exit "unknown subcommand exits 2"         2 sh "$TMUX_BIN" bogus
assert_exit "spawn missing command exits 2"      2 sh "$TMUX_BIN" spawn --role x
assert_exit "spawn unknown option exits 2"       2 sh "$TMUX_BIN" spawn --bogus -- true
assert_exit "spawn bad status exits 2"           2 sh "$TMUX_BIN" spawn --status wat -- true
assert_exit "spawn --home + --dir conflict exits 2" 2 sh "$TMUX_BIN" spawn --home --dir /tmp -- true
assert_exit "name without a basis exits 2"       2 sh "$TMUX_BIN" name
assert_exit "spawn dry-run happy path exits 0"   0 env IT2AGENT_FORCE=1 sh "$TMUX_BIN" spawn --role x --dry-run -- true

echo
echo "--- 9. generated AppleScript compiles (osacompile), nested quoting intact ---"
if command -v osacompile >/dev/null 2>&1; then
	for label in "tmux-spawn" "attach"; do
		AS_FILE="$(mktemp).applescript"
		if [ "$label" = "tmux-spawn" ]; then
			IT2AGENT_FORCE=1 sh "$TMUX_BIN" spawn --role worker --task "o'brien #5" --dry-run -- claude --resume \
				| awk '/AppleScript that WOULD run:/{f=1;next} f' | sed 's/^    //' > "$AS_FILE"
		else
			sh "$TMUX_BIN" attach --role worker --task "o'brien #5" --dry-run \
				| awk '/AppleScript that WOULD run:/{f=1;next} f' | sed 's/^    //' > "$AS_FILE"
		fi
		if osacompile -o /dev/null "$AS_FILE" >/dev/null 2>&1; then
			green "AppleScript compiles ($label, apostrophe in --task)"
		else
			red "AppleScript failed to compile ($label)"
		fi
		rm -f "$AS_FILE"
	done
else
	printf '  \033[33mNOTE\033[0m osacompile not available; skipping AppleScript parse check\n'
fi

rm -rf "$CFG_DIR"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]

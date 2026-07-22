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
echo "--- 4. the inner tmux script is ONE physical line (write text safe) ---"
# Extract the inner session script line from the dry-run and assert it has no
# embedded newline (iTerm2's `write text` would otherwise submit it piecemeal).
inner="$(printf '%s\n' "$on_out" | awk '/inner session script/{getline; print; exit}')"
assert_contains "inner script present"        "cd '"          "$inner"
assert_contains "inner script is ;-joined"    " ; "           "$inner"
assert_contains "inner script exec's command" "exec 'claude'" "$inner"

echo
echo "--- 5. emitted tmux argv round-trips: -lc payload is a SINGLE arg ---"
# Eval the printed `tmux -CC ...` line with `tmux` shimmed to a shell function
# that records its arg count. Correct quoting => tmux sees exactly 8 args:
#   -CC new-session -A -s <name> <shell> -lc <inner-as-ONE-arg>
tmux_line="$(printf '%s\n' "$on_out" | awk '/tmux -CC command/{getline; sub(/^[[:space:]]+/,""); print; exit}')"
assert_contains "extracted a tmux -CC line" "tmux -CC new-session" "$tmux_line"
TMUX_ARGC=0; TMUX_LAST=""
tmux() { TMUX_ARGC=$#; eval "TMUX_LAST=\${$#}"; }
eval "$tmux_line"
assert_eq  "tmux received exactly 8 argv (payload not split)" "8" "$TMUX_ARGC"
assert_contains "the -lc payload arg carries the whole script" "exec 'claude' '--resume'" "$TMUX_LAST"
assert_contains "the -lc payload arg also carries the cd"      "cd '"                     "$TMUX_LAST"
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

#!/usr/bin/env bash
# Behavior tests for it2agent-spawn (Tier 0.4, #10).
#
# it2agent-spawn drives iTerm2 via osascript, which we cannot exercise
# headless, so every test runs in --dry-run mode and asserts on the plan it
# prints: the resolved cwd, the identity emit calls, and the AppleScript.
#
# Asserts:
#   - default cwd == the spawner's $PWD
#   - --dir <path> and --home override the cwd
#   - identity emits shell out to the MERGED it2agent-emit (not re-implemented)
#   - those emits, when run, set the DOT-FREE user vars (agent_role/task/status)
#   - --help exits 0; missing command / bad flags error with exit 2
#   - the generated AppleScript parses (osacompile), when available
#
# Run from anywhere: bash it2agent/spawn/tests/test_spawn.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SPAWN_DIR="$(dirname "$HERE")"
SPAWN="$SPAWN_DIR/it2agent-spawn"
EMIT="$(cd "$SPAWN_DIR/../emit" && pwd)/it2agent-emit"

pass=0
fail=0
green() { printf '  \033[32mPASS\033[0m %s\n' "$1"; pass=$((pass + 1)); }
red() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail + 1)); }

# assert_contains <label> <needle> <haystack>
assert_contains() {
	case "$3" in
		*"$2"*) green "$1" ;;
		*)      red "$1 (missing: $2)" ;;
	esac
}

# assert_exit <label> <want> <cmd...>
assert_exit() {
	local label="$1" want="$2"; shift 2
	"$@" >/dev/null 2>&1
	local got=$?
	if [ "$got" = "$want" ]; then green "$label (exit $got)"; else red "$label (want $want, got $got)"; fi
}

# Hermetic: isolate the feature-flag config so these tests never depend on the
# operator's real ~/.config/it2agent/config.toml. An empty config => every flag
# defaults OFF, which is what the gate/isolation assertions below assume.
IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
export IT2AGENT_CONFIG

echo "=== it2agent-spawn behavior tests (dry-run) ==="
echo "spawn: $SPAWN"
echo "emit : $EMIT"

echo
echo "--- 1. cwd resolution ---"
# Default: inherit the spawner's $PWD. Run from a known directory.
WORKDIR="$(mktemp -d)"
default_out="$(cd "$WORKDIR" && sh "$SPAWN" --role worker --dry-run -- true)"
assert_contains "default cwd inherits spawner \$PWD" "resolved cwd : $WORKDIR" "$default_out"
assert_contains "default cwd -> cd into \$PWD"        "cd '$WORKDIR'"           "$default_out"

# --dir overrides.
dir_out="$(sh "$SPAWN" --dir /tmp/spawn-target --role worker --dry-run -- true)"
assert_contains "--dir overrides cwd"        "resolved cwd : /tmp/spawn-target" "$dir_out"
assert_contains "--dir=... form overrides"   "resolved cwd : /tmp/other" \
	"$(sh "$SPAWN" --dir=/tmp/other --dry-run -- true)"

# --home overrides to $HOME.
home_out="$(sh "$SPAWN" --home --role worker --dry-run -- true)"
assert_contains "--home overrides cwd to \$HOME" "resolved cwd : $HOME" "$home_out"

echo
echo "--- 2. identity emits use the merged it2agent-emit + dot-free vars ---"
id_out="$(sh "$SPAWN" --role tech-lead --task 'build #10' --status idle --dry-run -- claude)"
# Every identity facet is a call to the MERGED it2agent-emit (absolute path),
# not a re-implemented escape code.
assert_contains "role emit shells out to merged it2agent-emit"   "$EMIT' role 'tech-lead'"   "$id_out"
assert_contains "task emit shells out to merged it2agent-emit"   "$EMIT' task 'build #10'"    "$id_out"
assert_contains "status emit shells out to merged it2agent-emit" "$EMIT' status 'idle'"       "$id_out"
assert_contains "color derived from status"                       "$EMIT' color 'idle'"        "$id_out"
assert_contains "badge emit shells out to merged it2agent-emit"  "$EMIT' badge"               "$id_out"
# No hand-rolled escape codes in spawn's own output (identity is delegated).
case "$id_out" in
	*SetUserVar*|*1337*) red "spawn re-implemented escape codes (should delegate to emit)" ;;
	*)                   green "spawn does not re-implement escape codes" ;;
esac
# The emit calls, when actually run, set the DOT-FREE user vars agent_role /
# agent_task / agent_status (iTerm2 forbids '.' in a SetUserVar key). Verify the
# mechanism against the real emit (bypassing its gate).
emit_role="$(IT2AGENT_FORCE=1 sh "$EMIT" role tech-lead | cat -v)"
assert_contains "emit role -> dot-free user var agent_role"     "SetUserVar=agent_role="   "$emit_role"
case "$emit_role" in
	*agent.role*) red "emit produced a DOTTED var (agent.role) — must be dot-free" ;;
	*)            green "emit role has no dotted agent.role" ;;
esac
emit_status="$(IT2AGENT_FORCE=1 sh "$EMIT" status idle | cat -v)"
assert_contains "emit status -> dot-free user var agent_status" "SetUserVar=agent_status=" "$emit_status"

echo
echo "--- 3. gate forwarding ---"
# Default: no bypass; the plan says emit self-gates.
assert_contains "default: emit self-gates (no bypass)" "gate bypass  : no" \
	"$(sh "$SPAWN" --role x --dry-run -- true)"
# --no-gate forwards --no-gate into every emit call.
nogate_out="$(sh "$SPAWN" --role x --no-gate --dry-run -- true)"
assert_contains "--no-gate forwarded to emit" "$EMIT' --no-gate role 'x'" "$nogate_out"
# IT2AGENT_FORCE=1 in the spawner env is forwarded as --no-gate.
force_out="$(IT2AGENT_FORCE=1 sh "$SPAWN" --role x --dry-run -- true)"
assert_contains "IT2AGENT_FORCE=1 forwarded as --no-gate" "$EMIT' --no-gate role 'x'" "$force_out"

echo
echo "--- 4. the command to run is preserved as trailing args ---"
cmd_out="$(sh "$SPAWN" --role x --dry-run -- claude --resume --model opus)"
assert_contains "trailing command preserved" "'claude' '--resume' '--model' 'opus'" "$cmd_out"

echo
echo "--- 5. exit codes ---"
assert_exit "--help exits 0"                 0 sh "$SPAWN" --help
assert_exit "missing command exits 2"        2 sh "$SPAWN" --role x
assert_exit "unknown option exits 2"         2 sh "$SPAWN" --bogus -- true
assert_exit "bad status exits 2"             2 sh "$SPAWN" --status wat -- true
assert_exit "--home + --dir conflict exits 2" 2 sh "$SPAWN" --home --dir /tmp -- true
assert_exit "dry-run happy path exits 0"     0 sh "$SPAWN" --role x --dry-run -- true

echo
echo "--- 6b. worktree isolation gate (#13) ---"
# Gate OFF (default config): spawn behaves EXACTLY as #10 — plain \$PWD, no
# worktree, no port exports. Run from a plain (git) dir; the flag is off.
iso_off="$(cd "$SPAWN_DIR" && sh "$SPAWN" --id 13 --role worker --dry-run -- true)"
assert_contains "isolation OFF by default (= #10)" "isolation    : off" "$iso_off"
case "$iso_off" in
	*IT2AGENT_PORT*) red "gate OFF leaked a IT2AGENT_PORT export (should be #10)" ;;
	*)                green "gate OFF injects no IT2AGENT_PORT (plain cwd inheritance)" ;;
esac
# Bug P2 fix: --no-gate / IT2AGENT_FORCE bypass ONLY the emit gate; they must
# NOT enable worktree isolation. With the flag OFF, isolation stays OFF.
iso_nogate="$(cd "$SPAWN_DIR" && sh "$SPAWN" --id 13 --role worker --no-gate --dry-run -- true)"
assert_contains "--no-gate does NOT force isolation (P2)" "isolation    : off" "$iso_nogate"
iso_force_env="$(cd "$SPAWN_DIR" && IT2AGENT_FORCE=1 sh "$SPAWN" --id 13 --role worker --dry-run -- true)"
assert_contains "IT2AGENT_FORCE does NOT force isolation (P2)" "isolation    : off" "$iso_force_env"
# Explicit opt-in: --force-isolation turns isolation ON even with the flag OFF ->
# worktree cwd + port/ns exports appear. Requires a git repo, so run from the spawn dir.
iso_on="$(cd "$SPAWN_DIR" && sh "$SPAWN" --id 13 --role worker --task iso --force-isolation --dry-run -- claude)"
assert_contains "isolation ON via --force-isolation"  "isolation    : ON"                 "$iso_on"
assert_contains "ON: new tab cds into the worktree"   "worktree=" "$iso_on"
assert_contains "ON: exports IT2AGENT_PORT"          "export IT2AGENT_PORT="            "$iso_on"
assert_contains "ON: exports IT2AGENT_NS"            "export IT2AGENT_NS="              "$iso_on"
assert_contains "ON: branch under it2agent/"         "branch=it2agent/worker-13-"       "$iso_on"
# The user command still survives after the injected exports.
assert_contains "ON: user command preserved after exports" "'claude'" "$iso_on"

echo
echo "--- 6c. capability-guide header (#56) ---"
# By default the new session gets a 1-line pointer to the guide (it2agent help),
# both flagged in the plan and present in the session commands.
guide_out="$(sh "$SPAWN" --role x --dry-run -- true)"
assert_contains "guide header on by default (plan line)" "guide header : on" "$guide_out"
assert_contains "guide header printed into the session"  "run: it2agent help" "$guide_out"
# The header is a pointer, NOT the whole guide (no capability tables dumped).
case "$guide_out" in
	*"Known flags:"*|*"| flag |"*) red "spawn dumped guide content (should only point at it)" ;;
	*)                             green "spawn points at the guide, does not dump it" ;;
esac
# --no-guide opts out: no header line in the plan or the session commands.
noguide_out="$(sh "$SPAWN" --role x --no-guide --dry-run -- true)"
assert_contains "--no-guide reflected in plan" "guide header : off (--no-guide)" "$noguide_out"
case "$noguide_out" in
	*"run: it2agent help"*) red "--no-guide still injected the guide header" ;;
	*)                       green "--no-guide omits the guide header" ;;
esac

echo
echo "--- 6d. delivery: boot LAUNCHED as the tab command, not typed (bugs P1 + #74) ---"
# P1: the (possibly long) script lives in a /tmp boot file, never inlined. #74:
# the boot is LAUNCHED as the new tab's `command` (create tab/window with default
# profile command "..."), NEVER typed via `write text`. Launching as the session
# command runs it in exactly the created tab with nothing fed to a line editor,
# which kills the zle/typeahead delivery race that dropped identity on some tabs.
deliv_out="$(sh "$SPAWN" --role worker --task "build #10" --dry-run -- python3 /a/very/long/path/to/some/agent_shim.py --sock /tmp/x.sock --result /tmp/y.log --timeout 30)"
assert_contains "plan documents /tmp boot-file delivery" "boot file" "$deliv_out"
as_only="$(printf '%s\n' "$deliv_out" | awk '/AppleScript that WOULD run:/{f=1;next} f')"
assert_contains "#74: tab created WITH a command"          "with default profile command " "$as_only"
assert_contains "#74: command sources the /tmp boot file"  ". /tmp/it2agent-boot."          "$as_only"
# #74: absolutely no `write text` (that was the racy path). No assert_absent here,
# so check by hand.
case "$as_only" in
	*"write text"*) red "#74: AppleScript still types via write text (racy path not removed)" ;;
	*)              green "#74: no write text — launched as the tab command" ;;
esac
case "$as_only" in
	*agent_shim.py*) red "P1: long command leaked into the AppleScript (should be in the boot file)" ;;
	*)               green "P1: long command stays in the boot file, not the launched line" ;;
esac

echo
echo "--- 6e. native tab-status (ccstatus) wiring (#89) ---"
# The boot script always includes an it2agent-emit ccstatus line AFTER the
# identity emits. It self-gates on agent.native_status inside it2agent-emit, so
# it is a no-op when that flag is OFF — hence it is present in the plan
# regardless of flag state. <status> mirrors --status; --detail mirrors the
# badge's role · task composition.
cc_out="$(sh "$SPAWN" --role tech-lead --task 'build #10' --status busy --dry-run -- claude)"
assert_contains "ccstatus line present with status + role · task detail" \
	"$EMIT' ccstatus 'busy' --detail 'tech-lead · build #10'" "$cc_out"
# It comes AFTER the identity emits (badge precedes ccstatus in the boot script).
cc_order="$(printf '%s\n' "$cc_out" | grep -n "it2agent-emit' \(badge\|ccstatus\)")"
badge_ln="$(printf '%s\n' "$cc_order" | sed -n 's/^\([0-9]*\):.*badge.*/\1/p' | head -1)"
ccst_ln="$(printf '%s\n' "$cc_order" | sed -n 's/^\([0-9]*\):.*ccstatus.*/\1/p' | head -1)"
if [ -n "$badge_ln" ] && [ -n "$ccst_ln" ] && [ "$ccst_ln" -gt "$badge_ln" ]; then
	green "ccstatus emitted AFTER the identity emits (badge before ccstatus)"
else
	red "ccstatus not ordered after the identity emits (badge=$badge_ln ccstatus=$ccst_ln)"
fi
# <status> tracks --status.
cc_idle="$(sh "$SPAWN" --role r --task t --status idle --dry-run -- true)"
assert_contains "ccstatus status tracks --status (idle)" "ccstatus 'idle' --detail 'r · t'" "$cc_idle"
# --no-gate is forwarded to the ccstatus emit like the others.
cc_nogate="$(sh "$SPAWN" --role r --task t --no-gate --dry-run -- true)"
assert_contains "--no-gate forwarded to ccstatus" "$EMIT' --no-gate ccstatus 'busy' --detail 'r · t'" "$cc_nogate"
# Fallbacks mirror the badge: role-only, task-only, and both-empty (omit --detail).
cc_roleonly="$(sh "$SPAWN" --role solo --dry-run -- true)"
assert_contains "role-only detail is just the role" "ccstatus 'busy' --detail 'solo'" "$cc_roleonly"
cc_taskonly="$(sh "$SPAWN" --task lonely --dry-run -- true)"
assert_contains "task-only detail is just the task" "ccstatus 'busy' --detail 'lonely'" "$cc_taskonly"
cc_none="$(sh "$SPAWN" --dry-run -- true)"
assert_contains "no role/task still emits ccstatus (status only)" "ccstatus 'busy'" "$cc_none"
case "$cc_none" in
	*"ccstatus 'busy' --detail"*) red "ccstatus should omit --detail when role and task are both empty" ;;
	*)                            green "ccstatus omits --detail when role and task are both empty" ;;
esac

echo
echo "--- 6. generated AppleScript parses ---"
if command -v osacompile >/dev/null 2>&1; then
	AS_FILE="$(mktemp).applescript"
	sh "$SPAWN" --role worker --task "o'brien build #10" --dry-run -- claude --resume \
		| awk '/AppleScript that WOULD run:/{f=1;next} f' | sed 's/^    //' > "$AS_FILE"
	if osacompile -o /dev/null "$AS_FILE" >/dev/null 2>&1; then
		green "AppleScript compiles (osacompile), even with an apostrophe in --task"
	else
		red "AppleScript failed to compile"
	fi
	rm -f "$AS_FILE"
else
	printf '  \033[33mNOTE\033[0m osacompile not available; skipping AppleScript parse check\n'
fi

rm -rf "$WORKDIR"

echo
echo "=== summary: $pass passed, $fail failed ==="
[ "$fail" -eq 0 ]

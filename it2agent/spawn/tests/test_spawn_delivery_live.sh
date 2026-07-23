#!/usr/bin/env bash
# LIVE regression test for the #74 boot-delivery race in it2agent-spawn.
#
# Unlike test_spawn.sh (dry-run only), this drives REAL iTerm2 spawns — but it
# verifies HEADLESS, with no human eye on the GUI. Each spawned agent's boot
# script (launched as the tab's `command`, the #74 fix) writes a sentinel file
# as its LAST step. A correct, race-free delivery therefore produces EXACTLY one
# sentinel per burst spawn. The old `write text` path fed the new shell's line
# editor before it was ready, so stray typeahead bytes (`que.`/`foi `) corrupted
# the source line on some tabs — those tabs produced NO sentinel. So:
#
#     count(sentinels) == N   <=>   every burst delivery landed intact.
#
# We spawn N agents in a tight burst (the stress that provoked the race) over
# several rounds, and assert N/N every round. Tabs we open are closed by their
# recorded iTerm2 session id, so the test leaves no residue.
#
# Requires a running iTerm2. If none is running, the test SKIPS (exit 0) rather
# than fabricate a result — the dry-run suite still covers the shape.
#
#   bash it2agent/spawn/tests/test_spawn_delivery_live.sh [N] [ROUNDS]
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
SPAWN="$(dirname "$HERE")/it2agent-spawn"

N="${1:-6}"        # agents per burst round
ROUNDS="${2:-5}"   # number of burst rounds

green() { printf '  \033[32mPASS\033[0m %s\n' "$1"; }
red()   { printf '  \033[31mFAIL\033[0m %s\n' "$1"; }

echo "=== it2agent-spawn LIVE delivery regression (#74) ==="
echo "spawn : $SPAWN"
echo "burst : ${N} agents x ${ROUNDS} rounds"

# Need a live iTerm2 — do not fabricate a result without one.
if ! osascript -e 'tell application "System Events" to (name of processes) contains "iTerm2"' 2>/dev/null | grep -q true; then
	printf '  \033[33mSKIP\033[0m iTerm2 is not running — live delivery test skipped (dry-run suite still covers shape)\n'
	exit 0
fi

# Isolated flag config so behaviour does not depend on the operator's real one.
# IT2AGENT_FORCE=1 (below) exercises the full identity path; the sentinel is the
# boot script's last line, so it only appears if the WHOLE boot ran.
CFG_DIR="$(mktemp -d)"; export IT2AGENT_CONFIG="$CFG_DIR/config.toml"; : > "$IT2AGENT_CONFIG"

# Close the iTerm2 sessions whose id matches a recorded ITERM_SESSION_ID uuid.
close_uuid() {
	osascript - "$1" >/dev/null 2>&1 <<'AS'
on run argv
	set targetId to item 1 of argv
	tell application "iTerm2"
		repeat with w in windows
			repeat with t in tabs of w
				repeat with s in sessions of t
					if (id of s) is targetId then close s
				end repeat
			end repeat
		end repeat
	end tell
end run
AS
}

overall_fail=0
for r in $(seq 1 "$ROUNDS"); do
	DONE_DIR="$(mktemp -d /tmp/it2burst.XXXXXX)"
	SID_DIR="$(mktemp -d /tmp/it2sid.XXXXXX)"

	# Burst: launch all N spawns as fast as the shell can fork them.
	for k in $(seq 1 "$N"); do
		id="burst-${r}-${k}"
		# Agent command: record MY iTerm session id (for cleanup), then drop the
		# sentinel. $ITERM_SESSION_ID is evaluated in the NEW session (kept literal
		# here via \$); the file paths are absolute so they need no session env.
		body="printf %s \"\$ITERM_SESSION_ID\" > '${SID_DIR}/${id}.sid'; : > '${DONE_DIR}/${id}'"
		IT2AGENT_FORCE=1 sh "$SPAWN" --role burst --id "$id" --task "r${r}" -- /bin/sh -c "$body" &
	done
	wait

	# Wait for N sentinels — bounded poll on the real completion signal (the files),
	# NOT a fixed sleep masking a race: the tabs boot asynchronously and we stop the
	# instant all N land, or bail at a hard deadline.
	deadline=$((SECONDS + 30))
	while :; do
		got=$(find "$DONE_DIR" -type f -name 'burst-*' | wc -l | tr -d ' ')
		[ "$got" -ge "$N" ] && break
		[ "$SECONDS" -ge "$deadline" ] && break
		sleep 0.2
	done
	got=$(find "$DONE_DIR" -type f -name 'burst-*' | wc -l | tr -d ' ')

	# Close every tab we opened this round (by recorded session id).
	for f in "$SID_DIR"/*.sid; do
		[ -f "$f" ] || continue
		sid="$(cat "$f")"; uuid="${sid##*:}"
		[ -n "$uuid" ] && close_uuid "$uuid"
	done

	if [ "$got" = "$N" ]; then
		green "round ${r}: ${got}/${N} sentinels — every delivery intact"
	else
		red "round ${r}: ${got}/${N} sentinels — ${got} deliveries, $((N - got)) LOST (race!)"
		overall_fail=1
	fi
	rm -rf "$DONE_DIR" "$SID_DIR"
done

rm -rf "$CFG_DIR"

echo
if [ "$overall_fail" -eq 0 ]; then
	echo "=== PASS: 100% delivery across ${ROUNDS} rounds of ${N} burst spawns ==="
else
	echo "=== FAIL: at least one burst round lost a delivery ==="
fi
[ "$overall_fail" -eq 0 ]

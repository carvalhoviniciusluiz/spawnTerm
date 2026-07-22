# Validating the iTerm2 Python API over `tmux -CC` (manual checklist)

Tier 3 (#5). **This checklist requires a live macOS + iTerm2 + tmux run.** The
CI/unit tests in `tests/test_tmux.sh` do NOT cover it (they are pure/dry-run).
Do **not** record results here from anything other than a real run — the point
of Tier 3 is to answer an open question honestly, not to guess.

There are two ways to run it:

- **Automated harness:** `python3 validate_api_over_tmux.py --session st-<id>`
  — connects via the iTerm2 Python API, measures surfaces (2), (4), (5)
  automatically, and prints a PASS/FAIL table. Surfaces (1) and (3) need an
  operator action to *fire*, so the harness only confirms the monitors attach.
- **Fully manual checklist:** below, if you prefer to eyeball each surface.

## What we are validating and why

The Tier 1 daemon (`spawnterm/daemon`) controls agent sessions through the
iTerm2 Python API. Under `tmux -CC`, the agent's iTerm2 windows are *tmux
clients*: tmux owns the processes. The open question is whether the API still
sees and controls those sessions. Five surfaces the daemon depends on:

| # | API surface | daemon use | risk under tmux-CC |
|---|-------------|------------|--------------------|
| 1 | `NewSessionMonitor` (`new_session`) | register the agent | low — iTerm2 mints a real session per tmux window |
| 2 | `CustomControlSequenceMonitor` (`custom_escape_sequence`) | agent→daemon envelopes | **HIGH** — tmux may swallow OSC 1337 unless passthrough-wrapped |
| 3 | `PromptMonitor` (`prompt`) | idle detection | medium — needs shell integration marks through tmux |
| 4 | `async_get_screen_contents` | ack-by-observation | low/medium |
| 5 | `async_set_variable` / `async_get_variable` (`user.*`) | identity/tagging | **HIGH** — user vars are set via the same OSC 1337 family |

## Prerequisites (once)

1. iTerm2 → Settings → General → Magic → **Enable Python API**.
2. `pip3 install iterm2`
3. iTerm2 shell integration installed in the login shell (for surface 3/prompt).

## Procedure

1. **Start the daemon with debug logging** so its `new_session` / `ingest` /
   `prompt` / `deliver` log lines are visible:
   - `spawnterm.daemon`, `spawnterm.status_board` flags ON (`spawnterm-flag enable …`),
   - run `spawnterm/daemon/spawnterm_daemon.py` (see the daemon README).

2. **Spawn an agent under tmux -CC** from an iTerm2 tab:
   ```sh
   spawnterm/tmux/spawnterm-tmux spawn --no-gate --role probe --task api -- $SHELL -l
   ```
   iTerm2 should open a **new native window/tab** for the tmux session
   `st-probe-api`. (If not, run `tmux -CC new-session -A -s st-probe-api` by hand
   to confirm native integration works at all on this box.)

3. **Surface 1 — new_session:** confirm the daemon logged
   `new_session <id> (registry size N)` for the tmux-backed session. ☐ pass ☐ fail
   Notes: ________________________________________________

4. **Surface 5 — user vars:** in the tmux pane, run
   `spawnterm-emit --no-gate role probe`. Then either watch the daemon re-snapshot
   or run `validate_api_over_tmux.py`. Confirm the API reads back
   `user.agent_role = probe`. ☐ pass ☐ fail
   - If it FAILS: try wrapping in tmux passthrough (`tmux set -g allow-passthrough on`,
     or emit `\ePtmux;\e…\e\\`). Record which was needed.
   Notes: ________________________________________________

5. **Surface 2 — custom escape sequence:** in the tmux pane, emit a spawnterm
   envelope (e.g. via the agent's messaging path, or by hand:
   `printf '\033]1337;Custom=id=spawnterm:%s\a' "$(printf '{"v":1}' | base64)"`).
   Confirm the daemon logged `ingest: … known_type=…`. ☐ pass ☐ fail
   - If it FAILS raw but PASSES wrapped in `\ePtmux;…\e\\`, record that the
     agent-side emit must wrap under tmux (a follow-up for `spawnterm-emit`).
   Notes: ________________________________________________

6. **Surface 3 — prompt/idle:** let the agent reach a shell prompt (or press
   Enter). Confirm the daemon logged `prompt: session <id> idle`. ☐ pass ☐ fail
   Notes: ________________________________________________

7. **Surface 4 — screen read:** trigger a delivery (send the agent a broker
   message) or run the harness; confirm `async_get_screen_contents` returned the
   pane text (ack-by-observation works). ☐ pass ☐ fail
   Notes: ________________________________________________

8. **Persistence — the whole point:** with the agent mid-task, **quit iTerm2**
   (⌘Q) or kill it. Reopen iTerm2 and run
   `spawnterm/tmux/spawnterm-tmux attach --role probe --task api`. Confirm the
   window/agent come back and the daemon re-registers the session. ☐ pass ☐ fail
   Notes: ________________________________________________

## Recording results

Copy the harness's PASS/FAIL table (or the boxes above) into `README.md` under
**“Findings (API over tmux-CC)”**, dated, with the iTerm2 + tmux versions. Until
a real run happens, that section stays marked **UNVALIDATED**.

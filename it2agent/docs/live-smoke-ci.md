# Live smoke CI (self-hosted macOS runner)

The `.github/workflows/live-smoke.yml` workflow closes the "live layer has no CI"
gap (issue #132, part of #1). It runs `it2agent/tests/live_smoke.py --json` on a
**self-hosted macOS runner** — the operator's Mac, with iTerm2 3.7.dev running and
the Python API enabled — and fails the job on any surface that is not `PASS`.

## Why self-hosted (and not a hosted runner)

Four of seven production bugs (#74/#81/#85/#76) only appeared **live**, in the
layer that talks to a *running* iTerm2 (Python API / AppleScript). Hosted GitHub
runners have no GUI, no iTerm2, and no API socket, so they can never exercise the
`spawn` / `tmux` / `mcp` surfaces. The two existing workflows cover everything a
hosted runner *can* reach and are untouched by this one:

- `test.yml` — Python-API library (ubuntu) + Xcode ModernTests (macOS).
- `headless-tests.yml` — the pure it2agent layer + `live_smoke.py --only ccstatus`
  (the single live surface that needs no running iTerm2: exact OSC 21337 bytes).

The job pins `runs-on: [self-hosted, macOS, it2agent-live]`. The distinctive
`it2agent-live` label guarantees it dispatches **only** to the operator's
registered Mac and never to a hosted runner.

## Triggers

- **`workflow_dispatch`** — the "Run workflow" button in the Actions tab (manual).
- **`schedule`** — daily at 07:00 UTC (a regression sweep).
- **`pull_request`** — runs **only** when the PR carries the `live` label (a job
  `if:` gates it). Unlabelled PRs never trigger the live layer, so everyday PRs
  are not blocked on the operator's Mac being online.

If no runner with the `it2agent-live` label is online, the job simply stays
**queued / pending**. That is expected and does **not** block or fail the hosted
CI in the other workflows — they are separate workflows with their own runners.

## Register your Mac as the runner (one-time)

**Prerequisites** (must hold every time the job runs, not just at setup):

1. iTerm2 3.7.dev installed **and running** with at least one open window/tab (the
   live surfaces open real tabs; the app must be up, not merely installed).
2. The iTerm2 Python API server enabled:
   ```
   defaults write com.googlecode.iterm2 EnableAPIServer -bool true
   defaults read  com.googlecode.iterm2 EnableAPIServer   # must print 1
   ```
   (UI: Settings → General → Magic → "Enable Python API".)
3. `python3 -c "import iterm2"` works for the `python3` the runner will use (the
   runner inherits the login shell's PATH; install the `iterm2` package there).

**Register** (the GitHub UI hands you a one-time token — never hard-code it):

1. Repo → **Settings → Actions → Runners → New self-hosted runner**.
2. Choose macOS / your arch and follow the shown download + `./config.sh` steps.
3. At the `./config.sh` prompt **"Enter any additional labels"**, type:
   ```
   it2agent-live
   ```
   The default `self-hosted` and `macOS`/`Darwin` labels are added automatically;
   this workflow's `runs-on` requires all three together.
4. Start it: `./run.sh` (foreground) or `./svc.sh install && ./svc.sh start` to
   run as a launchd service. The runner process must have a **GUI session** (a
   logged-in desktop) so iTerm2 can open windows — run it as your logged-in user,
   not as a headless root daemon.

Verify: Settings → Actions → Runners should list the runner as **Idle** with the
`it2agent-live` label. Then trigger the workflow via **Run workflow** and confirm
it lands on your runner.

## The tmux retry (#127)

The `tmux -CC` surface is the biggest flakiness source: iTerm2 attaches the
integrated session a beat after tmux creates it, and a busy machine can push that
past the wait deadline. The workflow runs the full smoke once; if — and only if —
`tmux` is the **sole** non-`PASS` surface, it retries `--only tmux` once with
`IT2AGENT_SMOKE_TMUX_TIMEOUT=90`. If any other surface (`spawn`/`mcp`/`ccstatus`)
is non-`PASS`, it fails immediately, so the retry can never mask a real
regression. A persistent tmux timeout still reddens the build; just re-run the
workflow, or bump the timeout further.

## Validation

There is no self-hosted runner in the hosted CI environment, so the workflow
cannot be exercised there — it is validated by config (YAML parses; `runs-on`
uses the self-hosted `it2agent-live` label so it can never run on a hosted
runner) plus the operator registering the runner as above. Opening a PR does not
run this workflow unless the PR is labelled `live` and the runner is online.

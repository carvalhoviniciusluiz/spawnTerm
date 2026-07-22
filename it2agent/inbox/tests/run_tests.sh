#!/usr/bin/env bash
# Run the it2agent agent-inbox unit tests (#17). Pure Python + stdlib only; no
# pip deps, no iTerm2, no external services, no sleeps. Covers: the policy engine
# across reversibility/scope/cost + allow-list (auto/needs-human/block), the
# allow-list config loader, attention-routing as pure logic, the intake ->
# queue -> decision flow with a mock broker + recording emitter, graceful
# degradation when the broker is down, and the gate-off no-op + module purity.
# New test_*.py files are auto-discovered.
#
# Usage: bash it2agent/inbox/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag reads OFF deterministically.
export IT2AGENT_CONFIG="$(mktemp -d)/config.toml"
unset IT2AGENT_FORCE
unset IT2AGENT_INBOX_CONFIG

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v

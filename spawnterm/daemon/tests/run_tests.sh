#!/usr/bin/env bash
# Run the spawnTerm daemon unit tests (#26, #28). Pure Python; no iterm2
# required. Auto-discovers test_*.py, so registry/envelope/gate/router are all
# included.
#
# Usage: bash spawnterm/daemon/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config so the flag-gate tests are deterministic.
export SPAWNTERM_CONFIG="$(mktemp -d)/config.toml"
unset SPAWNTERM_FORCE

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v

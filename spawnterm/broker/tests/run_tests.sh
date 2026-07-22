#!/usr/bin/env bash
# Run the spawnTerm broker unit tests (#34). Pure Python + stdlib only; no pip
# deps, no iTerm2, no external services. Covers: path resolution, sqlite schema
# (WAL/busy-timeout/idempotent migration), wire-protocol round-trips, op-dispatch
# (ping/health/unknown/extensibility), the spawnterm.broker flag gate + purity,
# and an end-to-end unix-socket round-trip (socket in a tmpdir). New test_*.py
# files are auto-discovered.
#
# Usage: bash spawnterm/broker/tests/run_tests.sh
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

# Isolate from any real ~/.config and ~/.local/state so tests are deterministic.
export SPAWNTERM_CONFIG="$(mktemp -d)/config.toml"
unset SPAWNTERM_FORCE
unset SPAWNTERM_BROKER_DB
unset SPAWNTERM_BROKER_SOCK

python3 -m unittest discover -s "$HERE" -p 'test_*.py' -v

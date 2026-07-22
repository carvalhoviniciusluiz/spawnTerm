#!/usr/bin/env bash
# Run the spawnterm i18n test suite (#66).
#
# Usage: bash spawnterm/i18n/tests/run_tests.sh
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
exec bash "$HERE/test_i18n.sh"

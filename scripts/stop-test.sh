#!/usr/bin/env bash
# stop-test.sh — terminate all Java processes started by run-test.sh
# for a given run directory and run analyze-logs.py on the results.
#
# Usage:
#   ./scripts/stop-test.sh <run-dir>
#
# If <run-dir> is omitted, defaults to the most recent subdirectory under
# e2e-results/.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_DIR="${1:-}"

if [[ -z "$RUN_DIR" ]]; then
    RUN_DIR="$(ls -td "$REPO_ROOT"/e2e-results/*/ 2>/dev/null | head -1)"
    RUN_DIR="${RUN_DIR%/}"
fi

if [[ -z "$RUN_DIR" || ! -d "$RUN_DIR" ]]; then
    echo "ERROR: run dir not found: ${RUN_DIR:-<none>}" >&2
    exit 1
fi

echo "Stopping run: $RUN_DIR"

stopped=0
for pidfile in "$RUN_DIR"/*.pid; do
    [[ -f "$pidfile" ]] || continue
    pid="$(cat "$pidfile")"
    name="$(basename "$pidfile" .pid)"
    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null || true
        echo "  stopped $name (pid $pid)"
        stopped=$((stopped + 1))
    else
        echo "  $name (pid $pid) already exited"
    fi
    rm -f "$pidfile"
done

# Give processes a couple seconds to flush logs cleanly
sleep 2

# Summarise + run analysis
echo
echo "Logs in $RUN_DIR:"
ls -la "$RUN_DIR"/*.log 2>/dev/null | sed 's|.* |  |'

echo
echo "Running analyze-logs.py..."
python3 "$REPO_ROOT/scripts/analyze-logs.py" "$RUN_DIR"

echo
echo "Done. Aggregate to dashboard with:"
echo "  python3 $REPO_ROOT/scripts/compare-runs.py $REPO_ROOT/e2e-results -o $REPO_ROOT/dashboard/data/runs.json"

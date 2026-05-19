#!/bin/bash
# v11-then-v12.sh — Wait for v11 to finish, then auto-launch v12.
# Run: nohup bash infra/v11-then-v12.sh > /tmp/v11-v12-chain.log 2>&1 &

set -euo pipefail
cd "$(dirname "$0")/.."

echo "[$(date)] Waiting for v11 orchestrator to finish..."

# Wait for v11 orchestrator process to exit
while true; do
    V11_PID=$(cat /tmp/v11-orchestrator.pid 2>/dev/null || echo "")
    if [[ -z "$V11_PID" ]] || ! kill -0 "$V11_PID" 2>/dev/null; then
        echo "[$(date)] v11 orchestrator finished (pid=$V11_PID)"
        break
    fi
    sleep 30
done

# Check v11 result
V11_STATUS=$(python3 -c "
import json
d = json.load(open('infra/state/v11-progress.json'))
phases = d.get('phases', {})
failed = [n for n,p in phases.items() if p.get('status') == 'failed']
done = [n for n,p in phases.items() if p.get('status') == 'done']
print(f'done={len(done)} failed={len(failed)}')
if failed:
    print('FAILED phases: ' + ', '.join(failed[:5]))
")
echo "[$(date)] v11 result: $V11_STATUS"

# Launch v12 regardless (v12 uses its own state files)
echo "[$(date)] Launching v12 (aggressive-timeouts)..."
export V11_CONFIG=v12-aggressive-timeouts
rm -f infra/state/v12-progress.json infra/state/v12-master.lock

nohup python3 infra/orchestrate-v11.py > /tmp/v12-launch.log 2>&1 < /dev/null &
V12_PID=$!
echo "$V12_PID" > /tmp/v12-orchestrator.pid
sleep 5

if kill -0 $V12_PID 2>/dev/null; then
    echo "[$(date)] ✓ v12 orchestrator alive (pid=$V12_PID)"
else
    echo "[$(date)] ✗ v12 orchestrator died!"
    tail -20 /tmp/v12-launch.log
    exit 1
fi

echo "[$(date)] v12 running. Monitor: tail -f infra/state/v12-master.log"

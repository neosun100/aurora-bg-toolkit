#!/bin/bash
# v14-then-v15.sh — Wait for v14 to finish, then auto-launch v15.
#
# v15 = v14 (ZGC + AlwaysPreTouch + StringDedup + fixed Xms)
#       + Linux TCP keepalive sysctl tuning (60s / 10s / 6 probes)
#
# Run: nohup bash infra/v14-then-v15.sh > /tmp/v14-v15-chain.log 2>&1 &

set -euo pipefail
cd "$(dirname "$0")/.."

echo "[$(date)] Waiting for v14 orchestrator to finish..."

while true; do
    V14_PID=$(cat /tmp/v14-orchestrator.pid 2>/dev/null || echo "")
    if [[ -z "$V14_PID" ]] || ! kill -0 "$V14_PID" 2>/dev/null; then
        echo "[$(date)] v14 orchestrator finished (pid=$V14_PID)"
        break
    fi
    sleep 30
done

V14_STATUS=$(python3 -c "
import json
d = json.load(open('infra/state/v14-progress.json'))
phases = d.get('phases', {})
done = sum(1 for p in phases.values() if p.get('status') == 'done')
failed = sum(1 for p in phases.values() if p.get('status') == 'failed')
print(f'done={done} failed={failed}')
" 2>/dev/null || echo "v14 progress not readable")
echo "[$(date)] v14 result: $V14_STATUS"

# Verify clusters cleaned
sleep 30
CLUSTERS=$(aws --profile jiasunm-neo --region us-east-1 rds describe-db-clusters \
    --query 'length(DBClusters[?contains(DBClusterIdentifier,`v11`)])' \
    --output text 2>/dev/null || echo "?")
echo "[$(date)] Clusters remaining after v14: $CLUSTERS"

if [[ "$CLUSTERS" != "0" ]]; then
    echo "[$(date)] WARNING: v14 didn't fully clean. Manual cleanup may be needed."
    # Don't abort — try to proceed; v15 cdk deploy will handle stack updates
fi

# Launch v15
echo "[$(date)] Launching v15 (TCP keepalive tuned)..."

rm -f infra/state/v15-progress.json infra/state/v15-master.lock

export V11_CONFIG=v15-tcp-tuned
export V11_EXTRA_JVM='-XX:+UnlockExperimentalVMOptions -XX:+UseZGC -XX:+AlwaysPreTouch -XX:+UseStringDeduplication -Xms2g'
export V11_APPLY_SYSCTL=1

nohup python3 infra/orchestrate-v11.py > /tmp/v15-launch.log 2>&1 < /dev/null &
V15_PID=$!
echo "$V15_PID" > /tmp/v15-orchestrator.pid
sleep 5

if kill -0 $V15_PID 2>/dev/null; then
    echo "[$(date)] ✓ v15 orchestrator alive (pid=$V15_PID)"
    echo "[$(date)] state: infra/state/v15-progress.json"
    echo "[$(date)] log:   infra/state/v15-master.log"
    echo "[$(date)] sysctl tuning: ENABLED (V11_APPLY_SYSCTL=1)"
else
    echo "[$(date)] ✗ v15 orchestrator died!"
    tail -20 /tmp/v15-launch.log
    exit 1
fi

echo "[$(date)] v15 running. Monitor: tail -f infra/state/v15-master.log"
echo "[$(date)] Or: open http://localhost:9999"

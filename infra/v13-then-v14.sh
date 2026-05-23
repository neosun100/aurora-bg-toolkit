#!/bin/bash
# v13-then-v14.sh — Wait for v13 to finish, then auto-launch v14.
#
# v14 is JVM tuning maximization: ZGC + AlwaysPreTouch + StringDedup + fixed Xms.
#
# Run: nohup bash infra/v13-then-v14.sh > /tmp/v13-v14-chain.log 2>&1 &

set -euo pipefail
cd "$(dirname "$0")/.."

echo "[$(date)] Waiting for v13 orchestrator to finish..."

while true; do
    V13_PID=$(cat /tmp/v13-orchestrator.pid 2>/dev/null || echo "")
    if [[ -z "$V13_PID" ]] || ! kill -0 "$V13_PID" 2>/dev/null; then
        echo "[$(date)] v13 orchestrator finished (pid=$V13_PID)"
        break
    fi
    sleep 30
done

# Check v13 result
V13_STATUS=$(python3 -c "
import json
d = json.load(open('infra/state/v13-progress.json'))
phases = d.get('phases', {})
done = sum(1 for p in phases.values() if p.get('status') == 'done')
failed = sum(1 for p in phases.values() if p.get('status') == 'failed')
print(f'done={done} failed={failed}')
")
echo "[$(date)] v13 result: $V13_STATUS"

# AWS resources should be cleaned by v13's CDK_DESTROY
# But verify clusters are 0 — if not, abort (something's wrong)
sleep 30
CLUSTERS=$(aws --profile jiasunm-neo --region us-east-1 rds describe-db-clusters \
    --query 'length(DBClusters[?contains(DBClusterIdentifier,`v11`)])' \
    --output text 2>/dev/null || echo "?")
echo "[$(date)] Clusters remaining after v13: $CLUSTERS"

if [[ "$CLUSTERS" != "0" ]]; then
    echo "[$(date)] ABORT: v13 didn't clean up. Manual intervention needed."
    exit 1
fi

# Launch v14
echo "[$(date)] Launching v14 (jvm-tuned: ZGC + AlwaysPreTouch + StringDedup + fixed Xms)..."

# Clean v14 state
rm -f infra/state/v14-progress.json infra/state/v14-master.lock

export V11_CONFIG=v14-jvm-tuned
export V11_EXTRA_JVM='-XX:+UnlockExperimentalVMOptions -XX:+UseZGC -XX:+AlwaysPreTouch -XX:+UseStringDeduplication -Xms2g'

nohup python3 infra/orchestrate-v11.py > /tmp/v14-launch.log 2>&1 < /dev/null &
V14_PID=$!
echo "$V14_PID" > /tmp/v14-orchestrator.pid
sleep 5

if kill -0 $V14_PID 2>/dev/null; then
    echo "[$(date)] ✓ v14 orchestrator alive (pid=$V14_PID)"
    echo "[$(date)] state: infra/state/v14-progress.json"
    echo "[$(date)] log:   infra/state/v14-master.log"
else
    echo "[$(date)] ✗ v14 orchestrator died!"
    tail -20 /tmp/v14-launch.log
    exit 1
fi

echo "[$(date)] v14 running. Monitor: tail -f infra/state/v14-master.log"
echo "[$(date)] Or: open http://localhost:9999"

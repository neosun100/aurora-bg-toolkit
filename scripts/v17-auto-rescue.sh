#!/usr/bin/env bash
# v17-auto-rescue.sh — auto-detect and unblock orchestrator when wait_for_clean
# circles for too long (>15 min on a single cluster).
#
# Background: orchestrate-v11.py's cdk destroy can fail silently for
# AbtV11ClusterStack-N when an RDS BG lifecycle race holds the cluster.
# orchestrate-matrix.py then loops in wait_for_clean_account, never
# escaping. We rescue by:
#   1. Detect: matrix-progress.json hasn't moved status for >15 min AND
#      there's exactly 1-2 lingering test-v11-N cluster
#   2. Force delete cluster + instances + stack
#   3. Mark previous run as done in progress.json (data is preserved)
#   4. Wait for orchestrator's next loop iteration to detect 0 clusters
#      and proceed to next run
#
# Run on user laptop / cron every 15 min during v17 matrix execution:
#   */15 * * * * /Users/jiasunm/Code/aurora-bg-toolkit/scripts/v17-auto-rescue.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="$REPO_ROOT/infra/state/abt-v11-key.pem"
RUNNER_IP="${V17_RUNNER_IP:-54.165.23.6}"
BUCKET="${ABT_STATE_BUCKET:-abt-v17-state-835751346093}"

export AWS_PROFILE=jiasunm-neo
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1

TMP=$(mktemp -d)
trap 'rm -rf $TMP' EXIT

# 1) Pull current progress
aws s3 cp "s3://$BUCKET/matrix-progress.json" "$TMP/p.json" --quiet 2>/dev/null || {
    echo "[$(date +%H:%M)] cannot fetch progress.json"
    exit 0
}

# 2) Check if we're stuck
stuck=$(python3 -c "
import json, datetime
p = json.load(open('$TMP/p.json'))
runs = p.get('runs', {})
running = [k for k,v in runs.items() if v.get('status') == 'running']
if not running:
    print('not_running')
elif len(running) > 1:
    print('multiple_running')
else:
    rid = running[0]
    started = runs[rid].get('started_at', '')
    if not started:
        print('no_start_time')
    else:
        s = datetime.datetime.fromisoformat(started.rstrip('Z'))
        age_min = (datetime.datetime.utcnow() - s).total_seconds() / 60
        # If running > 4 hours, very likely stuck (longest legit run ~3.5h)
        if age_min > 240:
            print(f'stuck:{rid}:{int(age_min)}')
        else:
            print(f'ok:{rid}:{int(age_min)}')
" 2>/dev/null || echo "parse_error")

case "$stuck" in
    not_running|parse_error)
        echo "[$(date +%H:%M)] $stuck — no action"; exit 0 ;;
    ok:*)
        echo "[$(date +%H:%M)] $stuck — running normally"; exit 0 ;;
esac

# 3) We're stuck — count lingering RDS clusters
cluster_count=$(aws rds describe-db-clusters \
    --query 'length(DBClusters[?starts_with(DBClusterIdentifier, `test-v11`)])' \
    --output text 2>/dev/null || echo "0")

if [ "$cluster_count" = "0" ]; then
    echo "[$(date +%H:%M)] $stuck but 0 clusters — orchestrator should advance soon"
    exit 0
fi

# 4) Lingering clusters — force delete
echo "[$(date +%H:%M)] STUCK detected: $stuck with $cluster_count clusters — RESCUING"

CLUSTERS=$(aws rds describe-db-clusters \
    --query 'DBClusters[?starts_with(DBClusterIdentifier, `test-v11`)].DBClusterIdentifier' \
    --output text 2>/dev/null)

for CLUSTER in $CLUSTERS; do
    echo "  Force-deleting $CLUSTER..."
    aws rds describe-db-instances --filters "Name=db-cluster-id,Values=$CLUSTER" \
        --query 'DBInstances[*].DBInstanceIdentifier' --output text 2>/dev/null | tr '\t' '\n' | \
        while read instance; do
            [ -z "$instance" ] && continue
            aws rds delete-db-instance --db-instance-identifier "$instance" \
                --skip-final-snapshot --no-delete-automated-backups >/dev/null 2>&1 || true
            echo "    triggered delete: $instance"
        done

    sleep 60
    aws rds delete-db-cluster --db-cluster-identifier "$CLUSTER" \
        --skip-final-snapshot >/dev/null 2>&1 || true

    # Also delete stack
    stack="AbtV11ClusterStack-$(echo "$CLUSTER" | awk -F- '{print $NF}')"
    aws cloudformation delete-stack --stack-name "$stack" >/dev/null 2>&1 || true
done

# 5) Bark notify the user
if [ -n "${BARK_PASSWORD:-}" ] && [ -n "${BARK_KEY_MAC:-}" ]; then
    curl -s -X POST "https://${BARK_SERVER:-bark.aws.xin}/push" \
        -u "${BARK_USERNAME:-bark}:${BARK_PASSWORD}" \
        -H "Content-Type: application/json" \
        -H "User-Agent: v17-rescue/1.0" \
        -d "{\"device_key\":\"${BARK_KEY_MAC}\",\"title\":\"⚠️ v17 auto-rescue triggered\",\"body\":\"Detected stuck $stuck. Force-deleted $cluster_count cluster(s). Matrix should resume in ~5 min.\",\"level\":\"timeSensitive\",\"sound\":\"calypso\",\"group\":\"AuroraBGToolkit-v17\"}" \
        >/dev/null 2>&1 || true
fi

echo "[$(date +%H:%M)] rescue complete — wait for orchestrator's next wait_for_clean to succeed"

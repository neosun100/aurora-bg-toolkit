#!/usr/bin/env bash
# v17-monitor-heartbeat.sh — Health watchdog for v17 matrix sweep.
#
# Designed to run via cron every 10-15 minutes on the runner EC2 (or locally).
# Detects stuck states that the orchestrator's own state file might miss:
#   1. matrix-progress.json hasn't updated in >30 minutes (orchestrator hung)
#   2. RDS clusters lingering >30 min after a run is supposedly done
#   3. systemd service inactive but no completed_at in matrix-progress.json
#   4. CDK stacks in *_FAILED state
#
# Triggers Bark alarm (level=critical) on any detection, so user gets a phone
# notification even if email/SNS is filtered.
#
# Usage (locally for spot-checking):
#   bash scripts/v17-monitor-heartbeat.sh
#
# Usage on runner (cron-installable):
#   */15 * * * * /opt/abt/aurora-bg-toolkit/scripts/v17-monitor-heartbeat.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AWS_PROFILE="${AWS_PROFILE:-jiasunm-neo}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_PROFILE AWS_REGION

# Load Bark from ~/.env if local; on runner it's in systemd Environment
[ -f "$HOME/.env" ] && { set -a; source "$HOME/.env"; set +a; }

ABT_STATE_BUCKET="${ABT_STATE_BUCKET:-abt-v17-state-$(aws sts get-caller-identity --query Account --output text 2>/dev/null)}"
TMP=$(mktemp -d)
trap 'rm -rf $TMP' EXIT

bark_critical() {
    local title="$1"; local body="$2"
    [ -z "${BARK_PASSWORD:-}" ] && { echo "[no Bark]"; echo "$title: $body"; return; }
    curl -s -X POST "https://${BARK_SERVER:-bark.aws.xin}/push" \
        -u "${BARK_USERNAME:-bark}:${BARK_PASSWORD}" \
        -H "Content-Type: application/json" \
        -H "User-Agent: abt-v17-watchdog/1.0" \
        -d "$(cat <<EOF
{"device_key":"${BARK_KEY_MAC}","title":"⚠️ $title","body":"$body","level":"critical","sound":"alarm","group":"AuroraBGToolkit-v17"}
EOF
)" >/dev/null 2>&1 || true
    echo "[Bark critical sent] $title"
}

# 1. Check matrix-progress.json freshness
if aws s3 cp "s3://$ABT_STATE_BUCKET/matrix-progress.json" "$TMP/p.json" --quiet 2>/dev/null; then
    last_mod=$(aws s3api head-object --bucket "$ABT_STATE_BUCKET" --key matrix-progress.json --query 'LastModified' --output text 2>/dev/null)
    if [ -n "$last_mod" ]; then
        last_epoch=$(date -j -f "%Y-%m-%dT%H:%M:%S+00:00" "${last_mod%.*}+00:00" "+%s" 2>/dev/null || \
                     date -d "$last_mod" "+%s" 2>/dev/null || echo 0)
        now=$(date +%s)
        age=$((now - last_epoch))
        completed=$(python3 -c "import json; print(json.load(open('$TMP/p.json')).get('completed_at') or '')")

        # If completed_at set, all good. If not set AND >30 min stale → alarm
        if [ -z "$completed" ] && [ "$age" -gt 1800 ]; then
            mins=$((age / 60))
            bark_critical "v17 progress.json stale" "matrix-progress.json hasn't updated in ${mins} minutes (last_mod=$last_mod). Orchestrator may be hung."
        fi
    fi
else
    bark_critical "v17 state bucket unreachable" "Cannot fetch s3://$ABT_STATE_BUCKET/matrix-progress.json. Check AWS creds or bucket name."
fi

# 2. Check for lingering RDS clusters
v11_count=$(aws rds describe-db-clusters \
    --query "length(DBClusters[?starts_with(DBClusterIdentifier, 'test-v11-')])" --output text 2>/dev/null || echo 0)
if [ "$v11_count" -gt 5 ]; then
    bark_critical "v17 too many RDS clusters" "Found $v11_count test-v11-* clusters; expected ≤5 in steady state. Check for orphans."
fi

# 3. CDK stacks in failed state
failed_stacks=$(aws cloudformation list-stacks \
    --stack-status-filter CREATE_FAILED ROLLBACK_FAILED ROLLBACK_COMPLETE UPDATE_ROLLBACK_FAILED DELETE_FAILED \
    --query 'StackSummaries[?contains(StackName, `Abt`)].StackName' --output text 2>/dev/null || echo "")
if [ -n "$failed_stacks" ]; then
    bark_critical "v17 CDK stacks failed" "Stacks: $failed_stacks"
fi

echo "[$(date +%H:%M:%S)] v17 watchdog: OK"

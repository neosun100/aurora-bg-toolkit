#!/usr/bin/env bash
# orchestrate-bg-round.sh — orchestrate ONE round of Blue/Green switchover
# across all 5 clusters in parallel.
#
# Per cluster:
#   1. Start a Java client on the EC2 (background; one log file per cluster)
#   2. Wait 60 seconds for the workload to stabilise
#   3. Trigger switchover-blue-green-deployment
#   4. Wait 5 minutes for completion / stabilisation
#   5. Stop the Java client; pull its log back to local; analyze
#
# Each (cluster, config) maps to:
#   test-01 -> customer-baseline
#   test-02 -> v4-current
#   test-03 -> v5-experimental
#   test-04 -> v6-aggressive
#   test-05 -> v7-dns-warmup
#
# Usage: ./orchestrate-bg-round.sh <round-number>
#
# Per-round result lives at e2e-results/round-N/.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
source "$STATE_DIR/ec2.env"

ROUND="${1:?usage: $0 <round-number>}"
TMP_PASSWORD=$(cat "$STATE_DIR/.tmp-master-pass")
ROUND_DIR="$REPO_ROOT/e2e-results/round-${ROUND}_$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$ROUND_DIR"

declare -A CFG=(
    [test-01]=customer-baseline
    [test-02]=v4-current
    [test-03]=v5-experimental
    [test-04]=v6-aggressive
    [test-05]=v7-dns-warmup
)

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
ssh_ec2() { ssh -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR ec2-user@"$ABT_EC2_PUBLIC_IP" "$@"; }

echo "============================================================"
echo " BG Round $ROUND  ($(date -u +%H:%M:%S) UTC)"
echo "============================================================"

# Phase 1: Start Java clients (4 per cluster: wrapper-3.3 + wrapper-4.0 each EC2-only)
# For simplicity: 1 wrapper version (4.0.0) per cluster in this round, since 5
# clusters * 2 wrappers = 10 java processes simultaneously is heavy on c6i.large.
echo "[1] Starting Java clients on EC2..."
ssh_ec2 "rm -rf /home/ec2-user/round-${ROUND} && mkdir -p /home/ec2-user/round-${ROUND}"
for c in test-01 test-02 test-03 test-04 test-05; do
    source "$STATE_DIR/${c}.env"
    cfg=${CFG[$c]}
    cat <<EOF | ssh_ec2 "bash -s" >/dev/null
set -e
cd /home/ec2-user/aurora-bg-toolkit
mkdir -p /home/ec2-user/round-${ROUND}/${c}
DB_ENDPOINT="$ABT_CLUSTER_ENDPOINT" DB_PORT=4488 DB_USER=admin DB_NAME=demo \\
  DB_PASSWORD="$TMP_PASSWORD" \\
  TABLE_SUFFIX="ec2_4_r${ROUND}" WRAPPER_VERSION=4.0.0 \\
  nohup java --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED \\
    -jar abt-w400.jar configs/${cfg}.yaml \\
    > /home/ec2-user/round-${ROUND}/${c}/ec2_wrapper4.log 2>&1 &
echo \$! > /home/ec2-user/round-${ROUND}/${c}/ec2_wrapper4.pid
EOF
    pid=$(ssh_ec2 "cat /home/ec2-user/round-${ROUND}/${c}/ec2_wrapper4.pid")
    echo "   $c ($cfg, w4.0.0) -> pid $pid"
done

# Phase 2: warm-up
echo "[2] Warm-up 60s..."
sleep 60

# Phase 3: trigger switchover for each cluster IN PARALLEL
echo "[3] Triggering switchover for all 5 BGs..."
TRIGGER_TS=$(date -u +%s)
for c in test-01 test-02 test-03 test-04 test-05; do
    source "$STATE_DIR/${c}.bg.env"
    aws_ rds switchover-blue-green-deployment \
        --blue-green-deployment-identifier "$ABT_BG_ID" \
        --switchover-timeout 600 >/dev/null &
    echo "   $c -> switchover initiated"
done
wait

# Phase 4: stabilise
echo "[4] Stabilise 5 minutes..."
sleep 300

# Phase 5: stop java clients
echo "[5] Stopping java clients..."
for c in test-01 test-02 test-03 test-04 test-05; do
    pid=$(ssh_ec2 "cat /home/ec2-user/round-${ROUND}/${c}/ec2_wrapper4.pid 2>/dev/null || echo 0")
    if [[ "$pid" != "0" ]]; then
        ssh_ec2 "kill $pid 2>/dev/null || true"
        echo "   $c killed pid $pid"
    fi
done
sleep 5  # let logs flush

# Phase 6: pull logs back
echo "[6] Pulling logs..."
for c in test-01 test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    mkdir -p "$ROUND_DIR/${c}_${cfg}"
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/round-${ROUND}/${c}/ec2_wrapper4.log \
        "$ROUND_DIR/${c}_${cfg}/ec2_wrapper4.log" 2>/dev/null
    cat > "$ROUND_DIR/${c}_${cfg}/meta.json" <<JSON
{
  "runId": "${c}_${cfg}_round${ROUND}",
  "config": "$cfg",
  "scenario": "blue-green",
  "round": $ROUND,
  "endpoint": "$(grep ABT_CLUSTER_ENDPOINT $STATE_DIR/${c}.env | cut -d'"' -f2)",
  "scenarioStartedAt": "$(date -u -r $TRIGGER_TS +%Y-%m-%dT%H:%M:%S 2>/dev/null || date -u +%Y-%m-%dT%H:%M:%S)"
}
JSON
done

# Phase 7: analyze
echo "[7] Analyzing logs..."
for c in test-01 test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    python3 "$REPO_ROOT/scripts/analyze-logs.py" "$ROUND_DIR/${c}_${cfg}" 2>&1 | tail -2
done

echo
echo "Round $ROUND complete. Results: $ROUND_DIR"
echo "Summary:"
for c in test-01 test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    if [[ -f "$ROUND_DIR/${c}_${cfg}/analysis.json" ]]; then
        wmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/analysis.json'));print(d['summary']['writeMaxMs'])")
        rmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/analysis.json'));print(d['summary']['readMaxMs'])")
        printf "   %-10s %-22s write_max=%6dms  read_max=%6dms\n" "$c" "$cfg" "$wmax" "$rmax"
    fi
done

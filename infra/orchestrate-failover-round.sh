#!/usr/bin/env bash
# orchestrate-failover-round.sh — one round of Failover across 4 clusters in parallel
# (skipping test-01 customer-baseline since baseline downtime in failover is well-known).
#
# Failover (FailoverDBCluster) doesn't need a Blue/Green deployment. We can
# repeat it on the same cluster many times.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
source "$STATE_DIR/ec2.env"

ROUND="${1:?usage: $0 <round-number>}"
TMP_PASSWORD=$(cat "$STATE_DIR/.tmp-master-pass")
ROUND_DIR="$REPO_ROOT/e2e-results/failover-${ROUND}_$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$ROUND_DIR"

declare -A CFG=(
    [test-02]=v4-current
    [test-03]=v5-experimental
    [test-04]=v6-aggressive
    [test-05]=v7-dns-warmup
)

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
ssh_ec2() { ssh -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR ec2-user@"$ABT_EC2_PUBLIC_IP" "$@"; }

echo "============================================================"
echo " Failover Round $ROUND  ($(date -u +%H:%M:%S) UTC)"
echo "============================================================"

# Phase 1: start 4 clients
echo "[1] Starting Java clients..."
ssh_ec2 "rm -rf /home/ec2-user/failover-${ROUND} && mkdir -p /home/ec2-user/failover-${ROUND}"
for c in test-02 test-03 test-04 test-05; do
    source "$STATE_DIR/${c}.env"
    cfg=${CFG[$c]}
    cat <<EOF | ssh_ec2 "bash -s" >/dev/null
mkdir -p /home/ec2-user/failover-${ROUND}/${c}
cd /home/ec2-user/aurora-bg-toolkit
DB_ENDPOINT="$ABT_CLUSTER_ENDPOINT" DB_PORT=4488 DB_USER=admin DB_NAME=demo \\
  DB_PASSWORD="$TMP_PASSWORD" \\
  TABLE_SUFFIX="ec2_4_fr${ROUND}" WRAPPER_VERSION=4.0.0 \\
  nohup java --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED \\
    -jar abt-w400.jar configs/${cfg}.yaml \\
    > /home/ec2-user/failover-${ROUND}/${c}/ec2_wrapper4.log 2>&1 &
echo \$! > /home/ec2-user/failover-${ROUND}/${c}/ec2_wrapper4.pid
EOF
    pid=$(ssh_ec2 "cat /home/ec2-user/failover-${ROUND}/${c}/ec2_wrapper4.pid")
    echo "   $c ($cfg) -> pid $pid"
done

# Phase 2: warm-up
echo "[2] Warm-up 60s..."
sleep 60

# Phase 3: trigger failover for all 4 clusters in parallel
echo "[3] Triggering failover-db-cluster on all 4 clusters..."
for c in test-02 test-03 test-04 test-05; do
    aws_ rds failover-db-cluster --db-cluster-identifier "$c" >/dev/null &
    echo "   $c -> failover initiated"
done
wait

# Phase 4: stabilise
echo "[4] Stabilise 3 minutes..."
sleep 180

# Phase 5: stop java clients + pull logs
echo "[5] Stopping clients..."
for c in test-02 test-03 test-04 test-05; do
    pid=$(ssh_ec2 "cat /home/ec2-user/failover-${ROUND}/${c}/ec2_wrapper4.pid 2>/dev/null || echo 0")
    if [[ "$pid" != "0" ]]; then
        ssh_ec2 "kill $pid 2>/dev/null || true"
    fi
done
sleep 5

echo "[6] Pulling logs..."
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    mkdir -p "$ROUND_DIR/${c}_${cfg}"
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/failover-${ROUND}/${c}/ec2_wrapper4.log \
        "$ROUND_DIR/${c}_${cfg}/ec2_wrapper4.log" 2>/dev/null
    cat > "$ROUND_DIR/${c}_${cfg}/meta.json" <<JSON
{"runId": "${c}_${cfg}_failover_r${ROUND}", "config": "$cfg", "scenario": "failover", "round": $ROUND}
JSON
done

echo "[7] Analyzing..."
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    python3 "$REPO_ROOT/scripts/analyze-logs.py" "$ROUND_DIR/${c}_${cfg}" 2>&1 | tail -1
done

echo
echo "Failover Round $ROUND complete. Results: $ROUND_DIR"
echo "Summary:"
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    if [[ -f "$ROUND_DIR/${c}_${cfg}/analysis.json" ]]; then
        wmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/analysis.json'));print(d['summary']['writeMaxMs'])")
        rmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/analysis.json'));print(d['summary']['readMaxMs'])")
        printf "   %-10s %-22s write_max=%6dms  read_max=%6dms\n" "$c" "$cfg" "$wmax" "$rmax"
    fi
done

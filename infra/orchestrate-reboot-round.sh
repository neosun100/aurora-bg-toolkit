#!/usr/bin/env bash
# orchestrate-reboot-round.sh — one round of writer reboot across 4 clusters in parallel.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
source "$STATE_DIR/ec2.env"

ROUND="${1:?usage: $0 <round-number>}"
TMP_PASSWORD=$(cat "$STATE_DIR/.tmp-master-pass")
ROUND_DIR="$REPO_ROOT/e2e-results/reboot-${ROUND}_$(date -u +%Y%m%d_%H%M%S)"
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
echo " Reboot Round $ROUND  ($(date -u +%H:%M:%S) UTC)"
echo "============================================================"

ssh_ec2 "rm -rf /home/ec2-user/reboot-${ROUND} && mkdir -p /home/ec2-user/reboot-${ROUND}"
for c in test-02 test-03 test-04 test-05; do
    source "$STATE_DIR/${c}.env"
    cfg=${CFG[$c]}
    cat <<EOF | ssh_ec2 "bash -s" >/dev/null
mkdir -p /home/ec2-user/reboot-${ROUND}/${c}
cd /home/ec2-user/aurora-bg-toolkit
DB_ENDPOINT="$ABT_CLUSTER_ENDPOINT" DB_PORT=4488 DB_USER=admin DB_NAME=demo DB_PASSWORD="$TMP_PASSWORD" \\
  TABLE_SUFFIX="ec2_4_rb${ROUND}" WRAPPER_VERSION=4.0.0 \\
  nohup java --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED \\
    -jar abt-w400.jar configs/${cfg}.yaml \\
    > /home/ec2-user/reboot-${ROUND}/${c}/ec2_wrapper4.log 2>&1 &
echo \$! > /home/ec2-user/reboot-${ROUND}/${c}/ec2_wrapper4.pid
EOF
    pid=$(ssh_ec2 "cat /home/ec2-user/reboot-${ROUND}/${c}/ec2_wrapper4.pid")
    echo "   $c ($cfg) -> pid $pid"
done

echo "[2] Warm-up 60s..."
sleep 60

echo "[3] Triggering reboot-db-instance on all 4 writers..."
for c in test-02 test-03 test-04 test-05; do
    aws_ rds reboot-db-instance --db-instance-identifier "$c-writer" >/dev/null &
    echo "   $c-writer -> reboot initiated"
done
wait

echo "[4] Stabilise 2 minutes..."
sleep 120

echo "[5] Stopping clients..."
for c in test-02 test-03 test-04 test-05; do
    pid=$(ssh_ec2 "cat /home/ec2-user/reboot-${ROUND}/${c}/ec2_wrapper4.pid 2>/dev/null || echo 0")
    [[ "$pid" != "0" ]] && ssh_ec2 "kill $pid 2>/dev/null || true"
done
sleep 5

echo "[6] Pulling + analyzing..."
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    mkdir -p "$ROUND_DIR/${c}_${cfg}"
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/reboot-${ROUND}/${c}/ec2_wrapper4.log \
        "$ROUND_DIR/${c}_${cfg}/ec2_wrapper4.log" 2>/dev/null
    cat > "$ROUND_DIR/${c}_${cfg}/meta.json" <<JSON
{"runId": "${c}_${cfg}_reboot_r${ROUND}", "config": "$cfg", "scenario": "reboot", "round": $ROUND}
JSON
    python3 "$REPO_ROOT/scripts/analyze-logs.py" "$ROUND_DIR/${c}_${cfg}" 2>&1 | tail -1
done

echo
echo "Reboot Round $ROUND results: $ROUND_DIR"
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    if [[ -f "$ROUND_DIR/${c}_${cfg}/analysis.json" ]]; then
        wmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/analysis.json'));print(d['summary']['writeMaxMs'])")
        sgap=$(python3 "$REPO_ROOT/scripts/analyze-stats-gap.py" "$ROUND_DIR/${c}_${cfg}/ec2_wrapper4.log" 2>/dev/null | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['summary']['writeMaxMs'])")
        printf "   %-10s %-22s WRITE_FAIL_max=%5dms  STATS_gap_max=%5dms\n" "$c" "$cfg" "$wmax" "$sgap"
    fi
done

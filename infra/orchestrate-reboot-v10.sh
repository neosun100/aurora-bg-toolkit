#!/usr/bin/env bash
# orchestrate-reboot-v10.sh — single Reboot round for the v10 experiment.
#
# Single cell: cluster=test-v10, config=v10-final, wrapper=4.0.1.
#
# Usage: ./orchestrate-reboot-v10.sh <round-number>

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
source "$STATE_DIR/ec2.env"
source "$STATE_DIR/test-v10.env"

ROUND="${1:?usage: $0 <round-number>}"
TMP_PASSWORD=$(cat "$STATE_DIR/.tmp-master-pass")
ROUND_DIR="$REPO_ROOT/e2e-results/v10-reboot-${ROUND}_$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$ROUND_DIR/test-v10_v10-final"

CFG="v10-final"
JAR="abt-w401.jar"

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
ssh_ec2() { ssh -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=10 ec2-user@"$ABT_EC2_PUBLIC_IP" "$@"; }

echo "============================================================"
echo " v10 Reboot Round $ROUND  ($(date -u +%H:%M:%S) UTC)"
echo "============================================================"

ssh_ec2 "rm -rf /home/ec2-user/v10rb-${ROUND} && mkdir -p /home/ec2-user/v10rb-${ROUND}"
cat <<EOF | ssh_ec2 "bash -s" >/dev/null
mkdir -p /home/ec2-user/v10rb-${ROUND}/test-v10
cd /home/ec2-user/aurora-bg-toolkit
DB_ENDPOINT="$ABT_CLUSTER_ENDPOINT" DB_PORT=4488 DB_USER=admin DB_NAME=demo \\
  DB_PASSWORD="$TMP_PASSWORD" \\
  TABLE_SUFFIX="ec2_v10rb${ROUND}" WRAPPER_VERSION="${JAR%.jar}" \\
  nohup java -Dnetworkaddress.cache.ttl=5 -Dnetworkaddress.cache.negative.ttl=2 \\
    --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED \\
    -Xmx4g -jar ${JAR} configs/${CFG}.yaml \\
    > /home/ec2-user/v10rb-${ROUND}/test-v10/ec2_wrapper.log 2>&1 &
echo \$! > /home/ec2-user/v10rb-${ROUND}/test-v10/ec2_wrapper.pid
EOF
echo "[1] client started"

echo "[2] Warm-up 60s..."
sleep 60

echo "[3] Rebooting writer test-v10-writer..."
aws_ rds reboot-db-instance --db-instance-identifier "test-v10-writer" >/dev/null
echo "    reboot initiated"

echo "[4] Stabilise 90s..."
sleep 90

echo "[5] Stopping client..."
pid=$(ssh_ec2 "cat /home/ec2-user/v10rb-${ROUND}/test-v10/ec2_wrapper.pid 2>/dev/null || echo 0")
[[ "$pid" != "0" ]] && ssh_ec2 "kill $pid 2>/dev/null || true"
sleep 5

echo "[6] Pulling log + analyzing..."
scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
    ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/v10rb-${ROUND}/test-v10/ec2_wrapper.log \
    "$ROUND_DIR/test-v10_${CFG}/ec2_wrapper.log" 2>/dev/null

cat > "$ROUND_DIR/test-v10_${CFG}/meta.json" <<JSON
{"runId": "test-v10_${CFG}_v10rb_r${ROUND}", "config": "$CFG", "scenario": "reboot", "round": $ROUND, "wrapperJar": "${JAR}", "experiment": "v10-production"}
JSON

python3 "$REPO_ROOT/scripts/analyze-stats-gap.py" \
    "$ROUND_DIR/test-v10_${CFG}/ec2_wrapper.log" \
    > "$ROUND_DIR/test-v10_${CFG}/stats-gap.json" 2>/dev/null

if [[ -f "$ROUND_DIR/test-v10_${CFG}/stats-gap.json" ]]; then
    wmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/test-v10_${CFG}/stats-gap.json'));print(d['summary']['writeMaxMs'])" 2>/dev/null || echo 0)
    rmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/test-v10_${CFG}/stats-gap.json'));print(d['summary']['readMaxMs'])" 2>/dev/null || echo 0)
    period=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/test-v10_${CFG}/stats-gap.json'));print(d.get('detectedPeriodMs','?'))" 2>/dev/null || echo "?")
    echo
    echo "v10 Reboot Round $ROUND result:"
    printf "   test-v10  %s  %s  period=%sms  write_max=%dms  read_max=%dms\n" \
        "$CFG" "$JAR" "$period" "$wmax" "$rmax"
    echo "$wmax" > "$ROUND_DIR/test-v10_${CFG}/writeMaxMs"
    echo "$rmax" > "$ROUND_DIR/test-v10_${CFG}/readMaxMs"
fi

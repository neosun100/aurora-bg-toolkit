#!/usr/bin/env bash
# orchestrate-bg-v9.sh — single Blue/Green round across 4 cells:
#   test-02: v4-current   + wrapper 4.0.0  (control)
#   test-03: v4-current   + wrapper 4.0.1
#   test-04: v9-tuned     + wrapper 4.0.0
#   test-05: v9-tuned     + wrapper 4.0.1
#
# Each cell runs the same workload (1280 ops/s, pool=50). The 10Hz STATS
# reporter only kicks in when the CONFIG sets statsReporterHz > 1, so
# v4-current cells emit at 1Hz and v9-tuned cells emit at 10Hz. The
# analyzer auto-detects the period.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
source "$STATE_DIR/ec2.env"

ROUND="${1:?usage: $0 <round-number>}"
TMP_PASSWORD=$(cat "$STATE_DIR/.tmp-master-pass")
ROUND_DIR="$REPO_ROOT/e2e-results/v9-bg-${ROUND}_$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$ROUND_DIR"

# (cluster, config, jar) triples
declare -A CFG=(
    [test-02]=v4-current
    [test-03]=v4-current
    [test-04]=v9-tuned
    [test-05]=v9-tuned
)
declare -A JAR=(
    [test-02]=abt-w400.jar
    [test-03]=abt-w401.jar
    [test-04]=abt-w400.jar
    [test-05]=abt-w401.jar
)

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
ssh_ec2() { ssh -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR ec2-user@"$ABT_EC2_PUBLIC_IP" "$@"; }

echo "============================================================"
echo " v9 BG Round $ROUND  ($(date -u +%H:%M:%S) UTC)"
echo "============================================================"

# Phase 1: Start clients
echo "[1] Starting Java clients..."
ssh_ec2 "rm -rf /home/ec2-user/v9bg-${ROUND} && mkdir -p /home/ec2-user/v9bg-${ROUND}"
for c in test-02 test-03 test-04 test-05; do
    source "$STATE_DIR/${c}.env"
    cfg=${CFG[$c]}
    jar=${JAR[$c]}
    cat <<EOF | ssh_ec2 "bash -s" >/dev/null
mkdir -p /home/ec2-user/v9bg-${ROUND}/${c}
cd /home/ec2-user/aurora-bg-toolkit
DB_ENDPOINT="$ABT_CLUSTER_ENDPOINT" DB_PORT=4488 DB_USER=admin DB_NAME=demo \\
  DB_PASSWORD="$TMP_PASSWORD" \\
  TABLE_SUFFIX="ec2_v9bg${ROUND}" WRAPPER_VERSION="${jar%.jar}" \\
  nohup java -Dnetworkaddress.cache.ttl=5 -Dnetworkaddress.cache.negative.ttl=2 \\
    --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED \\
    -Xmx4g -jar ${jar} configs/${cfg}.yaml \\
    > /home/ec2-user/v9bg-${ROUND}/${c}/ec2_wrapper.log 2>&1 &
echo \$! > /home/ec2-user/v9bg-${ROUND}/${c}/ec2_wrapper.pid
EOF
    pid=$(ssh_ec2 "cat /home/ec2-user/v9bg-${ROUND}/${c}/ec2_wrapper.pid")
    echo "   $c ($cfg, $jar) -> pid $pid"
done

# Phase 2: warm-up
echo "[2] Warm-up 90s..."
sleep 90

# Phase 3: trigger BG switchover (parallel)
echo "[3] Triggering switchover for all 4 BGs..."
for c in test-02 test-03 test-04 test-05; do
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

# Phase 5: stop clients
echo "[5] Stopping clients..."
for c in test-02 test-03 test-04 test-05; do
    pid=$(ssh_ec2 "cat /home/ec2-user/v9bg-${ROUND}/${c}/ec2_wrapper.pid 2>/dev/null || echo 0")
    [[ "$pid" != "0" ]] && ssh_ec2 "kill $pid 2>/dev/null || true"
done
sleep 5

# Phase 6: pull logs + analyze
echo "[6] Pulling logs + analyzing..."
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    mkdir -p "$ROUND_DIR/${c}_${cfg}"
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/v9bg-${ROUND}/${c}/ec2_wrapper.log \
        "$ROUND_DIR/${c}_${cfg}/ec2_wrapper.log" 2>/dev/null
    cat > "$ROUND_DIR/${c}_${cfg}/meta.json" <<JSON
{"runId": "${c}_${cfg}_v9bg_r${ROUND}", "config": "$cfg", "scenario": "blue-green", "round": $ROUND, "wrapperJar": "${JAR[$c]}"}
JSON
    python3 "$REPO_ROOT/scripts/analyze-stats-gap.py" "$ROUND_DIR/${c}_${cfg}/ec2_wrapper.log" \
        > "$ROUND_DIR/${c}_${cfg}/stats-gap.json" 2>/dev/null
done

echo
echo "v9 BG Round $ROUND results:"
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    jar=${JAR[$c]}
    if [[ -f "$ROUND_DIR/${c}_${cfg}/stats-gap.json" ]]; then
        wmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/stats-gap.json'));print(d['summary']['writeMaxMs'])" 2>/dev/null || echo 0)
        period=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/stats-gap.json'));print(d.get('detectedPeriodMs','?'))" 2>/dev/null || echo "?")
        printf "   %-10s %-12s %-13s period=%4sms write_max=%6dms\n" "$c" "$cfg" "$jar" "$period" "$wmax"
    fi
done

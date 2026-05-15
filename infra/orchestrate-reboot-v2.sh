#!/usr/bin/env bash
# orchestrate-reboot-v2.sh — high-load Reboot round.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
source "$STATE_DIR/ec2.env"

ROUND="${1:?usage: $0 <round-number>}"
TMP_PASSWORD=$(cat "$STATE_DIR/.tmp-master-pass")
ROUND_DIR="$REPO_ROOT/e2e-results/v2-reboot-${ROUND}_$(date -u +%Y%m%d_%H%M%S)"
mkdir -p "$ROUND_DIR"

declare -A CFG=([test-02]=v4-current [test-03]=v5-experimental [test-04]=v8-prod-load [test-05]=v8-prod-load)
aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }
ssh_ec2() { ssh -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR ec2-user@"$ABT_EC2_PUBLIC_IP" "$@"; }

echo "============================================================"
echo " v2 Reboot Round $ROUND  ($(date -u +%H:%M:%S) UTC)"
echo "============================================================"

ssh_ec2 "rm -rf /home/ec2-user/v2rb-${ROUND} && mkdir -p /home/ec2-user/v2rb-${ROUND}"
for c in test-02 test-03 test-04 test-05; do
    source "$STATE_DIR/${c}.env"
    cfg=${CFG[$c]}
    cat <<EOF | ssh_ec2 "bash -s" >/dev/null
mkdir -p /home/ec2-user/v2rb-${ROUND}/${c}
cd /home/ec2-user/aurora-bg-toolkit
DB_ENDPOINT="$ABT_CLUSTER_ENDPOINT" DB_PORT=4488 DB_USER=admin DB_NAME=demo DB_PASSWORD="$TMP_PASSWORD" \\
  TABLE_SUFFIX="ec2_4_v2rb${ROUND}" WRAPPER_VERSION=4.0.0 \\
  nohup java --add-opens java.base/java.lang=ALL-UNNAMED --add-opens java.base/java.lang.reflect=ALL-UNNAMED \\
    -Xmx4g -jar abt-w400.jar configs/${cfg}.yaml \\
    > /home/ec2-user/v2rb-${ROUND}/${c}/ec2_wrapper4.log 2>&1 &
echo \$! > /home/ec2-user/v2rb-${ROUND}/${c}/ec2_wrapper4.pid
EOF
done

sleep 60
for c in test-02 test-03 test-04 test-05; do
    aws_ rds reboot-db-instance --db-instance-identifier "$c-writer" >/dev/null &
done
wait
sleep 90

for c in test-02 test-03 test-04 test-05; do
    pid=$(ssh_ec2 "cat /home/ec2-user/v2rb-${ROUND}/${c}/ec2_wrapper4.pid 2>/dev/null || echo 0")
    [[ "$pid" != "0" ]] && ssh_ec2 "kill $pid 2>/dev/null || true"
done
sleep 5

for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    mkdir -p "$ROUND_DIR/${c}_${cfg}"
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/v2rb-${ROUND}/${c}/ec2_wrapper4.log \
        "$ROUND_DIR/${c}_${cfg}/ec2_wrapper4.log" 2>/dev/null
    cat > "$ROUND_DIR/${c}_${cfg}/meta.json" <<JSON
{"runId": "${c}_${cfg}_v2rb_r${ROUND}", "config": "$cfg", "scenario": "reboot", "round": $ROUND}
JSON
    python3 "$REPO_ROOT/scripts/analyze-stats-gap.py" "$ROUND_DIR/${c}_${cfg}/ec2_wrapper4.log" \
        > "$ROUND_DIR/${c}_${cfg}/stats-gap.json" 2>/dev/null
done

echo "v2 Reboot Round $ROUND results:"
for c in test-02 test-03 test-04 test-05; do
    cfg=${CFG[$c]}
    if [[ -f "$ROUND_DIR/${c}_${cfg}/stats-gap.json" ]]; then
        wmax=$(python3 -c "import json;d=json.load(open('$ROUND_DIR/${c}_${cfg}/stats-gap.json'));print(d['summary']['writeMaxMs'])" 2>/dev/null || echo 0)
        printf "   %-10s %-22s STATS_gap write_max=%6dms\n" "$c" "$cfg" "$wmax"
    fi
done

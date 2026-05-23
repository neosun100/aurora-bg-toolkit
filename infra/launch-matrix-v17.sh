#!/usr/bin/env bash
# launch-matrix.sh — One-shot v17 matrix sweep launcher.
#
# What it does (in order):
#   1. Pre-flight: verify aws/cdk/python/maven, build fat-jar
#   2. Deploy AbtV11NetworkStack (if missing — needed for shared key/SG)
#   3. Deploy AbtV17MatrixRunnerStack (creates t3.small runner EC2 + S3 + SNS)
#   4. Wait for runner EC2 to be SSH-ready (cloud-init complete)
#   5. tar the toolkit + scp to runner
#   6. Install systemd service on runner that runs orchestrate-matrix.py
#   7. start the service
#   8. Print monitoring URLs and exit
#
# After this returns, the user can close the laptop. Matrix runs autonomously
# on AWS for ~10-15h. SNS notifies on each run completion + final completion.
#
# Usage:
#   bash infra/launch-matrix.sh [your-email@example.com]
#
# Idempotency: re-runs are safe. If the runner stack already exists, this
# uploads new code + restarts the systemd service. If matrix-progress.json
# already exists on the runner, the orchestrator picks up where it left off.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# ── Args & defaults ─────────────────────────────────────────────
# Notifications go via Bark to Neo's M5 Max (no email needed).
# SNS topic is still created in the stack as a fallback channel,
# but notifications work without an email subscription.
NOTIFICATION_EMAIL="${1:-${ABT_NOTIFICATION_EMAIL:-}}"
AWS_PROFILE="${AWS_PROFILE:-jiasunm-neo}"
AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_PROFILE AWS_REGION

# ── Load Bark credentials from ~/.env ───────────────────────────
if [[ -f "$HOME/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$HOME/.env"; set +a
fi

if [[ -z "${BARK_PASSWORD:-}" ]] || [[ -z "${BARK_KEY_MAC:-}" ]]; then
    echo "ERROR: BARK_PASSWORD or BARK_KEY_MAC missing from ~/.env"
    echo "  These are required so the AWS-side orchestrator can push notifications"
    echo "  to your M5 Max. Set up bark-push first (see ~/Code/NewMac/.claude/skills/bark-push/SKILL.md)"
    exit 1
fi
BARK_SERVER="${BARK_SERVER:-bark.aws.xin}"
BARK_USERNAME="${BARK_USERNAME:-bark}"
echo "✓ Bark credentials loaded (target: M5 Max key=${BARK_KEY_MAC:0:6}...)"

CYAN='\033[0;36m'; GRN='\033[0;32m'; YEL='\033[0;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GRN}[$(date +%H:%M:%S)] ✓${NC} $*"; }
warn() { echo -e "${YEL}[$(date +%H:%M:%S)] !${NC} $*"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ✗${NC} $*"; }

# ── Step 1: Pre-flight ──────────────────────────────────────────
log "Step 1/8: pre-flight checks"
for cmd in aws cdk python3 java mvn jq tar scp ssh; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        err "Missing tool: $cmd"
        exit 1
    fi
done
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ok "AWS account: $ACCOUNT_ID, region: $AWS_REGION"

# Build fat-jar if missing
if [[ ! -f target/abt-w401.jar ]]; then
    log "Building fat-jar (one-time, ~30s)..."
    mvn -q clean package -DskipTests -Pwrapper-4.1
    cp target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar target/abt-w401.jar
fi
ok "fat-jar present: target/abt-w401.jar ($(du -h target/abt-w401.jar | awk '{print $1}'))"

# ── Step 2: Deploy NetworkStack (if missing) ────────────────────
log "Step 2/8: ensure AbtV11NetworkStack deployed (needed for shared key/SG)"
NET_STATUS="$(aws cloudformation describe-stacks --stack-name AbtV11NetworkStack --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo "MISSING")"
if [[ "$NET_STATUS" == "MISSING" ]] || [[ "$NET_STATUS" == "ROLLBACK_COMPLETE" ]]; then
    log "Deploying AbtV11NetworkStack..."
    cd infra/cdk
    [[ -d .venv ]] || (uv venv .venv && uv pip install -r requirements.txt)
    cdk deploy AbtV11NetworkStack --require-approval never \
        -c "@aws-cdk/aws-ec2:restrictDefaultSecurityGroup=false"
    cd "$REPO_ROOT"
fi
ok "NetworkStack ready ($NET_STATUS)"

# ── Step 3: Deploy MatrixRunnerStack ────────────────────────────
log "Step 3/8: deploy AbtV17MatrixRunnerStack (t3.small + S3 + SNS)"
cd infra/cdk
INCLUDE_MATRIX_RUNNER_V17=1 \
    ABT_NOTIFICATION_EMAIL="$NOTIFICATION_EMAIL" \
    cdk deploy AbtV17MatrixRunnerStack --require-approval never \
        -c "@aws-cdk/aws-ec2:restrictDefaultSecurityGroup=false"
cd "$REPO_ROOT"
ok "MatrixRunnerStack deployed"

# ── Step 4: Collect outputs ─────────────────────────────────────
log "Step 4/8: collect MatrixRunnerStack outputs"
RUNNER_IP="$(aws cloudformation describe-stacks --stack-name AbtV17MatrixRunnerStack \
             --query "Stacks[0].Outputs[?OutputKey=='RunnerPublicIp'].OutputValue" --output text)"
BUCKET="$(aws cloudformation describe-stacks --stack-name AbtV17MatrixRunnerStack \
          --query "Stacks[0].Outputs[?OutputKey=='StateBucketName'].OutputValue" --output text)"
TOPIC_ARN="$(aws cloudformation describe-stacks --stack-name AbtV17MatrixRunnerStack \
             --query "Stacks[0].Outputs[?OutputKey=='TopicArn'].OutputValue" --output text)"
DASHBOARD_URL="$(aws cloudformation describe-stacks --stack-name AbtV17MatrixRunnerStack \
                 --query "Stacks[0].Outputs[?OutputKey=='DashboardUrl'].OutputValue" --output text)"

ok "Runner IP: $RUNNER_IP"
ok "S3 bucket: $BUCKET"
ok "SNS topic: $TOPIC_ARN"

# Pull SSH key from SSM. ALWAYS refresh — the NetworkStack may have been
# re-deployed (e.g. after a v15 destroy + v17 redeploy), in which case the
# KeyPair is new and the cached key is stale → SSH "Permission denied".
KEY_ID="$(aws ec2 describe-key-pairs --filters Name=key-name,Values=abt-v11-key \
          --query 'KeyPairs[0].KeyPairId' --output text)"
KEY_PATH="$REPO_ROOT/infra/state/abt-v11-key.pem"
mkdir -p "$REPO_ROOT/infra/state"
log "Refreshing SSH key from SSM (key id: $KEY_ID)..."
aws ssm get-parameter --name "/ec2/keypair/$KEY_ID" --with-decryption \
    --query 'Parameter.Value' --output text > "$KEY_PATH"
chmod 600 "$KEY_PATH"
ok "SSH key refreshed: $KEY_PATH ($(wc -l < "$KEY_PATH") lines)"

# ── Step 5: Wait for runner SSH-ready ───────────────────────────
log "Step 5/8: wait for runner EC2 cloud-init (up to 5 min)"
SSH_OPTS="-i $KEY_PATH -o StrictHostKeyChecking=no -o LogLevel=ERROR -o ConnectTimeout=10"
for i in {1..30}; do
    if ssh $SSH_OPTS ec2-user@"$RUNNER_IP" \
        "test -f /var/log/abt-v17-runner-userdata.log && grep -q 'user-data complete' /var/log/abt-v17-runner-userdata.log" 2>/dev/null; then
        ok "Runner SSH-ready (cloud-init complete)"
        break
    fi
    if (( i == 30 )); then
        err "Runner never became ready after 5 min"
        exit 2
    fi
    sleep 10
done

# ── Step 6: Upload toolkit ──────────────────────────────────────
log "Step 6/8: upload toolkit to runner"
TARBALL="/tmp/abt-toolkit-$(date +%s).tar.gz"
# Exclude bulky/local stuff (cdk.out, .venv, .git, e2e-results, target/*.jar except abt-w401)
tar --exclude='./infra/cdk/cdk.out' --exclude='./infra/cdk/.venv' \
    --exclude='./.git' --exclude='./e2e-results' --exclude='./ppt' \
    --exclude='./target/test-classes' --exclude='./target/classes' \
    --exclude='./target/generated-sources' --exclude='./target/generated-test-sources' \
    --exclude='./target/maven-archiver' --exclude='./target/maven-status' \
    --exclude='./target/aurora-bg-toolkit.jar' --exclude='./target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar' \
    --exclude='./infra/state' \
    -czf "$TARBALL" -C "$REPO_ROOT" .

scp $SSH_OPTS "$TARBALL" ec2-user@"$RUNNER_IP":/opt/abt/toolkit.tar.gz
ssh $SSH_OPTS ec2-user@"$RUNNER_IP" \
    "rm -rf /opt/abt/aurora-bg-toolkit && \
     mkdir -p /opt/abt/aurora-bg-toolkit && \
     tar -xzf /opt/abt/toolkit.tar.gz -C /opt/abt/aurora-bg-toolkit && \
     mkdir -p /opt/abt/aurora-bg-toolkit/infra/state && \
     mkdir -p /opt/abt/aurora-bg-toolkit/infra/cdk/.venv && \
     cd /opt/abt/aurora-bg-toolkit/infra/cdk && \
     ~/.local/bin/uv venv .venv 2>/dev/null || python3 -m venv .venv && \
     /opt/abt/aurora-bg-toolkit/infra/cdk/.venv/bin/pip install -q -r requirements.txt"
rm -f "$TARBALL"
ok "Toolkit uploaded + dependencies installed"

# Copy SSH key to runner so it can SSH into ClientStack EC2
ssh $SSH_OPTS ec2-user@"$RUNNER_IP" \
    "mkdir -p ~/.ssh && chmod 700 ~/.ssh"
scp $SSH_OPTS "$KEY_PATH" ec2-user@"$RUNNER_IP":/home/ec2-user/.ssh/abt-v11-key.pem
ssh $SSH_OPTS ec2-user@"$RUNNER_IP" \
    "chmod 600 ~/.ssh/abt-v11-key.pem && \
     mkdir -p /opt/abt/aurora-bg-toolkit/infra/state && \
     cp ~/.ssh/abt-v11-key.pem /opt/abt/aurora-bg-toolkit/infra/state/abt-v11-key.pem && \
     chmod 600 /opt/abt/aurora-bg-toolkit/infra/state/abt-v11-key.pem"
ok "SSH key copied to runner state dir"

# ── Step 7: Install systemd service ─────────────────────────────
log "Step 7/8: install systemd service"
SERVICE_FILE="/tmp/abt-v17-matrix.service"
cat > "$SERVICE_FILE" << SVCEOF
[Unit]
Description=Aurora BG Toolkit v17 Matrix Sweep
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/opt/abt/aurora-bg-toolkit
Environment=AWS_REGION=$AWS_REGION
Environment=ABT_STATE_BUCKET=$BUCKET
Environment=ABT_TOPIC_ARN=$TOPIC_ARN
Environment=ABT_MATRIX_SPEC=/opt/abt/aurora-bg-toolkit/infra/matrix-spec-v17.yaml
Environment=BARK_SERVER=$BARK_SERVER
Environment=BARK_USERNAME=$BARK_USERNAME
Environment=BARK_PASSWORD=$BARK_PASSWORD
Environment=BARK_KEY_MAC=$BARK_KEY_MAC
Environment=PATH=/usr/local/bin:/usr/bin:/bin:/home/ec2-user/.local/bin
ExecStart=/usr/bin/python3 /opt/abt/aurora-bg-toolkit/infra/orchestrate-matrix.py
Restart=on-failure
RestartSec=60
StandardOutput=append:/var/log/abt-v17-matrix.log
StandardError=append:/var/log/abt-v17-matrix.log

[Install]
WantedBy=multi-user.target
SVCEOF

# This file is uploaded to runner via scp, then moved by sudo. systemd
# unit must be 0644. Bark credentials are sensitive, so we restrict
# the file to root-only on the runner side.
scp $SSH_OPTS "$SERVICE_FILE" ec2-user@"$RUNNER_IP":/tmp/abt-v17-matrix.service
ssh $SSH_OPTS ec2-user@"$RUNNER_IP" \
    "sudo mv /tmp/abt-v17-matrix.service /etc/systemd/system/ && \
     sudo chmod 0640 /etc/systemd/system/abt-v17-matrix.service && \
     sudo chown root:root /etc/systemd/system/abt-v17-matrix.service && \
     sudo systemctl daemon-reload && \
     sudo systemctl enable abt-v17-matrix.service"
rm -f "$SERVICE_FILE"
ok "Systemd service installed (with Bark credentials, mode 0640)"

# ── Step 8: Start the service ───────────────────────────────────
log "Step 8/8: start abt-v17-matrix.service on runner"
ssh $SSH_OPTS ec2-user@"$RUNNER_IP" \
    "sudo systemctl restart abt-v17-matrix.service && \
     sleep 3 && sudo systemctl status abt-v17-matrix.service --no-pager | head -15"
ok "Service started"

# ── Done ────────────────────────────────────────────────────────
echo
echo -e "${GRN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GRN}║  Matrix sweep is running autonomously on AWS.                   ║${NC}"
echo -e "${GRN}║  You can close this laptop now. ☕                              ║${NC}"
echo -e "${GRN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo
echo "Monitoring (any machine, any time):"
echo "  • Terminal status:   bash scripts/v16-check.sh"
echo "  • Open dashboard:    bash scripts/v16-check.sh --open"
echo "  • Watch (30s):       bash scripts/v16-check.sh --watch"
echo "  • S3 progress:       aws s3 cp s3://$BUCKET/matrix-progress.json - | jq ."
echo "  • Runner SSH:        ssh -i $KEY_PATH ec2-user@$RUNNER_IP"
echo "  • Live log:          ssh ... 'sudo journalctl -u abt-v17-matrix -f'"
echo
echo "Notifications:"
echo "  • Bark → M5 Max (key=${BARK_KEY_MAC:0:6}...)"
echo "    Sound levels: passive(start) | active(progress) | timeSensitive(complete) | critical(fail)"
if [[ -n "$NOTIFICATION_EMAIL" ]]; then
    echo "  • Email fallback: $NOTIFICATION_EMAIL (check spam + click SNS confirm link first)"
fi
echo
echo "Estimated wall time: ~10-15h (smoke 30min + 6 runs × 1.5h + cleanup)"
echo "Estimated AWS cost:  ~\$170 total"
echo
echo "Stop / restart matrix:"
echo "  ssh -i $KEY_PATH ec2-user@$RUNNER_IP 'sudo systemctl stop abt-v17-matrix'"
echo "  ssh -i $KEY_PATH ec2-user@$RUNNER_IP 'sudo systemctl restart abt-v17-matrix'"
echo

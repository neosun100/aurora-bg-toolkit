#!/usr/bin/env bash
# orchestrate-v10-master.sh — end-to-end orchestrator for v10-production.
#
# One command: bootstrap → cluster → BG prereqs → EC2 → 30 measurements →
# analyze → dashboard → final report → teardown.
#
# Resumability:
#   * State file: infra/state/v10-progress.json
#   * Each phase has status: pending|running|done|failed
#   * On launch: skip done; reset running→pending; retry failed up to 3 times
#   * Run again with same args = resumes from last checkpoint
#
# Usage:
#   bash infra/orchestrate-v10-master.sh                  # normal launch (resume if state exists)
#   FRESH=1 bash infra/orchestrate-v10-master.sh          # ignore state, start over (also re-creates AWS resources)
#   SKIP_PHASES=TEARDOWN bash infra/...master.sh          # don't tear down at end (manual inspection)
#   ONLY_PHASES=TEST_BG_R7 bash infra/...master.sh        # run a single phase (debug)
#
# Status: bash scripts/v10-status.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
STATE_DIR="$INFRA_DIR/state"
SCRIPTS_DIR="$REPO_ROOT/scripts"
mkdir -p "$STATE_DIR"

PROGRESS="$STATE_DIR/v10-progress.json"
LOG_FILE="$STATE_DIR/v10-master.log"
LOCK_FILE="$STATE_DIR/v10-master.lock"

CLUSTER_NAME="test-v10"
CONFIG_NAME="v10-final"
ROUNDS=10

export AWS_PROFILE="${AWS_PROFILE:-jiasunm-neo}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

# ──────────────── Logging ────────────────
log() {
    local level="$1"; shift
    local msg="$*"
    local ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "[$ts] [$level] $msg" | tee -a "$LOG_FILE" >&2
}
info()  { log INFO  "$*"; }
warn()  { log WARN  "$*"; }
error() { log ERROR "$*"; }

# ──────────────── Lock (avoid double-launch) ────────────────
acquire_lock() {
    if [[ -f "$LOCK_FILE" ]]; then
        local pid=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            error "Master orchestrator already running (pid=$pid). Refusing to start a second."
            error "If you're sure it's not running, remove $LOCK_FILE and re-launch."
            exit 1
        fi
    fi
    echo $$ > "$LOCK_FILE"
    trap "rm -f '$LOCK_FILE'" EXIT
}

# ──────────────── State management ────────────────
init_progress() {
    if [[ "${FRESH:-0}" == "1" ]] || [[ ! -f "$PROGRESS" ]]; then
        info "Initializing progress state at $PROGRESS"
        cat > "$PROGRESS" <<EOF
{
  "experiment": "v10-production",
  "config_file": "configs/${CONFIG_NAME}.yaml",
  "started_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "current_phase": null,
  "phases": {},
  "errors": []
}
EOF
    fi
}

phase_status() {
    local p="$1"
    python3 -c "
import json
d=json.load(open('$PROGRESS'))
print(d.get('phases',{}).get('$p',{}).get('status','pending'))
"
}

phase_attempts() {
    local p="$1"
    python3 -c "
import json
d=json.load(open('$PROGRESS'))
print(d.get('phases',{}).get('$p',{}).get('attempts',0))
"
}

phase_set() {
    local p="$1" status="$2"
    local extra_kvs=""
    if [[ $# -gt 2 ]]; then
        # all remaining args are JSON kv pairs key=value (string-only)
        shift 2
        for kv in "$@"; do
            local k="${kv%%=*}"
            local v="${kv#*=}"
            extra_kvs+="    d['phases']['$p']['$k'] = '$v'
"
        done
    fi
    python3 - <<EOF
import json, datetime
d = json.load(open('$PROGRESS'))
phases = d.setdefault('phases', {})
ph = phases.setdefault('$p', {})
old_status = ph.get('status')
ph['status'] = '$status'
ts = datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z'
if '$status' == 'running' and 'started_at' not in ph:
    ph['started_at'] = ts
    ph['attempts'] = ph.get('attempts', 0) + 1
if '$status' in ('done', 'failed'):
    ph['ended_at'] = ts
    if 'started_at' in ph:
        try:
            s = datetime.datetime.fromisoformat(ph['started_at'].rstrip('Z'))
            e = datetime.datetime.fromisoformat(ts.rstrip('Z'))
            ph['duration_s'] = int((e - s).total_seconds())
        except Exception:
            pass
$extra_kvs
d['current_phase'] = '$p' if '$status' in ('running', 'pending') else None
json.dump(d, open('$PROGRESS', 'w'), indent=2)
EOF
}

phase_record_metric() {
    local p="$1" metric="$2" value="$3"
    python3 - <<EOF
import json
d = json.load(open('$PROGRESS'))
d['phases'].setdefault('$p', {})['$metric'] = $value
json.dump(d, open('$PROGRESS', 'w'), indent=2)
EOF
}

phase_record_error() {
    local p="$1" err="$2"
    python3 - <<EOF
import json, datetime
d = json.load(open('$PROGRESS'))
d.setdefault('errors', []).append({
    'phase': '$p',
    'timestamp': datetime.datetime.utcnow().isoformat(timespec='seconds') + 'Z',
    'error': """$err"""
})
json.dump(d, open('$PROGRESS', 'w'), indent=2)
EOF
}

# ──────────────── Phase runner with retry ────────────────
run_phase() {
    local phase_name="$1"
    local fn="$2"

    if [[ -n "${ONLY_PHASES:-}" ]] && [[ "$ONLY_PHASES" != *"$phase_name"* ]]; then
        return 0
    fi
    if [[ -n "${SKIP_PHASES:-}" ]] && [[ "$SKIP_PHASES" == *"$phase_name"* ]]; then
        info "SKIP $phase_name (per SKIP_PHASES)"
        phase_set "$phase_name" "done" "note=skipped-by-user"
        return 0
    fi

    local current=$(phase_status "$phase_name")
    case "$current" in
        done)
            info "skip $phase_name (already done)"
            return 0
            ;;
        running)
            warn "$phase_name was 'running' (interrupted previously); resetting to pending"
            phase_set "$phase_name" "pending"
            ;;
    esac

    local attempts=$(phase_attempts "$phase_name")
    if [[ $attempts -ge 3 ]]; then
        error "$phase_name has already failed 3 times; refusing to retry. Run with SKIP_PHASES=$phase_name to bypass."
        return 1
    fi

    info "▶ START $phase_name (attempt $((attempts + 1)))"
    phase_set "$phase_name" "running"

    # eval allows the caller to pass "phase_BG_R 5" as a string
    if eval "$fn"; then
        info "✓ DONE  $phase_name"
        phase_set "$phase_name" "done"
        return 0
    else
        local rc=$?
        error "✗ FAIL  $phase_name (rc=$rc)"
        phase_set "$phase_name" "failed"
        phase_record_error "$phase_name" "exit code $rc; see $LOG_FILE"
        return $rc
    fi
}

# ──────────────── Phase implementations ────────────────

phase_PRECHECK() {
    info "Checking dependencies..."
    local missing=0
    for cmd in aws mvn python3 java jq ssh scp; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            error "  missing: $cmd"
            missing=1
        else
            info "  found:   $cmd ($($cmd --version 2>&1 | head -1 || echo ok))"
        fi
    done
    if [[ $missing -ne 0 ]]; then
        error "Install missing dependencies and re-run."
        return 1
    fi
    if ! aws_ sts get-caller-identity >/dev/null 2>&1; then
        error "AWS credentials don't work for profile $AWS_PROFILE region $AWS_REGION. Run 'mwinit'."
        return 1
    fi
    info "  AWS account: $(aws_ sts get-caller-identity --query Account --output text)"
    return 0
}

phase_BUILD() {
    info "Building fat-jars (wrapper 4.0.1)..."
    cd "$REPO_ROOT"
    if [[ ! -f target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar ]]; then
        bash scripts/install-local-wrapper-jars.sh >> "$LOG_FILE" 2>&1
    fi
    mvn -q clean package -DskipTests -Pwrapper-4.1 >> "$LOG_FILE" 2>&1 || return 1
    cp target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar target/abt-w401.jar
    info "  built: target/abt-w401.jar ($(du -h target/abt-w401.jar | awk '{print $1}'))"
    return 0
}

phase_BOOTSTRAP() {
    info "Running 00-bootstrap.sh ..."
    cd "$REPO_ROOT"
    bash infra/00-bootstrap.sh >> "$LOG_FILE" 2>&1 || return 1
    return 0
}

phase_CLUSTER_CREATE() {
    info "Creating Aurora cluster $CLUSTER_NAME..."
    cd "$REPO_ROOT"
    bash infra/10-create-cluster.sh "$CLUSTER_NAME" >> "$LOG_FILE" 2>&1 || return 1
    info "  Waiting for cluster + writer + reader to become available..."
    aws_ rds wait db-cluster-available --db-cluster-identifier "$CLUSTER_NAME" || return 1
    aws_ rds wait db-instance-available --db-instance-identifier "$CLUSTER_NAME-writer" || return 1
    aws_ rds wait db-instance-available --db-instance-identifier "$CLUSTER_NAME-reader" || return 1

    # Capture password from secrets manager into .tmp-master-pass (used by sub-orchestrators)
    info "  Fetching master password from Secrets Manager..."
    local secret_arn=$(aws_ rds describe-db-clusters --db-cluster-identifier "$CLUSTER_NAME" \
        --query 'DBClusters[0].MasterUserSecret.SecretArn' --output text)
    aws_ secretsmanager get-secret-value --secret-id "$secret_arn" \
        --query 'SecretString' --output text \
        | python3 -c 'import sys,json; print(json.load(sys.stdin)["password"])' \
        > "$STATE_DIR/.tmp-master-pass"
    chmod 600 "$STATE_DIR/.tmp-master-pass"
    info "  Recording cluster state..."
    bash infra/11-record-cluster-state.sh "$CLUSTER_NAME" >> "$LOG_FILE" 2>&1 || return 1
    return 0
}

phase_BG_PREREQS() {
    info "Applying BG prerequisites (binlog params + reboot)..."
    cd "$REPO_ROOT"
    bash infra/05-enable-bg-prereqs.sh >> "$LOG_FILE" 2>&1 || return 1
    return 0
}

phase_EC2_SETUP() {
    info "Creating EC2 c6i.2xlarge runner..."
    cd "$REPO_ROOT"
    bash infra/20-create-ec2.sh >> "$LOG_FILE" 2>&1 || return 1
    source "$STATE_DIR/bootstrap.env"
    source "$STATE_DIR/ec2.env"
    info "  EC2 ready: $ABT_EC2_PUBLIC_IP"

    info "  Waiting for cloud-init / SSH ready (~60s)..."
    for i in {1..30}; do
        if ssh -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o LogLevel=ERROR \
            ec2-user@"$ABT_EC2_PUBLIC_IP" "test -d /home/ec2-user/aurora-bg-toolkit" 2>/dev/null; then
            info "  EC2 SSH ready"
            break
        fi
        sleep 5
    done

    info "  Uploading fat-jar + configs..."
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        target/abt-w401.jar \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/aurora-bg-toolkit/abt-w401.jar >> "$LOG_FILE" 2>&1 || return 1
    scp -i "$ABT_KEY_FILE" -o StrictHostKeyChecking=no -o LogLevel=ERROR \
        configs/v10-final.yaml configs/v4-current.yaml \
        ec2-user@"$ABT_EC2_PUBLIC_IP":/home/ec2-user/aurora-bg-toolkit/configs/ >> "$LOG_FILE" 2>&1 || return 1
    info "  Files uploaded"
    return 0
}

phase_BG_R() {
    local round="$1"
    info "BG round $round starting..."
    cd "$REPO_ROOT"

    # ensure cluster parameter group is in-sync with writer before any BG op
    # (RDS metadata propagation lag can be 1-2 min after a writer reboot)
    info "  Checking cluster-pg in-sync state..."
    local pg_status="?"
    for i in $(seq 1 20); do
        pg_status=$(aws_ rds describe-db-clusters --db-cluster-identifier "$CLUSTER_NAME" \
            --query 'DBClusters[0].DBClusterMembers[?IsClusterWriter==`true`].DBClusterParameterGroupStatus|[0]' \
            --output text 2>/dev/null || echo "?")
        if [[ "$pg_status" == "in-sync" ]]; then
            info "  cluster-pg in-sync ✓"
            break
        fi
        info "  ... cluster-pg=$pg_status (attempt $i/20, waiting 15s)"
        if [[ "$pg_status" == "pending-reboot" ]]; then
            info "  → rebooting writer to clear pending-reboot"
            aws_ rds reboot-db-instance --db-instance-identifier "$CLUSTER_NAME-writer" >/dev/null 2>&1 || true
            aws_ rds wait db-instance-available --db-instance-identifier "$CLUSTER_NAME-writer" 2>/dev/null || true
        fi
        sleep 15
    done
    if [[ "$pg_status" != "in-sync" ]]; then
        error "cluster-pg never reached in-sync after 5 min; aborting round"
        return 1
    fi

    # Each round needs its own fresh BG (BG can only be switched-over once)
    local current_bg=""
    if [[ -f "$STATE_DIR/test-v10.bg.env" ]]; then
        current_bg=$(grep ABT_BG_ID "$STATE_DIR/test-v10.bg.env" | cut -d'"' -f2)
    fi
    if [[ -n "$current_bg" ]]; then
        local bg_status=$(aws_ rds describe-blue-green-deployments \
            --blue-green-deployment-identifier "$current_bg" \
            --query 'BlueGreenDeployments[0].Status' --output text 2>/dev/null || echo "MISSING")
        if [[ "$bg_status" == "SWITCHOVER_COMPLETED" ]]; then
            info "  Previous BG ($current_bg) already used; deleting + creating fresh..."
            aws_ rds delete-blue-green-deployment --blue-green-deployment-identifier "$current_bg" --delete-target >/dev/null 2>&1 || true
            sleep 30
            # Force-clean any -old* clusters/instances
            local OLD=$(aws_ rds describe-db-instances --query 'DBInstances[?contains(DBInstanceIdentifier, `-old`)].DBInstanceIdentifier' --output text 2>/dev/null || true)
            for inst in $OLD; do
                aws_ rds delete-db-instance --db-instance-identifier "$inst" --skip-final-snapshot --delete-automated-backups >/dev/null 2>&1 || true
            done
            for inst in $OLD; do aws_ rds wait db-instance-deleted --db-instance-identifier "$inst" 2>/dev/null || true; done
            local OLDC=$(aws_ rds describe-db-clusters --query 'DBClusters[?contains(DBClusterIdentifier, `-old`)].DBClusterIdentifier' --output text 2>/dev/null || true)
            for cl in $OLDC; do aws_ rds delete-db-cluster --db-cluster-identifier "$cl" --skip-final-snapshot >/dev/null 2>&1 || true; done
            for cl in $OLDC; do aws_ rds wait db-cluster-deleted --db-cluster-identifier "$cl" 2>/dev/null || true; done
            current_bg=""
        elif [[ "$bg_status" == "AVAILABLE" ]]; then
            info "  Existing BG ($current_bg) is AVAILABLE; reusing"
        else
            info "  Existing BG status: $bg_status; will wait or recreate"
        fi
    fi

    if [[ -z "$current_bg" ]] || [[ "$bg_status" == "MISSING" ]] || [[ "$bg_status" == "SWITCHOVER_COMPLETED" ]]; then
        info "  Creating fresh BG deployment..."
        bash infra/30-create-bg-deployment.sh "$CLUSTER_NAME" >> "$LOG_FILE" 2>&1 || return 1
    fi

    info "  Waiting for BG to reach AVAILABLE (max 30 min)..."
    for i in $(seq 1 60); do
        source "$STATE_DIR/test-v10.bg.env"
        local s=$(aws_ rds describe-blue-green-deployments \
            --blue-green-deployment-identifier "$ABT_BG_ID" \
            --query 'BlueGreenDeployments[0].Status' --output text 2>/dev/null || echo "?")
        if [[ "$s" == "AVAILABLE" ]]; then
            info "  BG AVAILABLE ($i × 30s waited)"
            break
        fi
        info "  ... BG status=$s (attempt $i)"
        sleep 30
    done
    [[ "$s" != "AVAILABLE" ]] && { error "BG never became AVAILABLE in 30 min"; return 1; }

    bash infra/orchestrate-bg-v10.sh "$round" >> "$LOG_FILE" 2>&1 || return 1

    # Capture metric to checkpoint
    local last_dir=$(ls -td "$REPO_ROOT/e2e-results/v10-bg-${round}_"* 2>/dev/null | head -1)
    if [[ -n "$last_dir" ]] && [[ -f "$last_dir/test-v10_v10-final/writeMaxMs" ]]; then
        local wmax=$(cat "$last_dir/test-v10_v10-final/writeMaxMs")
        local rmax=$(cat "$last_dir/test-v10_v10-final/readMaxMs")
        phase_record_metric "TEST_BG_R$round" "writeMaxMs" "$wmax"
        phase_record_metric "TEST_BG_R$round" "readMaxMs" "$rmax"
    fi
    return 0
}

phase_FO_R() {
    local round="$1"
    info "Failover round $round starting..."
    cd "$REPO_ROOT"
    bash infra/orchestrate-failover-v10.sh "$round" >> "$LOG_FILE" 2>&1 || return 1

    local last_dir=$(ls -td "$REPO_ROOT/e2e-results/v10-failover-${round}_"* 2>/dev/null | head -1)
    if [[ -n "$last_dir" ]] && [[ -f "$last_dir/test-v10_v10-final/writeMaxMs" ]]; then
        local wmax=$(cat "$last_dir/test-v10_v10-final/writeMaxMs")
        local rmax=$(cat "$last_dir/test-v10_v10-final/readMaxMs")
        phase_record_metric "TEST_FO_R$round" "writeMaxMs" "$wmax"
        phase_record_metric "TEST_FO_R$round" "readMaxMs" "$rmax"
    fi
    return 0
}

phase_RB_R() {
    local round="$1"
    info "Reboot round $round starting..."
    cd "$REPO_ROOT"
    bash infra/orchestrate-reboot-v10.sh "$round" >> "$LOG_FILE" 2>&1 || return 1

    local last_dir=$(ls -td "$REPO_ROOT/e2e-results/v10-reboot-${round}_"* 2>/dev/null | head -1)
    if [[ -n "$last_dir" ]] && [[ -f "$last_dir/test-v10_v10-final/writeMaxMs" ]]; then
        local wmax=$(cat "$last_dir/test-v10_v10-final/writeMaxMs")
        local rmax=$(cat "$last_dir/test-v10_v10-final/readMaxMs")
        phase_record_metric "TEST_RB_R$round" "writeMaxMs" "$wmax"
        phase_record_metric "TEST_RB_R$round" "readMaxMs" "$rmax"
    fi
    return 0
}

phase_ANALYZE() {
    info "Aggregating v10 measurements..."
    cd "$REPO_ROOT"
    python3 scripts/v10-extract-data.py >> "$LOG_FILE" 2>&1 || return 1
    info "  v10 dashboard data: dashboard/data/v10-only.json"
    return 0
}

phase_REPORT() {
    info "Generating final report..."
    cd "$REPO_ROOT"
    python3 scripts/v10-generate-report.py >> "$LOG_FILE" 2>&1 || return 1
    info "  Report: docs/REPORTS/2026-05-17-v10-production.md"
    return 0
}

phase_TEARDOWN() {
    info "Tearing down all v10 AWS resources..."
    cd "$REPO_ROOT"
    bash infra/99-teardown.sh >> "$LOG_FILE" 2>&1 || return 1
    info "  Teardown complete"
    return 0
}

# ──────────────── Main ────────────────
main() {
    info "============================================================"
    info " v10-production master orchestrator starting"
    info "  profile: $AWS_PROFILE   region: $AWS_REGION"
    info "  cluster: $CLUSTER_NAME   config: $CONFIG_NAME   rounds: $ROUNDS"
    info "  state:   $PROGRESS"
    info "  log:     $LOG_FILE"
    info "============================================================"

    acquire_lock
    init_progress

    # Setup phases
    run_phase PRECHECK         phase_PRECHECK         || exit 1
    run_phase BUILD            phase_BUILD            || exit 1
    run_phase BOOTSTRAP        phase_BOOTSTRAP        || exit 1
    run_phase CLUSTER_CREATE   phase_CLUSTER_CREATE   || exit 1
    run_phase BG_PREREQS       phase_BG_PREREQS       || exit 1
    run_phase EC2_SETUP        phase_EC2_SETUP        || exit 1

    # Test phases
    for r in $(seq 1 $ROUNDS); do
        run_phase "TEST_BG_R$r" "phase_BG_R $r" || warn "BG round $r failed; continuing"
    done
    for r in $(seq 1 $ROUNDS); do
        run_phase "TEST_FO_R$r" "phase_FO_R $r" || warn "FO round $r failed; continuing"
    done
    for r in $(seq 1 $ROUNDS); do
        run_phase "TEST_RB_R$r" "phase_RB_R $r" || warn "RB round $r failed; continuing"
    done

    # Wrap-up phases
    run_phase ANALYZE          phase_ANALYZE          || warn "analyze failed"
    run_phase REPORT           phase_REPORT           || warn "report failed"
    run_phase TEARDOWN         phase_TEARDOWN         || warn "teardown failed"

    info "============================================================"
    info " v10-production orchestrator complete."
    info " View dashboard: open dashboard/index.html"
    info " View report:    docs/REPORTS/2026-05-17-v10-production.md"
    info "============================================================"
}

main "$@"

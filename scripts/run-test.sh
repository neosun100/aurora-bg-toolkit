#!/usr/bin/env bash
# run-test.sh — single, configuration-driven launcher for the Aurora BG toolkit.
#
# Replaces the original engagement's 5 separate start_*.sh files. Each test
# round used to need its own copy of the script; now one script handles all
# configurations via the YAML files in configs/.
#
# What this does:
#   1. Validate required env vars
#   2. Build the fat-jar with the chosen wrapper version
#   3. Launch one or more Java processes in parallel, one per (config, wrapper)
#      combination, all writing logs to a unique run directory
#   4. Print the run directory path so the operator can stream logs / stop later
#
# Usage:
#   ./scripts/run-test.sh \
#       --endpoint test-04.cluster-xxx.us-east-1.rds.amazonaws.com \
#       --config v4-current \
#       --wrappers 3.3.0,4.0.0
#
# Required environment:
#   DB_PASSWORD       database password (read from Secrets Manager in CI)
#
# Optional environment:
#   DB_USER           default: admin
#   DB_NAME           default: demo
#   DB_PORT           default: 4488 (matches the customer's environment)
#   PROCS_PER_WRAPPER default: 1   (set to 2+ to mimic EC2+EKS dual deployment)
#   LOG_ROOT          default: e2e-results/
#   ROUND             default: 1   (recorded in meta.json)
#   SCENARIO          default: blue-green   (also recorded in meta.json)

set -euo pipefail

# ─── argument parsing ────────────────────────────────────────────────────────
ENDPOINT=""
CONFIG=""
WRAPPERS="4.0.0"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --endpoint) ENDPOINT="$2"; shift 2 ;;
        --config)   CONFIG="$2";   shift 2 ;;
        --wrappers) WRAPPERS="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *) echo "ERROR: unknown arg $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$ENDPOINT" || -z "$CONFIG" ]]; then
    echo "ERROR: --endpoint and --config are required" >&2
    echo "       try $0 --help" >&2
    exit 2
fi
if [[ -z "${DB_PASSWORD:-}" ]]; then
    echo "ERROR: DB_PASSWORD environment variable is not set" >&2
    exit 2
fi

# ─── derive paths and metadata ───────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$REPO_ROOT/configs/${CONFIG}.yaml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: config not found: $CONFIG_FILE" >&2
    exit 1
fi

DB_USER="${DB_USER:-admin}"
DB_NAME="${DB_NAME:-demo}"
DB_PORT="${DB_PORT:-4488}"
PROCS_PER_WRAPPER="${PROCS_PER_WRAPPER:-1}"
LOG_ROOT="${LOG_ROOT:-$REPO_ROOT/e2e-results}"
ROUND="${ROUND:-1}"
SCENARIO="${SCENARIO:-blue-green}"

CLUSTER_TAG="$(echo "$ENDPOINT" | cut -d. -f1)"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
RUN_DIR="$LOG_ROOT/${CLUSTER_TAG}_${CONFIG}_${TIMESTAMP}"
mkdir -p "$RUN_DIR"

# meta.json so analyze-logs.py / compare-runs.py have rich provenance
cat > "$RUN_DIR/meta.json" <<EOF
{
  "runId": "$(basename "$RUN_DIR")",
  "config": "$CONFIG",
  "scenario": "$SCENARIO",
  "round": $ROUND,
  "endpoint": "$ENDPOINT",
  "scenarioStartedAt": "$(date -u +%Y-%m-%dT%H:%M:%S)",
  "wrappers": "$WRAPPERS",
  "host": "$(hostname)"
}
EOF

# ─── build a fat-jar per wrapper version ────────────────────────────────────
build_wrapper() {
    local v="$1"
    local short
    short="$(echo "$v" | tr -d '.')"
    local profile=""
    case "$v" in
        3.3.0) profile="-Pwrapper-3.3" ;;
        4.0.0) profile="" ;;   # default
        4.0.1) profile="-Pwrapper-4.1" ;;
        2.6.0) profile="-Pwrapper-mvncentral" ;;
        *)
            echo "ERROR: unsupported wrapper version $v" >&2; exit 1 ;;
    esac
    local out="$REPO_ROOT/target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all-w${short}.jar"
    if [[ ! -f "$out" ]]; then
        echo "  building wrapper $v ..."
        ( cd "$REPO_ROOT" && mvn -q -B clean package -DskipITs $profile )
        cp "$REPO_ROOT/target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar" "$out"
    fi
    echo "$out"
}

# ─── launch java processes ──────────────────────────────────────────────────
echo "============================================================"
echo " Aurora BG Toolkit — run launcher"
echo "------------------------------------------------------------"
echo "  endpoint:  $ENDPOINT"
echo "  config:    $CONFIG"
echo "  scenario:  $SCENARIO  round=$ROUND"
echo "  wrappers:  $WRAPPERS"
echo "  procs/w:   $PROCS_PER_WRAPPER"
echo "  log dir:   $RUN_DIR"
echo "============================================================"

IFS=',' read -ra WRAPPER_LIST <<< "$WRAPPERS"
for w in "${WRAPPER_LIST[@]}"; do
    JAR="$(build_wrapper "$w")"
    short="$(echo "$w" | tr -d '.')"
    for ((i = 1; i <= PROCS_PER_WRAPPER; i++)); do
        suffix="$(echo "$w" | cut -c1)"   # 3 / 4 — match legacy log naming
        if [[ $PROCS_PER_WRAPPER -gt 1 ]]; then
            suffix="${suffix}_p${i}"
        fi
        log_name="ec2_wrapper${suffix}.log"
        pid_file="$RUN_DIR/ec2_wrapper${suffix}.pid"
        echo ">> launching wrapper $w as $log_name"
        env DB_ENDPOINT="$ENDPOINT" DB_PORT="$DB_PORT" DB_NAME="$DB_NAME" \
            DB_USER="$DB_USER" DB_PASSWORD="$DB_PASSWORD" \
            TABLE_SUFFIX="ec2_${suffix}" WRAPPER_VERSION="$w" \
            java -jar "$JAR" "$CONFIG_FILE" \
                > "$RUN_DIR/$log_name" 2>&1 &
        echo "$!" > "$pid_file"
        echo "   pid $(cat "$pid_file") -> $log_name"
    done
done

echo
echo "All processes launched. Trigger your scenario when ready, then stop with:"
echo "    ./scripts/stop-test.sh $RUN_DIR"
echo
echo "Logs:"
ls -la "$RUN_DIR"/*.log

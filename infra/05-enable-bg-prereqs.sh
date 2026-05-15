#!/usr/bin/env bash
# 05-enable-bg-prereqs.sh — apply Blue/Green prerequisites to all 5 test clusters.
#
# Aurora's Blue/Green Deployments require:
#   * binlog_format = ROW
#   * binlog_row_image = FULL
#   * aurora_enhanced_binlog = 1
#   * binlog_row_metadata = FULL
#   * binlog_backup = 1
# These are static cluster parameters; applying them requires a custom
# cluster parameter group + a cluster reboot.
#
# This script:
#   1. Creates a shared parameter group `abt-aurora-mysql8-bg` if missing
#   2. Sets the required parameter values
#   3. Attaches it to every cluster prefixed `test-`
#   4. Reboots the writer of each cluster (so the new params take effect)

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

PG_NAME="abt-aurora-mysql8-bg"
PG_FAMILY="aurora-mysql8.0"

echo "[1] Ensure cluster parameter group $PG_NAME exists ..."
if ! aws_ rds describe-db-cluster-parameter-groups --db-cluster-parameter-group-name "$PG_NAME" >/dev/null 2>&1; then
    aws_ rds create-db-cluster-parameter-group \
        --db-cluster-parameter-group-name "$PG_NAME" \
        --db-parameter-group-family "$PG_FAMILY" \
        --description "Aurora BG Toolkit -- enables logical replication for BG Deployments" \
        --tags Key=project,Value=aurora-bg-toolkit >/dev/null
    echo "    created"
else
    echo "    already exists"
fi

echo "[2] Set required parameters ..."
# Enable enhanced binlog AND disable conflicting flags in a SINGLE call so
# RDS validates the consistent end-state instead of intermediate steps.
aws_ rds modify-db-cluster-parameter-group --db-cluster-parameter-group-name "$PG_NAME" \
    --parameters \
        "ParameterName=aurora_enhanced_binlog,ParameterValue=1,ApplyMethod=pending-reboot" \
        "ParameterName=binlog_backup,ParameterValue=0,ApplyMethod=pending-reboot" \
        "ParameterName=binlog_replication_globaldb,ParameterValue=0,ApplyMethod=pending-reboot" \
        "ParameterName=binlog_format,ParameterValue=ROW,ApplyMethod=pending-reboot" \
        "ParameterName=binlog_row_image,ParameterValue=FULL,ApplyMethod=pending-reboot" \
        "ParameterName=binlog_row_metadata,ParameterValue=FULL,ApplyMethod=pending-reboot" \
    >/dev/null
echo "    parameters set"

echo "[3] Attach $PG_NAME to all test-* clusters ..."
CLUSTERS=$(aws_ rds describe-db-clusters \
    --query 'DBClusters[?starts_with(DBClusterIdentifier, `test-`)].DBClusterIdentifier' --output text)
for c in $CLUSTERS; do
    current=$(aws_ rds describe-db-clusters --db-cluster-identifier "$c" \
        --query 'DBClusters[0].DBClusterParameterGroup' --output text)
    if [[ "$current" == "$PG_NAME" ]]; then
        echo "    $c: already attached"
        continue
    fi
    aws_ rds modify-db-cluster --db-cluster-identifier "$c" \
        --db-cluster-parameter-group-name "$PG_NAME" \
        --apply-immediately >/dev/null
    echo "    $c: attached (was $current)"
done

echo "[4] Reboot writer of each cluster (required to apply the new params) ..."
WRITERS=$(aws_ rds describe-db-instances \
    --query 'DBInstances[?starts_with(DBInstanceIdentifier, `test-`) && ends_with(DBInstanceIdentifier, `-writer`)].DBInstanceIdentifier' --output text)
for w in $WRITERS; do
    aws_ rds reboot-db-instance --db-instance-identifier "$w" >/dev/null
    echo "    $w: reboot initiated"
done

echo
echo "Wait for writers to come back up:"
for w in $WRITERS; do
    aws_ rds wait db-instance-available --db-instance-identifier "$w"
    echo "    $w: available"
done

echo
echo "BG prerequisites applied. You can now run 30-create-bg-deployment.sh."

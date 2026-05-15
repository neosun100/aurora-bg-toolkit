#!/usr/bin/env bash
# 99-teardown.sh — destroy ALL Aurora BG Toolkit resources in this account/region
#
# Removes (idempotent):
#   * All Blue/Green Deployments tagged project=aurora-bg-toolkit
#   * All db instances tagged project=aurora-bg-toolkit
#   * All db clusters tagged project=aurora-bg-toolkit
#   * The shared security group
#   * The DB subnet group
#   * The EC2 key pair
#   * Local state dir
#
# Use with care! This is destructive.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"

if [[ -f "$STATE_DIR/bootstrap.env" ]]; then
    source "$STATE_DIR/bootstrap.env"
else
    export AWS_PROFILE="${AWS_PROFILE:-jiasunm-neo}"
    export AWS_REGION="${AWS_REGION:-us-east-1}"
fi
aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

echo "============================================================"
echo " Tearing down all Aurora BG Toolkit resources"
echo "  profile: $AWS_PROFILE   region: $AWS_REGION"
echo "============================================================"

# 1) Delete Blue/Green Deployments
echo "[1/6] Looking for tagged Blue/Green Deployments..."
BG_IDS=$(aws_ rds describe-blue-green-deployments \
    --query 'BlueGreenDeployments[?Tags && length(Tags[?Key==`project` && Value==`aurora-bg-toolkit`]) > `0`].BlueGreenDeploymentIdentifier' \
    --output text 2>/dev/null || true)
for bg in $BG_IDS; do
    echo "  deleting $bg"
    aws_ rds delete-blue-green-deployment --blue-green-deployment-identifier "$bg" --delete-target >/dev/null || true
done

# 2) Delete EC2 instances
echo "[2/6] Looking for tagged EC2 instances..."
INSTANCE_IDS=$(aws_ ec2 describe-instances \
    --filters "Name=tag:project,Values=aurora-bg-toolkit" "Name=instance-state-name,Values=running,stopped,pending" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -n "$INSTANCE_IDS" ]]; then
    echo "  terminating: $INSTANCE_IDS"
    aws_ ec2 terminate-instances --instance-ids $INSTANCE_IDS >/dev/null
    aws_ ec2 wait instance-terminated --instance-ids $INSTANCE_IDS
fi

# 3) Delete DB instances (in parallel, then wait)
echo "[3/6] Looking for tagged DB instances..."
DB_INSTANCES=$(aws_ rds describe-db-instances \
    --query 'DBInstances[?TagList && length(TagList[?Key==`project` && Value==`aurora-bg-toolkit`]) > `0`].DBInstanceIdentifier' \
    --output text 2>/dev/null || true)
for db in $DB_INSTANCES; do
    echo "  deleting db instance $db"
    aws_ rds delete-db-instance --db-instance-identifier "$db" --skip-final-snapshot --delete-automated-backups >/dev/null || true
done
for db in $DB_INSTANCES; do
    echo "  waiting for $db to delete..."
    aws_ rds wait db-instance-deleted --db-instance-identifier "$db" 2>/dev/null || true
done

# 4) Delete DB clusters
echo "[4/6] Looking for tagged DB clusters..."
CLUSTERS=$(aws_ rds describe-db-clusters \
    --query 'DBClusters[?TagList && length(TagList[?Key==`project` && Value==`aurora-bg-toolkit`]) > `0`].DBClusterIdentifier' \
    --output text 2>/dev/null || true)
for c in $CLUSTERS; do
    echo "  deleting cluster $c"
    aws_ rds delete-db-cluster --db-cluster-identifier "$c" --skip-final-snapshot >/dev/null || true
done
for c in $CLUSTERS; do
    echo "  waiting for cluster $c to delete..."
    aws_ rds wait db-cluster-deleted --db-cluster-identifier "$c" 2>/dev/null || true
done

# 5) Security group + DB subnet group + key pair
echo "[5/6] Cleaning up shared infra..."
if [[ -n "${ABT_SG_ID:-}" ]]; then
    aws_ ec2 delete-security-group --group-id "$ABT_SG_ID" 2>/dev/null && echo "  deleted SG $ABT_SG_ID" || echo "  SG $ABT_SG_ID delete failed (may have refs)"
fi
if [[ -n "${ABT_DB_SUBNET_GROUP:-}" ]]; then
    aws_ rds delete-db-subnet-group --db-subnet-group-name "$ABT_DB_SUBNET_GROUP" 2>/dev/null && echo "  deleted db-subnet-group $ABT_DB_SUBNET_GROUP" || true
fi
if [[ -n "${ABT_KEY_NAME:-}" ]]; then
    aws_ ec2 delete-key-pair --key-name "$ABT_KEY_NAME" 2>/dev/null && echo "  deleted key-pair $ABT_KEY_NAME" || true
fi

# 6) Local state
echo "[6/6] Removing local state dir..."
rm -rf "$STATE_DIR"

echo
echo "Teardown complete."

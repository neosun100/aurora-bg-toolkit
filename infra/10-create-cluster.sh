#!/usr/bin/env bash
# 10-create-cluster.sh — create one Aurora MySQL cluster matching the customer's spec
#
# Usage: ./10-create-cluster.sh <cluster-name>
#
# Creates:
#   * DB cluster: 8.0.mysql_aurora.3.10.4, aurora-iopt1, port 4488
#   * Writer instance: db.r7g.large
#   * Reader instance: db.t3.medium
#   * Master password managed by Secrets Manager (--manage-master-user-password)
#
# State written to state/<cluster-name>.env with the writer endpoint and secret ARN.

set -euo pipefail

CLUSTER_NAME="${1:?usage: $0 <cluster-name>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

WRITER_ID="${CLUSTER_NAME}-writer"
READER_ID="${CLUSTER_NAME}-reader"

echo "============================================================"
echo " Creating Aurora cluster: $CLUSTER_NAME"
echo "============================================================"

# 1) Cluster
if aws_ rds describe-db-clusters --db-cluster-identifier "$CLUSTER_NAME" >/dev/null 2>&1; then
    echo "cluster $CLUSTER_NAME already exists; skipping create"
else
    aws_ rds create-db-cluster \
        --db-cluster-identifier "$CLUSTER_NAME" \
        --engine aurora-mysql \
        --engine-version 8.0.mysql_aurora.3.10.4 \
        --master-username admin \
        --manage-master-user-password \
        --database-name demo \
        --port 4488 \
        --storage-type aurora-iopt1 \
        --vpc-security-group-ids "$ABT_SG_ID" \
        --db-subnet-group-name "$ABT_DB_SUBNET_GROUP" \
        --backup-retention-period 1 \
        --no-deletion-protection \
        --tags Key=project,Value=aurora-bg-toolkit Key=cluster,Value="$CLUSTER_NAME" \
        >/dev/null
    echo "  cluster create initiated"
fi

# 2) Writer
if aws_ rds describe-db-instances --db-instance-identifier "$WRITER_ID" >/dev/null 2>&1; then
    echo "writer $WRITER_ID already exists; skipping create"
else
    aws_ rds create-db-instance \
        --db-instance-identifier "$WRITER_ID" \
        --db-cluster-identifier "$CLUSTER_NAME" \
        --db-instance-class db.r7g.large \
        --engine aurora-mysql \
        --tags Key=project,Value=aurora-bg-toolkit Key=cluster,Value="$CLUSTER_NAME" Key=role,Value=writer \
        >/dev/null
    echo "  writer create initiated"
fi

# 3) Reader
if aws_ rds describe-db-instances --db-instance-identifier "$READER_ID" >/dev/null 2>&1; then
    echo "reader $READER_ID already exists; skipping create"
else
    aws_ rds create-db-instance \
        --db-instance-identifier "$READER_ID" \
        --db-cluster-identifier "$CLUSTER_NAME" \
        --db-instance-class db.t3.medium \
        --engine aurora-mysql \
        --tags Key=project,Value=aurora-bg-toolkit Key=cluster,Value="$CLUSTER_NAME" Key=role,Value=reader \
        >/dev/null
    echo "  reader create initiated"
fi

echo
echo "Resources for $CLUSTER_NAME submitted. Wait for them with:"
echo "  aws --profile $AWS_PROFILE --region $AWS_REGION rds wait db-cluster-available --db-cluster-identifier $CLUSTER_NAME"
echo "  aws --profile $AWS_PROFILE --region $AWS_REGION rds wait db-instance-available --db-instance-identifier $WRITER_ID"
echo "  aws --profile $AWS_PROFILE --region $AWS_REGION rds wait db-instance-available --db-instance-identifier $READER_ID"

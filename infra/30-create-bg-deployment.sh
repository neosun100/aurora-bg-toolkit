#!/usr/bin/env bash
# 30-create-bg-deployment.sh — create a Blue/Green Deployment for one cluster.
#
# Usage: ./30-create-bg-deployment.sh <cluster-name>
#
# This is a separate step from cluster creation because:
#   * BG deployments take 5-15 minutes to materialize (Green provisioning)
#   * Each BG can only be switched-over once; subsequent rounds need a fresh BG
#   * It pollutes the cluster's parameter group / version state, so we make it
#     opt-in
#
# The BG identifier is captured in state/<cluster>.bg.env so that
# 31-trigger-switchover.sh can find it.

set -euo pipefail

CLUSTER_NAME="${1:?usage: $0 <cluster-name>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"
aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

CLUSTER_ARN=$(aws_ rds describe-db-clusters --db-cluster-identifier "$CLUSTER_NAME" --query 'DBClusters[0].DBClusterArn' --output text)
[[ -z "$CLUSTER_ARN" || "$CLUSTER_ARN" == "None" ]] && { echo "ERR: cluster $CLUSTER_NAME not found"; exit 1; }

BG_NAME="bg-${CLUSTER_NAME}-$(date -u +%H%M%S)"
echo "Creating BG deployment $BG_NAME for $CLUSTER_NAME ..."
BG_ID=$(aws_ rds create-blue-green-deployment \
    --blue-green-deployment-name "$BG_NAME" \
    --source "$CLUSTER_ARN" \
    --tags Key=project,Value=aurora-bg-toolkit Key=cluster,Value="$CLUSTER_NAME" \
    --query 'BlueGreenDeployment.BlueGreenDeploymentIdentifier' --output text)
echo "  BG_ID: $BG_ID"

cat > "$STATE_DIR/${CLUSTER_NAME}.bg.env" <<EOF
export ABT_BG_NAME="$BG_NAME"
export ABT_BG_ID="$BG_ID"
EOF
echo "  state -> $STATE_DIR/${CLUSTER_NAME}.bg.env"

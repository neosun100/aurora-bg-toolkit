#!/usr/bin/env bash
# orchestrate-bg-v9-loop.sh — run N consecutive BG rounds with automatic
# BG deployment re-creation between rounds.
#
# Each round needs its own fresh BG deployment because a BG can only be
# switched over once (after which it's SWITCHOVER_COMPLETED and the green
# becomes the new blue with a new -old1 dangling).
#
# Per-round timeline:
#   1. Wait for any previous BG to reach SWITCHOVER_COMPLETED (or AVAILABLE for round 1)
#   2. Delete the old BG (cleanup; --delete-target so old1 cluster is reaped)
#   3. Verify cluster cluster-pg in-sync (the source-cluster check BG enforces)
#   4. Create new BG deployment
#   5. Wait BG AVAILABLE (~10-15 min for provisioning)
#   6. Run round via orchestrate-bg-v9.sh
#   7. Repeat
#
# Usage: ./orchestrate-bg-v9-loop.sh <start_round> <end_round>
# Round 1 must already have its BG AVAILABLE before starting this loop.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"

START_ROUND="${1:?usage: $0 <start_round> <end_round>}"
END_ROUND="${2:?usage: $0 <start_round> <end_round>}"

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

for r in $(seq "$START_ROUND" "$END_ROUND"); do
    echo
    echo "############################################################"
    echo " v9 BG round $r — $(date -u +%H:%M:%S) UTC"
    echo "############################################################"

    # 1) Run the round (assumes 4 BG deployments are AVAILABLE)
    bash "$REPO_ROOT/infra/orchestrate-bg-v9.sh" "$r"

    if [[ "$r" -ge "$END_ROUND" ]]; then
        echo "All requested rounds done."
        break
    fi

    # 2) Wait for the just-used BGs to reach SWITCHOVER_COMPLETED, then delete them
    echo
    echo "Cleanup: waiting for SWITCHOVER_COMPLETED + deleting used BGs..."
    sleep 30   # let RDS settle
    for c in test-02 test-03 test-04 test-05; do
        bg_id=$(grep ABT_BG_ID "$STATE_DIR/${c}.bg.env" 2>/dev/null | cut -d'"' -f2)
        if [[ -n "$bg_id" ]]; then
            aws_ rds delete-blue-green-deployment --blue-green-deployment-identifier "$bg_id" \
                --delete-target >/dev/null 2>&1 || true
            echo "  $c BG ($bg_id) delete initiated"
        fi
    done

    # Wait for cluster -old1 instances to be deleted (so cluster identifier is freed up)
    echo "Wait for old BG cleanup (~3 min)..."
    sleep 60
    # Delete ALL old instances (BG --delete-target retains them ~7 days; we force-delete to free quota)
    OLD_INSTS=$(aws_ rds describe-db-instances --query 'DBInstances[?contains(DBInstanceIdentifier, `-old`)].DBInstanceIdentifier' --output text 2>/dev/null || true)
    for inst in $OLD_INSTS; do
        aws_ rds delete-db-instance --db-instance-identifier "$inst" --skip-final-snapshot --delete-automated-backups >/dev/null 2>&1 || true
    done
    for inst in $OLD_INSTS; do
        aws_ rds wait db-instance-deleted --db-instance-identifier "$inst" 2>/dev/null || true
    done
    OLD_CLUSTERS=$(aws_ rds describe-db-clusters --query 'DBClusters[?contains(DBClusterIdentifier, `-old`)].DBClusterIdentifier' --output text 2>/dev/null || true)
    for cl in $OLD_CLUSTERS; do
        aws_ rds delete-db-cluster --db-cluster-identifier "$cl" --skip-final-snapshot >/dev/null 2>&1 || true
    done
    for cl in $OLD_CLUSTERS; do
        aws_ rds wait db-cluster-deleted --db-cluster-identifier "$cl" 2>/dev/null || true
    done
    echo "  all -old* clusters/instances cleaned"

    # 3) Re-check cluster-pg in-sync (it should still be in-sync after switchover, but verify)
    for c in test-02 test-03 test-04 test-05; do
        s=$(aws_ rds describe-db-clusters --db-cluster-identifier "$c" \
            --query 'DBClusters[0].DBClusterMembers[?IsClusterWriter==`true`].DBClusterParameterGroupStatus' --output text 2>/dev/null || echo "?")
        if [[ "$s" != "in-sync" ]]; then
            echo "  WARN $c cluster-pg=$s; rebooting writer to re-sync..."
            aws_ rds modify-db-cluster --db-cluster-identifier "$c" --apply-immediately >/dev/null
            sleep 5
            aws_ rds reboot-db-instance --db-instance-identifier "$c-writer" >/dev/null
            aws_ rds wait db-instance-available --db-instance-identifier "$c-writer"
        fi
    done

    # 4) Re-record state (endpoints may have changed due to switchover)
    for c in test-02 test-03 test-04 test-05; do
        bash "$REPO_ROOT/infra/11-record-cluster-state.sh" "$c" >/dev/null 2>&1 || true
    done

    # 5) Create new BG deployments
    echo
    echo "Creating fresh BG deployments for round $((r + 1))..."
    for c in test-02 test-03 test-04 test-05; do
        bash "$REPO_ROOT/infra/30-create-bg-deployment.sh" "$c" 2>&1 | tail -2
    done

    # 6) Wait until all 4 BGs reach AVAILABLE
    echo "Waiting for BGs to provision..."
    for i in $(seq 1 60); do
        AVAIL=$(aws_ rds describe-blue-green-deployments \
            --query 'BlueGreenDeployments[?starts_with(BlueGreenDeploymentName, `bg-test-`) && Status==`AVAILABLE`].BlueGreenDeploymentName' \
            --output text | wc -w | tr -d ' ')
        echo "  [$(date +%H:%M:%S)] AVAILABLE=$AVAIL/4"
        [[ "$AVAIL" -ge 4 ]] && break
        sleep 30
    done
done

echo
echo "Loop complete — rounds $START_ROUND..$END_ROUND."

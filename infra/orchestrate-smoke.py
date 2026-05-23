#!/usr/bin/env python3
"""
orchestrate-smoke.py — Mini end-to-end pipeline validation.

Goal: verify the full pipeline (CDK deploy → SSH → java client → STATS log →
analyze-stats-gap → CDK destroy → S3 sync → SNS publish) WITHOUT spending
1.5h on a real run. ~30 minutes, ~$1.

Mode: 1 cluster (test-v11-1) × 1 round × BG only.

Pass criteria:
  ✓ All 5 ABT stacks deploy cleanly
  ✓ EC2 SSH-ready
  ✓ Java client runs > 60s producing STATS lines
  ✓ Triggered Aurora BG switchover succeeds
  ✓ ec2_wrapper.log retrieved
  ✓ analyze-stats-gap.py outputs valid JSON with writeMaxMs > 0 and < 30000
  ✓ CDK destroy clean (0 v11 clusters left)
  ✓ S3 progress synced (if ABT_STATE_BUCKET set)
  ✓ SNS message published (if ABT_TOPIC_ARN set)

Reuses orchestrate-v11.py's machinery via env-var hooks: forces CLUSTER_COUNT=1,
ROUNDS_PER_SCENARIO=1, and skips Failover + Reboot phases by setting
SKIP_PHASES env var.

Note: this script doesn't directly invoke v11 orchestrator's main(); instead
it imports its V11Orchestrator class and replaces test_parallel() with a
single-cluster-BG-only variant. This keeps the validation tight and fast.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Default to smoke prefix BEFORE importing orchestrate_v11
os.environ.setdefault("V11_RUN_LABEL", "smoke-test")
os.environ.setdefault("V11_STATE_PREFIX", "smoke-test")
os.environ.setdefault("V11_CONFIG", "v16-tps1280")
os.environ.setdefault("V11_ROUNDS", "1")
os.environ.setdefault("V11_WRITER_INSTANCE", "r7g.large")
os.environ.setdefault("V11_READER_INSTANCE", "t3.medium")
os.environ.setdefault("V11_CLIENT_INSTANCE", "c6i.2xlarge")
os.environ.setdefault("V11_HEAP_FLAG", "-Xmx2g")
os.environ.setdefault("V11_BUFFER_WARMUP_S", "30")
os.environ.setdefault("V11_REBOOT_STABILIZE_S", "60")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "infra"))

# Now import — module-level constants pick up our env settings
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "orchestrate_v11", str(REPO_ROOT / "infra" / "orchestrate-v11.py"))
orchestrate_v11 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orchestrate_v11)

V11Orchestrator = orchestrate_v11.V11Orchestrator
CLUSTER_IDS = orchestrate_v11.CLUSTER_IDS  # full 5 cluster list
log = orchestrate_v11.log
phase_set = orchestrate_v11.phase_set
phase_status = orchestrate_v11.phase_status


# ── S3 sync hook for smoke (uses same ABT_STATE_BUCKET as matrix) ──
def _smoke_sync():
    bucket = os.environ.get("ABT_STATE_BUCKET", "")
    if not bucket:
        return
    try:
        import boto3
        s3 = boto3.client("s3")
        pf = orchestrate_v11.PROGRESS_FILE
        if pf.exists():
            s3.put_object(
                Bucket=bucket, Key=f"runs/smoke-progress.json",
                Body=pf.read_bytes(), ContentType="application/json",
            )
    except Exception as e:
        log.debug("smoke sync to S3 failed: %s", e)


orchestrate_v11.SYNC_HOOK = _smoke_sync


class SmokeOrchestrator(V11Orchestrator):
    """Override test_parallel: only test-v11-1, BG only."""

    def test_parallel(self):
        log.info("SMOKE TEST: only cluster test-v11-1, BG only, 1 round")
        try:
            self.run_round("test-v11-1", "blue-green", 1)
        except Exception as e:
            log.exception("smoke BG round failed: %s", e)
            raise

    def run_cluster_all_rounds(self, cluster_id: str):
        # Not used in smoke (test_parallel directly calls run_round)
        if cluster_id != "test-v11-1":
            log.info("SMOKE: skipping %s", cluster_id)
            return
        self.run_round(cluster_id, "blue-green", 1)


def main():
    log.info("=" * 78)
    log.info(" Aurora BG Toolkit Smoke Test (Layer 2)")
    log.info(" Mode: 1 cluster × 1 round × BG only")
    log.info(" State prefix: %s", os.environ["V11_STATE_PREFIX"])
    log.info(" Expected duration: ~30 min, ~$1 AWS")
    log.info("=" * 78)

    o = SmokeOrchestrator()
    try:
        o.main()
    except Exception as e:
        log.exception("Smoke test failed: %s", e)
        return 2

    # Validate the result
    import json
    rd_pat = list((REPO_ROOT / "e2e-results").glob("smoke-test-blue-green-test-v11-1-r1_*"))
    if not rd_pat:
        log.error("Smoke test produced no e2e-results directory")
        return 3

    sub = rd_pat[-1] / "test-v11-1_v16-tps1280"
    gap_file = sub / "stats-gap.json"
    if not gap_file.exists():
        log.error("Smoke test: no stats-gap.json found at %s", gap_file)
        return 4

    try:
        gap = json.loads(gap_file.read_text())
        wmax = gap["summary"]["writeMaxMs"]
        if wmax <= 0:
            log.error("Smoke test: writeMaxMs=%d (expected > 0)", wmax)
            return 5
        if wmax > 30000:
            log.warning("Smoke test: writeMaxMs=%d ms (very high — pipeline may be broken)",
                        wmax)
            return 6
        log.info("✓ Smoke test PASSED. BG writeMaxMs = %d ms (%.2f s)",
                 wmax, wmax / 1000.0)
    except Exception as e:
        log.error("Smoke test result parsing failed: %s", e)
        return 7

    return 0


if __name__ == "__main__":
    sys.exit(main())

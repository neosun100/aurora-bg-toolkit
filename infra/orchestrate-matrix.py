#!/usr/bin/env python3
"""
orchestrate-matrix.py — v16 matrix sweep wrapper around orchestrate-v11.py.

Runs a sequence of v11 orchestrator cycles defined in matrix-spec.yaml.
Each run:
  1. Verifies the AWS account is clean (no AbtV11* clusters)
  2. Sets env vars for instance class / TPS / heap / etc.
  3. Invokes orchestrate-v11.py with V11_STATE_PREFIX = run_id
  4. Verifies cdk destroy completed cleanly
  5. Syncs progress + data to S3
  6. Publishes SNS notification
  7. Pauses inter_run_pause_s before next run

This script is designed to run as a systemd service on the matrix runner
EC2, fully decoupled from the user's laptop. It writes progress to a
single matrix-progress.json that's synced to S3 every cycle.

Recovery:
  - If killed mid-run, restart the systemd service: it picks up where it
    left off (per-run V11_STATE_PREFIX progress.json is preserved)
  - If a run fails, it's marked failed in matrix-progress.json and the
    next run starts after the inter_run_pause_s

Environment:
  ABT_MATRIX_SPEC=/opt/abt/aurora-bg-toolkit/infra/matrix-spec.yaml
  ABT_STATE_BUCKET=abt-v16-state-{account}
  ABT_TOPIC_ARN=arn:aws:sns:...:abt-v16-events
  AWS_PROFILE / AWS_REGION  (or use IAM role on the runner EC2)
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import boto3
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = Path(os.environ.get("ABT_MATRIX_SPEC", REPO_ROOT / "infra" / "matrix-spec.yaml"))
STATE_DIR = REPO_ROOT / "infra" / "state"
MATRIX_PROGRESS = STATE_DIR / "matrix-progress.json"
MATRIX_LOG = STATE_DIR / "matrix-master.log"

STATE_BUCKET = os.environ.get("ABT_STATE_BUCKET", "")
TOPIC_ARN = os.environ.get("ABT_TOPIC_ARN", "")

# ── Bark push notification (preferred channel) ──
# Pushes only to Neo's M5 Max via Bark service. Credentials passed by
# launch-matrix.sh from ~/.env into the systemd Environment.
BARK_SERVER = os.environ.get("BARK_SERVER", "bark.aws.xin")
BARK_USERNAME = os.environ.get("BARK_USERNAME", "bark")
BARK_PASSWORD = os.environ.get("BARK_PASSWORD", "")
BARK_KEY_MAC = os.environ.get("BARK_KEY_MAC", "")

STATE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [matrix] %(message)s",
    handlers=[logging.FileHandler(MATRIX_LOG), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("matrix")


def now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


# ───────────────────── progress state ─────────────────────
def load_progress() -> dict:
    if MATRIX_PROGRESS.exists():
        try:
            return json.loads(MATRIX_PROGRESS.read_text())
        except Exception:
            pass
    return {"started_at": now(), "spec": str(SPEC_PATH), "runs": {}, "events": []}


def save_progress(p: dict):
    MATRIX_PROGRESS.write_text(json.dumps(p, indent=2))
    sync_to_s3()


def update_run(run_id: str, **kwargs):
    p = load_progress()
    rr = p["runs"].setdefault(run_id, {})
    rr.update(kwargs)
    save_progress(p)


def append_event(level: str, message: str, run_id: str | None = None):
    p = load_progress()
    p["events"].append({
        "ts": now(), "level": level, "run": run_id, "message": message,
    })
    p["events"] = p["events"][-200:]  # cap
    save_progress(p)


# ───────────────────── S3 / SNS plumbing ─────────────────────
_s3 = None
_sns = None


def s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _s3


def sns():
    global _sns
    if _sns is None:
        _sns = boto3.client("sns", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _sns


def sync_to_s3():
    """Upload matrix-progress.json and master.log to S3.

    Called after every state change. Quiet on failure (network blips
    shouldn't kill the orchestrator).
    """
    if not STATE_BUCKET:
        return
    try:
        s3().put_object(
            Bucket=STATE_BUCKET,
            Key="matrix-progress.json",
            Body=MATRIX_PROGRESS.read_bytes(),
            ContentType="application/json",
            CacheControl="no-cache, max-age=10",
        )
    except Exception as e:
        log.debug("S3 progress upload failed: %s", e)
    try:
        if MATRIX_LOG.exists():
            s3().put_object(
                Bucket=STATE_BUCKET,
                Key="matrix-master.log",
                Body=MATRIX_LOG.read_bytes()[-2_000_000:],  # last 2MB
                ContentType="text/plain",
                CacheControl="no-cache, max-age=10",
            )
    except Exception as e:
        log.debug("S3 log upload failed: %s", e)


def publish_sns(subject: str, message: str):
    """Send notification — primary via Bark to M5 Max, secondary via SNS.

    Bark is preferred (instant, real device, no spam folder). SNS is kept
    as a fallback in case Bark is unreachable.
    """
    # ── Primary: Bark to M5 Max ──
    bark_ok = _publish_bark(subject, message)

    # ── Secondary: SNS (only if topic configured) ──
    if TOPIC_ARN:
        try:
            sns().publish(TopicArn=TOPIC_ARN, Subject=subject[:99], Message=message)
            log.info("SNS published: %s", subject)
        except Exception as e:
            log.warning("SNS publish failed: %s", e)

    if not bark_ok and not TOPIC_ARN:
        log.info("Notification skipped (no Bark or SNS): %s", subject)


def _publish_bark(subject: str, message: str, level: str = "active") -> bool:
    """POST to Bark server, push to M5 Max. Returns True on HTTP 200.

    Decides level/sound from subject content:
      - "FAILED" / "CRASHED" → critical + alarm
      - "COMPLETE" / "done"  → active + calypso (recovery feel)
      - "started"            → passive + silence (informational)
      - default              → active + glass
    """
    if not (BARK_PASSWORD and BARK_KEY_MAC):
        return False

    # Smart level/sound selection
    s_lower = subject.lower()
    if "failed" in s_lower or "crashed" in s_lower or "🚨" in subject:
        level, sound = "critical", "alarm"
    elif "complete" in s_lower or "🎉" in subject:
        level, sound = "timeSensitive", "calypso"
    elif "started" in s_lower:
        level, sound = "passive", "silence"
    else:
        level, sound = "active", "glass"

    # Truncate message for Bark notification body (iOS ~1000 char practical limit)
    body = message if len(message) < 1500 else (message[:1490] + "\n…(truncated)")

    payload = {
        "device_key": BARK_KEY_MAC,
        "title": subject[:120],
        "body": body,
        "level": level,
        "sound": sound,
        "group": "AuroraBGToolkit",
    }
    data = json.dumps(payload).encode("utf-8")

    import urllib.request
    import base64
    auth = base64.b64encode(f"{BARK_USERNAME}:{BARK_PASSWORD}".encode()).decode()
    req = urllib.request.Request(
        f"https://{BARK_SERVER}/push",
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Basic {auth}",
            # bark.aws.xin filters Python's default urllib User-Agent → 403.
            # Sending an explicit UA bypasses that. Discovered 2026-05-21.
            "User-Agent": "abt-v16-matrix/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body_text = resp.read().decode("utf-8", "replace")
            if resp.status == 200 and '"code":200' in body_text:
                log.info("Bark push to M5 Max OK: %s", subject)
                return True
            log.warning("Bark push non-200: %s | %s", resp.status, body_text[:200])
            return False
    except Exception as e:
        log.warning("Bark push failed: %s", e)
        return False


def sync_run_progress_to_s3(run_id: str):
    """Pull the v11-orchestrator's per-run progress.json (state prefix =
    run_id) and put it in S3 under runs/{run_id}-progress.json so the
    dashboard can see per-run detail."""
    if not STATE_BUCKET:
        return
    pf = STATE_DIR / f"{run_id}-progress.json"
    if not pf.exists():
        return
    try:
        s3().put_object(
            Bucket=STATE_BUCKET, Key=f"runs/{run_id}-progress.json",
            Body=pf.read_bytes(), ContentType="application/json",
        )
    except Exception as e:
        log.debug("S3 per-run progress upload failed: %s", e)


# ───────────────────── AWS state verification ─────────────────────
def _rds_client():
    """Lazy RDS client with explicit region (no named profile on runner EC2)."""
    if not hasattr(_rds_client, "_c"):
        _rds_client._c = boto3.client(
            "rds", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    return _rds_client._c


def list_v11_clusters() -> list[str]:
    """Return identifiers of any test-v11-* clusters currently in account."""
    try:
        resp = _rds_client().describe_db_clusters()
        return [c["DBClusterIdentifier"] for c in resp["DBClusters"]
                if c["DBClusterIdentifier"].startswith("test-v11-")]
    except Exception as e:
        log.warning("describe_db_clusters failed: %s", e)
        return []


def list_v11_old_artifacts() -> int:
    """Return count of any -old1 instances/clusters lingering from prior BG."""
    n = 0
    try:
        rds = _rds_client()
        for inst in rds.describe_db_instances()["DBInstances"]:
            if "-old" in inst["DBInstanceIdentifier"]:
                n += 1
        for cl in rds.describe_db_clusters()["DBClusters"]:
            if "-old" in cl["DBClusterIdentifier"]:
                n += 1
    except Exception as e:
        log.warning("list_v11_old_artifacts failed: %s", e)
    return n


def wait_for_clean_account(max_minutes: int = 30) -> bool:
    """Block until no AbtV11 clusters remain. Returns True if clean."""
    # Bypass for emergency recovery scenarios where pre-existing clusters are reusable
    # (e.g. M4 partial deploy where 4/5 clusters succeeded but cdk failed on stack-4).
    if os.environ.get("V16_SKIP_CLEAN_CHECK") == "1":
        log.warning("⚠ wait_for_clean_account BYPASSED via V16_SKIP_CLEAN_CHECK=1")
        return True
    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        clusters = list_v11_clusters()
        olds = list_v11_old_artifacts()
        if not clusters and olds == 0:
            log.info("✓ AWS account clean (no v11 clusters or -old artifacts)")
            return True
        log.info("Waiting for cleanup: %d v11 clusters + %d -old artifacts",
                 len(clusters), olds)
        time.sleep(30)
    return False


# ───────────────────── per-run execution ─────────────────────
def run_v11_orchestrator(run: dict, defaults: dict) -> dict:
    """Invoke orchestrate-v11.py as a subprocess with run-specific env."""
    run_id = run["id"]
    label = run.get("label", run_id)

    env = os.environ.copy()
    env["V11_CONFIG"] = run.get("config", defaults["config"])
    env["V11_RUN_LABEL"] = label
    env["V11_STATE_PREFIX"] = label
    env["V11_ROUNDS"] = str(run.get("rounds", defaults["rounds"]))
    env["V11_WRITER_INSTANCE"] = run.get("writer_instance", defaults["writer_instance"])
    env["V11_READER_INSTANCE"] = run.get("reader_instance", defaults["reader_instance"])
    env["V11_CLIENT_INSTANCE"] = run.get("client_instance", defaults["client_instance"])
    env["V11_HEAP_FLAG"] = run.get("jvm_heap", defaults["jvm_heap"])
    env["V11_BUFFER_WARMUP_S"] = str(run.get("buffer_warmup_s", defaults["buffer_warmup_s"]))
    env["V11_REBOOT_STABILIZE_S"] = str(run.get("reboot_stabilize_s", defaults["reboot_stabilize_s"]))
    # In matrix mode, NetworkStack is shared with V16MatrixRunnerStack, so v11
    # orchestrator must NOT try to destroy NetworkStack (would fail with
    # "Cannot delete export AbtV11KeyName as it is in use by AbtV16MatrixRunnerStack").
    env["V11_KEEP_NETWORK"] = "1"

    log.info("=" * 80)
    log.info("Starting run %s (%s)", run_id, label)
    log.info("  writer=%s reader=%s client=%s",
             env["V11_WRITER_INSTANCE"], env["V11_READER_INSTANCE"],
             env["V11_CLIENT_INSTANCE"])
    log.info("  config=%s rounds=%s heap=%s",
             env["V11_CONFIG"], env["V11_ROUNDS"], env["V11_HEAP_FLAG"])
    log.info("=" * 80)

    started = now()
    update_run(run_id, status="running", started_at=started, label=label,
               writer_instance=env["V11_WRITER_INSTANCE"],
               client_instance=env["V11_CLIENT_INSTANCE"],
               tps_config=env["V11_CONFIG"])
    publish_sns(f"[v16] Run {run_id} started",
                f"Run {label} started at {started}.\n"
                f"Writer: {env['V11_WRITER_INSTANCE']}\n"
                f"Client: {env['V11_CLIENT_INSTANCE']}\n"
                f"TPS config: {env['V11_CONFIG']}")

    if run.get("smoke"):
        # Smoke test uses a special mini-orchestrator
        cmd = ["python3", str(REPO_ROOT / "infra" / "orchestrate-smoke.py")]
    else:
        cmd = ["python3", str(REPO_ROOT / "infra" / "orchestrate-v11.py")]

    rc = -1
    err_msg = ""
    try:
        # Stream output to matrix master log AND check return code.
        # Use Popen so we can sync to S3 periodically while it runs.
        proc = subprocess.Popen(
            cmd, env=env, cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        last_sync = time.time()
        for line in proc.stdout:
            log.info("[%s] %s", run_id, line.rstrip())
            # Sync per-run progress to S3 every ~30s
            if time.time() - last_sync > 30:
                sync_run_progress_to_s3(label)
                last_sync = time.time()
        rc = proc.wait()
    except Exception as e:
        err_msg = str(e)[:400]
        log.exception("Run %s raised: %s", run_id, e)
    sync_run_progress_to_s3(label)

    ended = now()
    if rc == 0:
        status = "done"
        log.info("✓ Run %s completed", run_id)
        publish_sns(f"[v16] Run {run_id} done",
                    f"Run {label} completed successfully at {ended}.\n"
                    f"Writer: {env['V11_WRITER_INSTANCE']}, "
                    f"Client: {env['V11_CLIENT_INSTANCE']}")
    else:
        status = "failed"
        log.error("✗ Run %s failed (rc=%d): %s", run_id, rc, err_msg)
        publish_sns(f"[v16] Run {run_id} FAILED",
                    f"Run {label} failed (rc={rc}) at {ended}.\n{err_msg}")

    update_run(run_id, status=status, ended_at=ended, return_code=rc,
               error=err_msg or None)
    return {"status": status, "rc": rc}


# ───────────────────── main loop ─────────────────────
def main():
    if not SPEC_PATH.exists():
        log.error("Spec not found: %s", SPEC_PATH)
        sys.exit(2)
    spec = yaml.safe_load(SPEC_PATH.read_text())
    defaults = spec["defaults"]
    runs = spec["runs"]
    policy = spec.get("policy", {})

    p = load_progress()
    if "spec_loaded_at" not in p:
        p["spec_loaded_at"] = now()
        p["total_runs"] = len(runs)
        save_progress(p)

    publish_sns("[v16] Matrix sweep started",
                f"{len(runs)} runs queued. Started {p['started_at']}.\n"
                f"Spec: {SPEC_PATH}\n"
                f"Dashboard: aws s3 cp s3://{STATE_BUCKET}/dashboard.html /tmp/d.html && open /tmp/d.html")

    consecutive_failures = 0

    for run in runs:
        run_id = run["id"]
        existing = p["runs"].get(run_id, {})
        if existing.get("status") == "done":
            log.info("Skipping run %s (already done)", run_id)
            continue

        # Verify clean account before each run
        log.info("Pre-run cleanup verification for %s", run_id)
        if not wait_for_clean_account(max_minutes=15):
            log.error("Account not clean before %s — pausing matrix",
                      run_id)
            append_event("error", "Account not clean before run", run_id)
            publish_sns(f"[v16] Matrix paused — account not clean before {run_id}",
                        "Manual intervention required. Check existing v11 stacks.")
            sys.exit(3)

        # Execute the run
        result = run_v11_orchestrator(run, defaults)

        if result["status"] == "failed":
            consecutive_failures += 1
            if consecutive_failures >= 2 and policy.get("skip_on_deploy_failure", True):
                log.error("2 consecutive failures — pausing matrix")
                append_event("error", "2 consecutive failures, pausing", run_id)
                publish_sns("[v16] Matrix paused — consecutive failures",
                            f"Last failed run: {run_id}. Manual intervention required.")
                sys.exit(4)
        else:
            consecutive_failures = 0

        # Inter-run pause for RDS control-plane settle
        pause = int(policy.get("inter_run_pause_s", 180))
        log.info("Inter-run pause: %ds", pause)
        time.sleep(pause)

    p = load_progress()
    p["completed_at"] = now()
    save_progress(p)

    summary_lines = []
    for r in runs:
        rr = p["runs"].get(r["id"], {})
        summary_lines.append(f"  {r['id']:8s} {rr.get('status','?'):8s} "
                             f"{r.get('label','')}")
    summary = "\n".join(summary_lines)
    log.info("Matrix complete:\n%s", summary)
    publish_sns("[v16] Matrix sweep COMPLETE",
                f"All runs done at {p['completed_at']}.\n\n"
                f"{summary}\n\n"
                f"Pull report: aws s3 sync s3://{STATE_BUCKET}/reports/ ./reports/\n"
                f"Pull dashboard: aws s3 cp s3://{STATE_BUCKET}/dashboard.html /tmp/d.html && open /tmp/d.html")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Matrix interrupted by user")
        sys.exit(1)
    except Exception as e:
        log.exception("Matrix orchestrator died: %s", e)
        publish_sns("[v16] Matrix orchestrator CRASHED", str(e)[:1000])
        sys.exit(5)

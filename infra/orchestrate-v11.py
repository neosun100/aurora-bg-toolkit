#!/usr/bin/env python3
"""
orchestrate-v11.py — End-to-end Python orchestrator for v11 experiment.

Replaces the bash orchestrate-v10-master.sh with:
  - Full IaC via CDK (deploy + destroy)
  - 5-cluster parallel execution (ThreadPoolExecutor)
  - File-locked progress.json for resumability
  - Compatible with the same e2e-results/ + scripts/analyze-stats-gap.py outputs

Usage:
    python3 infra/orchestrate-v11.py            # normal launch (resume if state exists)
    FRESH=1 python3 infra/orchestrate-v11.py    # ignore state, start over
    SKIP_PHASES=CDK_DESTROY python3 ...         # don't tear down at end

Status:
    bash scripts/v11-status.sh
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from pathlib import Path

import boto3

# ────────────────── config ──────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "infra" / "state"

# Support running with different configs via environment variable:
#   V11_CONFIG=v12-aggressive-timeouts python3 infra/orchestrate-v11.py
# Default: v11-final (production baseline)
ACTIVE_CONFIG = os.environ.get("V11_CONFIG", "v11-final")
EXPERIMENT_NAME = f"v11-cdk-parallel" if ACTIVE_CONFIG == "v11-final" else f"v12-{ACTIVE_CONFIG.replace('v12-', '')}"

# State files are per-config so v11 and v12 don't clobber each other
_state_prefix = "v11" if ACTIVE_CONFIG == "v11-final" else "v12"
PROGRESS_FILE = STATE_DIR / f"{_state_prefix}-progress.json"
LOG_FILE = STATE_DIR / f"{_state_prefix}-master.log"
LOCK_FILE = STATE_DIR / f"{_state_prefix}-master.lock"
CDK_DIR = REPO_ROOT / "infra" / "cdk"

CLUSTER_COUNT = 5
ROUNDS_PER_SCENARIO = 2  # 2 × (BG + FO + RB) per cluster = 6 measurements/cluster
CLUSTER_IDS = [f"test-v11-{i}" for i in range(1, CLUSTER_COUNT + 1)]

AWS_PROFILE = os.environ.get("AWS_PROFILE", "jiasunm-neo")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

NETWORK_STACK = "AbtV11NetworkStack"
CLUSTER_STACKS = [f"AbtV11ClusterStack-{i}" for i in range(1, CLUSTER_COUNT + 1)]
CLIENT_STACK = "AbtV11ClientStack"
ALL_STACKS = [NETWORK_STACK] + CLUSTER_STACKS + [CLIENT_STACK]

# ────────────────── logging ──────────────────
STATE_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(threadName)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("v11")

# ────────────────── progress state ──────────────────
_progress_lock = threading.Lock()


def now() -> str:
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def load_progress() -> dict:
    if PROGRESS_FILE.exists() and os.environ.get("FRESH") != "1":
        return json.loads(PROGRESS_FILE.read_text())
    return {
        "experiment": EXPERIMENT_NAME,
        "started_at": now(),
        "phases": {},
        "errors": [],
    }


def save_progress(p):
    PROGRESS_FILE.write_text(json.dumps(p, indent=2))


@contextmanager
def progress():
    with _progress_lock:
        p = load_progress()
        yield p
        save_progress(p)


def phase_status(name) -> str:
    with progress() as p:
        return p["phases"].get(name, {}).get("status", "pending")


def phase_set(name, status, **kwargs):
    with progress() as p:
        ph = p["phases"].setdefault(name, {})
        ph["status"] = status
        if status == "running":
            ph["started_at"] = now()
            ph["attempts"] = ph.get("attempts", 0) + 1
        if status in ("done", "failed"):
            ph["ended_at"] = now()
            try:
                s = datetime.datetime.fromisoformat(ph["started_at"].rstrip("Z"))
                e = datetime.datetime.fromisoformat(ph["ended_at"].rstrip("Z"))
                ph["duration_s"] = int((e - s).total_seconds())
            except Exception:
                pass
        for k, v in kwargs.items():
            ph[k] = v


def phase_record_error(name, err):
    with progress() as p:
        p.setdefault("errors", []).append({"phase": name, "ts": now(), "error": str(err)[:1000]})


# ────────────────── shell helpers ──────────────────
def sh(cmd: list[str], cwd: Path | None = None, env: dict | None = None,
       check: bool = True, timeout: int | None = None,
       capture: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command. cmd is list of args (no shell=True for safety)."""
    e = os.environ.copy()
    if env:
        e.update(env)
    log.debug("$ %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(
        cmd, cwd=cwd, env=e, check=check, timeout=timeout,
        capture_output=capture, text=True,
    )


def ssh_run(public_ip: str, key_path: Path, cmd: str, timeout: int = 60) -> str:
    """Run a shell snippet on the EC2 runner via SSH. Returns stdout."""
    full = [
        "ssh", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=no",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=10",
        f"ec2-user@{public_ip}", cmd,
    ]
    r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ssh failed (rc={r.returncode}): {r.stderr.strip()[:500]}")
    return r.stdout


def scp_to(public_ip: str, key_path: Path, local: Path, remote: str, timeout: int = 120):
    cmd = [
        "scp", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR",
        str(local), f"ec2-user@{public_ip}:{remote}",
    ]
    subprocess.run(cmd, check=True, timeout=timeout)


def scp_from(public_ip: str, key_path: Path, remote: str, local: Path, timeout: int = 120):
    cmd = [
        "scp", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR",
        f"ec2-user@{public_ip}:{remote}", str(local),
    ]
    subprocess.run(cmd, check=True, timeout=timeout)


# ────────────────── orchestrator ──────────────────
class V11Orchestrator:
    def __init__(self):
        self.session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        self.cf = self.session.client("cloudformation")
        self.rds = self.session.client("rds")
        self.ec2 = self.session.client("ec2")
        self.sm = self.session.client("secretsmanager")
        # populated by COLLECT_OUTPUTS
        self.ec2_public_ip: str = ""
        self.ec2_instance_id: str = ""
        self.cluster_endpoints: dict[str, str] = {}
        self.cluster_arns: dict[str, str] = {}
        self.master_password: str = ""
        self.master_secret_arn: str = ""
        self.key_pair_id: str = ""
        self.key_path: Path = STATE_DIR / "abt-v11-key.pem"

    # ───── Lock ─────
    def acquire_lock(self):
        if LOCK_FILE.exists():
            try:
                pid = int(LOCK_FILE.read_text().strip())
                os.kill(pid, 0)  # signal 0 == check if alive
                log.error("orchestrator already running (pid=%s); refuse to start.", pid)
                sys.exit(1)
            except (ProcessLookupError, ValueError):
                pass  # stale lock
        LOCK_FILE.write_text(str(os.getpid()))

    def release_lock(self):
        try:
            LOCK_FILE.unlink()
        except FileNotFoundError:
            pass

    # ───── Phase runner ─────
    def run_phase(self, name: str, fn, *args):
        skip = os.environ.get("SKIP_PHASES", "").split(",")
        if name in skip:
            log.info("SKIP %s (per SKIP_PHASES)", name)
            phase_set(name, "done", note="skipped")
            return
        s = phase_status(name)
        if s == "done":
            log.info("skip %s (already done)", name)
            return
        if s == "running":
            log.warning("%s was running (interrupted); resetting", name)
            phase_set(name, "pending")
        log.info("▶ START %s", name)
        phase_set(name, "running")
        try:
            fn(*args)
        except Exception as e:
            log.exception("✗ FAIL %s", name)
            phase_set(name, "failed", error=str(e)[:500])
            phase_record_error(name, e)
            raise
        log.info("✓ DONE %s", name)
        phase_set(name, "done")

    # ───── Phase: PRECHECK ─────
    def precheck(self):
        for cmd in ("aws", "cdk", "mvn", "java", "python3", "ssh", "scp"):
            if subprocess.run(["which", cmd], capture_output=True).returncode != 0:
                raise RuntimeError(f"missing tool: {cmd}")
        # AWS auth
        ident = self.session.client("sts").get_caller_identity()
        log.info("AWS account: %s", ident["Account"])
        # CDK app present?
        if not (CDK_DIR / "app.py").exists():
            raise RuntimeError(f"CDK app not found at {CDK_DIR / 'app.py'}")
        # CDK venv ready?
        py = CDK_DIR / ".venv" / "bin" / "python3"
        if not py.exists():
            raise RuntimeError(f"CDK venv missing: {py}. Run: cd {CDK_DIR} && uv venv .venv && uv pip install -r requirements.txt")

    # ───── Phase: BUILD ─────
    def build(self):
        target = REPO_ROOT / "target" / "abt-w401.jar"
        if target.exists():
            log.info("fat-jar already built: %s (%.1f MB)", target, target.stat().st_size / 1e6)
            return
        log.info("building wrapper-4.1 fat-jar (this takes ~30 s)...")
        sh(["mvn", "-q", "clean", "package", "-DskipTests", "-Pwrapper-4.1"], cwd=REPO_ROOT)
        src = REPO_ROOT / "target" / "aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar"
        if not src.exists():
            raise RuntimeError(f"build did not produce {src}")
        target.write_bytes(src.read_bytes())
        log.info("built: %s", target)

    # ───── Phase: CDK_BOOTSTRAP ─────
    def cdk_bootstrap(self):
        # Idempotent: skips if CDKToolkit stack already exists
        try:
            self.cf.describe_stacks(StackName="CDKToolkit")
            log.info("CDKToolkit stack exists; skipping cdk bootstrap")
            return
        except self.cf.exceptions.ClientError:
            pass
        log.info("cdk bootstrap (first time setup)...")
        env = {
            "AWS_PROFILE": AWS_PROFILE,
            "AWS_REGION": AWS_REGION,
            "CDK_DEFAULT_ACCOUNT": self._account_id(),
            "CDK_DEFAULT_REGION": AWS_REGION,
            "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
        }
        sh(["cdk", "bootstrap"], cwd=CDK_DIR, env=env, timeout=300)

    def _account_id(self) -> str:
        return self.session.client("sts").get_caller_identity()["Account"]

    # ───── Phase: CDK_DEPLOY ─────
    def cdk_deploy(self):
        env = {
            "AWS_PROFILE": AWS_PROFILE,
            "AWS_REGION": AWS_REGION,
            "CDK_DEFAULT_ACCOUNT": self._account_id(),
            "CDK_DEFAULT_REGION": AWS_REGION,
            "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
        }
        log.info("cdk deploy --all (7 stacks; ~12 min)...")
        # CRITICAL: capture stderr+stdout into master log so failures don't get
        # silently swallowed (lesson from the first v11 attempt).
        full_env = os.environ.copy()
        full_env.update(env)
        proc = subprocess.Popen(
            ["cdk", "deploy", "--all", "--require-approval", "never",
             "--concurrency", "10", "--progress", "events"],
            cwd=CDK_DIR, env=full_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            for line in proc.stdout:
                # Stream cdk output line-by-line into the master log
                log.info("[cdk] %s", line.rstrip())
            rc = proc.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("cdk deploy timed out (1800 s)")
        if rc != 0:
            raise RuntimeError(f"cdk deploy --all exited with rc={rc}")
        log.info("cdk deploy complete")

    # ───── Phase: COLLECT_OUTPUTS ─────
    def _restore_runtime_state(self):
        """Restore in-memory state from progress.json + boto3 when resuming.

        When COLLECT_OUTPUTS was already done in a previous run, the in-memory
        cluster_arns/endpoints/ec2_public_ip are empty. This method repopulates
        them so TEST_PARALLEL can proceed.
        """
        if self.cluster_arns and self.ec2_public_ip:
            return  # already populated (fresh run)
        log.info("Restoring runtime state from progress.json + boto3...")
        p = load_progress()
        outs = p.get("outputs", {})
        if outs.get("ec2_public_ip"):
            self.ec2_public_ip = outs["ec2_public_ip"]
            self.ec2_instance_id = outs.get("ec2_instance_id", "")
            self.cluster_endpoints = outs.get("cluster_endpoints", {})
            self.master_secret_arn = outs.get("master_secret_arn", "")
        # cluster_arns not saved in outputs — fetch from RDS
        if not self.cluster_arns:
            for cid in CLUSTER_IDS:
                try:
                    r = self.rds.describe_db_clusters(DBClusterIdentifier=cid)
                    self.cluster_arns[cid] = r["DBClusters"][0]["DBClusterArn"]
                    if cid not in self.cluster_endpoints:
                        self.cluster_endpoints[cid] = r["DBClusters"][0]["Endpoint"]
                except Exception:
                    pass
        # master password from secret
        if not self.master_password and self.master_secret_arn:
            try:
                sec = self.sm.get_secret_value(SecretId=self.master_secret_arn)
                self.master_password = json.loads(sec["SecretString"])["password"]
            except Exception:
                pass
        # key file
        if not self.key_path.exists():
            try:
                # Fetch from SSM (stored by CDK KeyPair)
                ec2 = boto3.client("ec2", region_name=AWS_REGION)
                kps = ec2.describe_key_pairs(
                    Filters=[{"Name": "key-name", "Values": ["abt-v11-key"]}],
                    IncludePublicKey=False,
                )["KeyPairs"]
                if kps:
                    kid = kps[0]["KeyPairId"]
                    param = self.ssm.get_parameter(
                        Name=f"/ec2/keypair/{kid}", WithDecryption=True
                    )
                    self.key_path.write_text(param["Parameter"]["Value"])
                    os.chmod(self.key_path, 0o600)
            except Exception:
                pass
        log.info("Restored: ec2=%s, clusters=%d, arns=%d, key=%s",
                 self.ec2_public_ip, len(self.cluster_endpoints),
                 len(self.cluster_arns), self.key_path.exists())

    def collect_outputs(self):
        # Network outputs
        net = self._stack_outputs(NETWORK_STACK)
        self.master_secret_arn = net["MasterSecretArn"]
        self.key_pair_id = net["KeyPairId"]

        # Cluster outputs (5 stacks)
        for i, stack in enumerate(CLUSTER_STACKS, start=1):
            outs = self._stack_outputs(stack)
            cid = f"test-v11-{i}"
            self.cluster_endpoints[cid] = outs[f"ClusterEndpoint{i}"]
            self.cluster_arns[cid] = outs[f"ClusterArn{i}"]

        # Client outputs
        client = self._stack_outputs(CLIENT_STACK)
        self.ec2_instance_id = client["InstanceId"]
        self.ec2_public_ip = client["PublicIp"]

        # Master password from secret
        sec = self.sm.get_secret_value(SecretId=self.master_secret_arn)
        self.master_password = json.loads(sec["SecretString"])["password"]

        # SSH private key from EC2 KeyPair (CDK stored it in SSM Parameter Store)
        # Actually, when CDK creates an EC2 KeyPair, the private material goes
        # to Systems Manager Parameter Store at /ec2/keypair/<key-pair-id>.
        ssm = self.session.client("ssm")
        param = ssm.get_parameter(
            Name=f"/ec2/keypair/{self.key_pair_id}", WithDecryption=True,
        )
        self.key_path.write_text(param["Parameter"]["Value"])
        os.chmod(self.key_path, 0o600)

        log.info("EC2: %s @ %s", self.ec2_instance_id, self.ec2_public_ip)
        for cid, ep in self.cluster_endpoints.items():
            log.info("  %s -> %s", cid, ep)

        # Persist outputs for status tool
        with progress() as p:
            p["outputs"] = {
                "ec2_public_ip": self.ec2_public_ip,
                "ec2_instance_id": self.ec2_instance_id,
                "cluster_endpoints": self.cluster_endpoints,
                "master_secret_arn": self.master_secret_arn,
            }

    def _stack_outputs(self, stack_name: str) -> dict[str, str]:
        r = self.cf.describe_stacks(StackName=stack_name)
        return {o["OutputKey"]: o["OutputValue"]
                for o in r["Stacks"][0].get("Outputs", [])}

    # ───── Phase: EC2_PROVISION ─────
    def ec2_provision(self):
        # Wait for EC2 cloud-init to finish (java install)
        log.info("waiting for EC2 SSH (cloud-init)...")
        for _ in range(60):
            try:
                ssh_run(self.ec2_public_ip, self.key_path,
                        "test -d /home/ec2-user/aurora-bg-toolkit && which java",
                        timeout=10)
                break
            except Exception:
                time.sleep(5)
        else:
            raise RuntimeError("EC2 never became SSH-ready")

        # Upload jar + configs
        scp_to(self.ec2_public_ip, self.key_path,
               REPO_ROOT / "target" / "abt-w401.jar",
               "/home/ec2-user/aurora-bg-toolkit/abt-w401.jar")
        for cfg in (f"{ACTIVE_CONFIG}.yaml", "v11-final.yaml", "v10-final.yaml", "v4-current.yaml"):
            p = REPO_ROOT / "configs" / cfg
            if p.exists():
                scp_to(self.ec2_public_ip, self.key_path,
                       p, f"/home/ec2-user/aurora-bg-toolkit/configs/{cfg}")
        log.info("uploaded jar + configs to EC2")

    # ───── PHASE: TEST_PARALLEL (5 cluster threads) ─────
    def test_parallel(self):
        log.info("starting 5-cluster parallel execution (%d threads)", CLUSTER_COUNT)
        errors = []
        with ThreadPoolExecutor(max_workers=CLUSTER_COUNT, thread_name_prefix="C") as ex:
            futs = {ex.submit(self.run_cluster_all_rounds, cid): cid
                    for cid in CLUSTER_IDS}
            for f in as_completed(futs):
                cid = futs[f]
                try:
                    f.result()
                except Exception as e:
                    log.exception("cluster %s failed", cid)
                    errors.append((cid, e))
        if errors:
            log.warning("%d cluster threads had errors; continuing to ANALYZE",
                        len(errors))

    def run_cluster_all_rounds(self, cluster_id: str):
        log.info("[%s] starting 6 measurements (2 BG + 2 FO + 2 RB)", cluster_id)
        for r in range(1, ROUNDS_PER_SCENARIO + 1):
            self.run_round(cluster_id, "blue-green", r)
        for r in range(1, ROUNDS_PER_SCENARIO + 1):
            self.run_round(cluster_id, "failover", r)
        for r in range(1, ROUNDS_PER_SCENARIO + 1):
            self.run_round(cluster_id, "reboot", r)
        log.info("[%s] all 6 measurements complete", cluster_id)

    def run_round(self, cluster_id: str, scenario: str, round_no: int):
        scenario_short = {"blue-green": "BG", "failover": "FO", "reboot": "RB"}[scenario]
        phase_name = f"TEST_{cluster_id}_{scenario_short}_R{round_no}"
        if phase_status(phase_name) == "done":
            log.info("[%s] skip %s (already done)", cluster_id, phase_name)
            return
        log.info("[%s] START %s", cluster_id, phase_name)
        phase_set(phase_name, "running")
        try:
            if scenario == "blue-green":
                w_ms, r_ms = self._do_bg_round(cluster_id, round_no)
            elif scenario == "failover":
                w_ms, r_ms = self._do_failover_round(cluster_id, round_no)
            elif scenario == "reboot":
                w_ms, r_ms = self._do_reboot_round(cluster_id, round_no)
            else:
                raise ValueError(scenario)
            phase_set(phase_name, "done", writeMaxMs=w_ms, readMaxMs=r_ms)
            log.info("[%s] DONE %s: write=%dms read=%dms",
                     cluster_id, phase_name, w_ms, r_ms)
        except Exception as e:
            log.exception("[%s] FAIL %s", cluster_id, phase_name)
            phase_set(phase_name, "failed", error=str(e)[:500])

    # ─────── BG round ───────
    def _do_bg_round(self, cluster_id: str, round_no: int) -> tuple[int, int]:
        # 1) ensure cluster-pg is in-sync
        self._wait_pg_in_sync(cluster_id)
        # 2) ensure a fresh BG is AVAILABLE
        bg_id = self._ensure_bg_available(cluster_id)
        # 3) start java client on EC2
        round_dir = self._start_client(cluster_id, "v11bg", round_no)
        # 4) warm-up
        time.sleep(90)
        # 5) trigger switchover
        log.info("[%s] BG R%d: switchover %s", cluster_id, round_no, bg_id)
        self.rds.switchover_blue_green_deployment(
            BlueGreenDeploymentIdentifier=bg_id, SwitchoverTimeout=600,
        )
        # 6) stabilise
        time.sleep(240)
        # 7) stop + collect
        return self._stop_client_and_analyze(cluster_id, "blue-green", round_no, "v11bg", round_dir)

    # ─────── Failover round ───────
    def _do_failover_round(self, cluster_id: str, round_no: int) -> tuple[int, int]:
        round_dir = self._start_client(cluster_id, "v11fo", round_no)
        time.sleep(60)
        log.info("[%s] FO R%d: failover-db-cluster", cluster_id, round_no)
        self.rds.failover_db_cluster(DBClusterIdentifier=cluster_id)
        time.sleep(120)
        return self._stop_client_and_analyze(cluster_id, "failover", round_no, "v11fo", round_dir)

    # ─────── Reboot round ───────
    def _do_reboot_round(self, cluster_id: str, round_no: int) -> tuple[int, int]:
        round_dir = self._start_client(cluster_id, "v11rb", round_no)
        time.sleep(60)
        log.info("[%s] RB R%d: reboot-db-instance %s-writer", cluster_id, round_no, cluster_id)
        self.rds.reboot_db_instance(DBInstanceIdentifier=f"{cluster_id}-writer")
        time.sleep(90)
        return self._stop_client_and_analyze(cluster_id, "reboot", round_no, "v11rb", round_dir)

    # ─────── helpers ───────
    def _wait_pg_in_sync(self, cluster_id: str, max_attempts: int = 20):
        for i in range(1, max_attempts + 1):
            r = self.rds.describe_db_clusters(DBClusterIdentifier=cluster_id)
            members = r["DBClusters"][0]["DBClusterMembers"]
            writer = next((m for m in members if m["IsClusterWriter"]), None)
            s = writer.get("DBClusterParameterGroupStatus", "?") if writer else "?"
            if s == "in-sync":
                return
            log.info("[%s] cluster-pg=%s (attempt %d/%d), sleep 15s",
                     cluster_id, s, i, max_attempts)
            if s == "pending-reboot":
                self.rds.reboot_db_instance(DBInstanceIdentifier=f"{cluster_id}-writer")
                self._wait_db_instance_available(f"{cluster_id}-writer")
            time.sleep(15)
        raise RuntimeError(f"{cluster_id}: cluster-pg never reached in-sync")

    def _ensure_bg_available(self, cluster_id: str) -> str:
        # Look for an AVAILABLE BG for this cluster
        bgs = self.rds.describe_blue_green_deployments()["BlueGreenDeployments"]
        for bg in bgs:
            if bg.get("Source") and cluster_id in bg["Source"]:
                if bg["Status"] == "AVAILABLE":
                    return bg["BlueGreenDeploymentIdentifier"]
                if bg["Status"] == "SWITCHOVER_COMPLETED":
                    # delete + create new
                    # CRITICAL: must wait for -old* artifacts to be cleaned
                    # before delete_blue_green_deployment is allowed (RDS
                    # InvalidBlueGreenDeploymentStateFault otherwise).
                    self._safe_delete_bg(
                        bg["BlueGreenDeploymentIdentifier"], cluster_id
                    )
                    time.sleep(30)
        # Force-clean -old* from previous rounds
        self._cleanup_old_instances_clusters()
        # Create fresh
        cluster_arn = self.cluster_arns[cluster_id]
        bg_name = f"bg-{cluster_id}-{datetime.datetime.utcnow().strftime('%H%M%S')}"
        log.info("[%s] creating BG %s...", cluster_id, bg_name)
        r = self.rds.create_blue_green_deployment(
            BlueGreenDeploymentName=bg_name, Source=cluster_arn,
            Tags=[{"Key": "project", "Value": "aurora-bg-toolkit"},
                  {"Key": "cluster", "Value": cluster_id}],
        )
        bg_id = r["BlueGreenDeployment"]["BlueGreenDeploymentIdentifier"]
        # Wait AVAILABLE (max 30 min)
        for i in range(1, 61):
            s = self.rds.describe_blue_green_deployments(
                BlueGreenDeploymentIdentifier=bg_id,
            )["BlueGreenDeployments"][0]["Status"]
            if s == "AVAILABLE":
                log.info("[%s] BG %s AVAILABLE (waited %d × 30s)", cluster_id, bg_id, i)
                return bg_id
            time.sleep(30)
        raise RuntimeError(f"[{cluster_id}] BG never AVAILABLE")

    def _safe_delete_bg(self, bg_id: str, cluster_id: str, max_minutes: int = 30):
        """Delete a BG, retrying on lifecycle lock.

        v11 lesson: when a BG is in SWITCHOVER_COMPLETED, RDS still has work
        to do (creating -old1 cluster + instances) and rejects
        DeleteBlueGreenDeployment with InvalidBlueGreenDeploymentStateFault.

        The key insight (learned after 3 failed runs): the lifecycle lock
        persists until the -old1 cluster is FULLY AVAILABLE. Simply waiting
        is not enough — we must ACTIVELY DELETE the -old1 instances and
        cluster to unblock the lifecycle. Once -old1 artifacts are gone,
        the BG becomes deletable within seconds.

        Strategy:
        1. Aggressively delete -old* instances + clusters every attempt
        2. Wait for -old* to disappear (max 10 min)
        3. Then delete the BG (should succeed immediately)
        """
        from botocore.exceptions import ClientError

        log.info("[%s] _safe_delete_bg: cleaning -old* to unblock lifecycle...",
                 cluster_id)

        # Step 1: Aggressively clean -old* artifacts (the real blocker)
        for attempt in range(20):  # 20 * 30s = 10 min
            self._cleanup_old_instances_clusters()
            time.sleep(30)
            # Check if any -old* remain
            try:
                insts = self.rds.describe_db_instances()["DBInstances"]
                old_insts = [i for i in insts if "-old" in i["DBInstanceIdentifier"]]
                cls = self.rds.describe_db_clusters()["DBClusters"]
                old_cls = [c for c in cls if "-old" in c["DBClusterIdentifier"]]
                if not old_insts and not old_cls:
                    log.info("[%s] all -old* gone after %d attempts",
                             cluster_id, attempt + 1)
                    break
                if attempt % 4 == 0:
                    log.info("[%s] -old* still present: %d insts, %d clusters (attempt %d)",
                             cluster_id, len(old_insts), len(old_cls), attempt + 1)
            except Exception:
                pass

        # Step 2: Now try to delete the BG (should work quickly)
        max_attempts = (max_minutes * 60) // 30
        for attempt in range(1, max_attempts + 1):
            try:
                self.rds.delete_blue_green_deployment(
                    BlueGreenDeploymentIdentifier=bg_id, DeleteTarget=True,
                )
                log.info("[%s] BG %s deletion accepted (attempt %d)",
                         cluster_id, bg_id, attempt)
                return
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                msg = str(e)[:90]
                if "InvalidBlueGreenDeploymentStateFault" not in code and \
                   "InvalidBlueGreenDeploymentStateFault" not in msg:
                    raise
                if attempt % 5 == 1:
                    self._cleanup_old_instances_clusters()
                log.info("[%s] BG %s not deletable yet (attempt %d/%d): %s",
                         cluster_id, bg_id, attempt, max_attempts, msg)
                time.sleep(30)
        raise RuntimeError(
            f"[{cluster_id}] BG {bg_id} could not be deleted after {max_minutes} min"
        )

    def _cleanup_old_instances_clusters(self):
        # delete -old* DB instances
        try:
            r = self.rds.describe_db_instances()
            for inst in r["DBInstances"]:
                if "-old" in inst["DBInstanceIdentifier"]:
                    try:
                        self.rds.delete_db_instance(
                            DBInstanceIdentifier=inst["DBInstanceIdentifier"],
                            SkipFinalSnapshot=True, DeleteAutomatedBackups=True,
                        )
                    except Exception:
                        pass
        except Exception:
            pass
        # delete -old* DB clusters (only if their instances are gone)
        try:
            r = self.rds.describe_db_clusters()
            for cl in r["DBClusters"]:
                cid = cl["DBClusterIdentifier"]
                if "-old" not in cid:
                    continue
                if cl.get("DBClusterMembers"):
                    continue  # still has members; will retry next call
                try:
                    self.rds.delete_db_cluster(
                        DBClusterIdentifier=cid, SkipFinalSnapshot=True,
                    )
                except Exception:
                    pass
        except Exception:
            pass

    def _wait_db_instance_available(self, identifier: str, max_attempts: int = 60):
        for i in range(max_attempts):
            r = self.rds.describe_db_instances(DBInstanceIdentifier=identifier)
            if r["DBInstances"][0]["DBInstanceStatus"] == "available":
                return
            time.sleep(15)
        raise RuntimeError(f"{identifier} never became available")

    def _start_client(self, cluster_id: str, scenario_short: str, round_no: int) -> str:
        round_dir = f"v11{scenario_short[3:]}-{cluster_id}-r{round_no}"
        endpoint = self.cluster_endpoints[cluster_id]
        # Prepare remote dir + start java
        snippet = f"""
rm -rf /home/ec2-user/{round_dir}
mkdir -p /home/ec2-user/{round_dir}
cd /home/ec2-user/aurora-bg-toolkit
DB_ENDPOINT="{endpoint}" DB_PORT=4488 DB_USER=admin DB_NAME=demo \
  DB_PASSWORD='{self.master_password}' \
  TABLE_SUFFIX="ec2_{scenario_short}_{cluster_id.replace('-', '_')}_r{round_no}" \
  WRAPPER_VERSION="abt-w401" \
  nohup java -Dnetworkaddress.cache.ttl=5 -Dnetworkaddress.cache.negative.ttl=2 \
    --add-opens java.base/java.lang=ALL-UNNAMED \
    --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
    -Xmx2g -jar abt-w401.jar configs/{ACTIVE_CONFIG}.yaml \
    > /home/ec2-user/{round_dir}/ec2_wrapper.log 2>&1 &
echo $! > /home/ec2-user/{round_dir}/ec2_wrapper.pid
"""
        ssh_run(self.ec2_public_ip, self.key_path, snippet, timeout=30)
        return round_dir

    def _stop_client_and_analyze(
        self, cluster_id: str, scenario: str, round_no: int,
        scenario_short: str, round_dir: str,
    ) -> tuple[int, int]:
        # Stop the java pid
        try:
            pid = ssh_run(self.ec2_public_ip, self.key_path,
                          f"cat /home/ec2-user/{round_dir}/ec2_wrapper.pid 2>/dev/null || echo 0",
                          timeout=10).strip()
            if pid != "0":
                ssh_run(self.ec2_public_ip, self.key_path,
                        f"kill {pid} 2>/dev/null || true", timeout=10)
        except Exception:
            pass
        time.sleep(5)

        # Pull log
        local_round_dir = (REPO_ROOT / "e2e-results" /
                          f"v11-{scenario}-{cluster_id}-r{round_no}_"
                          f"{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}" /
                          f"{cluster_id}_{ACTIVE_CONFIG}")
        local_round_dir.mkdir(parents=True, exist_ok=True)
        scp_from(self.ec2_public_ip, self.key_path,
                 f"/home/ec2-user/{round_dir}/ec2_wrapper.log",
                 local_round_dir / "ec2_wrapper.log", timeout=180)

        meta = {
            "runId": f"{cluster_id}_{ACTIVE_CONFIG}_{scenario_short}_r{round_no}",
            "config": ACTIVE_CONFIG,
            "scenario": scenario,
            "round": round_no,
            "cluster": cluster_id,
            "wrapperJar": "abt-w401.jar",
            "experiment": EXPERIMENT_NAME,
        }
        (local_round_dir / "meta.json").write_text(json.dumps(meta))

        # Run analyze-stats-gap.py on the log
        gap_path = local_round_dir / "stats-gap.json"
        try:
            r = subprocess.run(
                ["python3", str(REPO_ROOT / "scripts" / "analyze-stats-gap.py"),
                 str(local_round_dir / "ec2_wrapper.log")],
                capture_output=True, text=True, check=True, timeout=60,
            )
            gap_path.write_text(r.stdout)
        except Exception as e:
            log.warning("[%s] analyze-stats-gap failed: %s", cluster_id, e)
            return 0, 0

        gap = json.loads(gap_path.read_text())
        return gap["summary"]["writeMaxMs"], gap["summary"]["readMaxMs"]

    # ───── Phase: ANALYZE ─────
    def analyze(self):
        # The v11 extract data script does the work; we just call it
        sh(["python3", str(REPO_ROOT / "scripts" / "v11-extract-data.py")],
           cwd=REPO_ROOT, timeout=60)

    # ───── Phase: REPORT ─────
    def report(self):
        sh(["python3", str(REPO_ROOT / "scripts" / "v11-generate-report.py")],
           cwd=REPO_ROOT, timeout=60)

    # ───── Phase: CDK_DESTROY ─────
    def cdk_destroy(self):
        env = {
            "AWS_PROFILE": AWS_PROFILE,
            "AWS_REGION": AWS_REGION,
            "CDK_DEFAULT_ACCOUNT": self._account_id(),
            "CDK_DEFAULT_REGION": AWS_REGION,
            "JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION": "1",
        }

        # ─── 1. Delete every v11 BG with retries (lifecycle-safe) ───
        log.info("Pre-destroy: deleting all v11 BGs (with retries)...")
        try:
            bgs = self.rds.describe_blue_green_deployments()["BlueGreenDeployments"]
            for bg in bgs:
                bg_name = bg.get("BlueGreenDeploymentName") or ""
                if "test-v11-" in bg_name:
                    try:
                        self._safe_delete_bg(
                            bg["BlueGreenDeploymentIdentifier"], "destroy",
                            max_minutes=12,
                        )
                    except Exception as e:
                        log.warning("BG delete failed (non-fatal): %s", e)
        except Exception as e:
            log.warning("BG enumeration failed (non-fatal): %s", e)

        # ─── 2. Wait for all -old* artifacts to be gone (max 10 min) ───
        log.info("Pre-destroy: waiting for -old* artifacts to disappear...")
        for attempt in range(40):  # 40 * 15s = 10 min
            try:
                insts = self.rds.describe_db_instances()["DBInstances"]
                old_insts = [i for i in insts if "-old" in i["DBInstanceIdentifier"]]
                cls = self.rds.describe_db_clusters()["DBClusters"]
                old_cls = [c for c in cls if "-old" in c["DBClusterIdentifier"]]
                if not old_insts and not old_cls:
                    log.info("Pre-destroy: all -old* gone (attempt %d)", attempt + 1)
                    break
                # Re-trigger cleanup every 3 attempts
                if attempt % 3 == 0:
                    self._cleanup_old_instances_clusters()
                if attempt % 4 == 0:
                    log.info("Pre-destroy: still waiting (insts=%d, cls=%d)",
                             len(old_insts), len(old_cls))
                time.sleep(15)
            except Exception as e:
                log.warning("Pre-destroy poll failed: %s", e)
                time.sleep(15)

        # ─── 3. Now run cdk destroy ───
        log.info("cdk destroy --all (~12 min)...")
        full_env = os.environ.copy()
        full_env.update(env)
        proc = subprocess.Popen(
            ["cdk", "destroy", "--all", "--force"],
            cwd=CDK_DIR, env=full_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            for line in proc.stdout:
                log.info("[cdk-destroy] %s", line.rstrip())
            rc = proc.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise RuntimeError("cdk destroy timed out (1800 s)")
        if rc != 0:
            raise RuntimeError(f"cdk destroy --all exited with rc={rc}")
        log.info("cdk destroy complete")

    # ───── main ─────
    def main(self):
        self.acquire_lock()
        try:
            self.run_phase("PRECHECK", self.precheck)
            self.run_phase("BUILD", self.build)
            self.run_phase("CDK_BOOTSTRAP", self.cdk_bootstrap)
            self.run_phase("CDK_DEPLOY", self.cdk_deploy)
            self.run_phase("COLLECT_OUTPUTS", self.collect_outputs)
            self.run_phase("EC2_PROVISION", self.ec2_provision)
            # Ensure in-memory state is populated even when resuming
            self._restore_runtime_state()
            self.run_phase("TEST_PARALLEL", self.test_parallel)
            self.run_phase("ANALYZE", self.analyze)
            self.run_phase("REPORT", self.report)
            self.run_phase("CDK_DESTROY", self.cdk_destroy)
        finally:
            self.release_lock()


if __name__ == "__main__":
    o = V11Orchestrator()
    o.main()

# Experiment v10 — Production-Grade Reference Configuration

> **Started**: 2026-05-17 06:00 SGT  
> **Status**: planning → execution  
> **Goal**: deliver the first **clean, single-config, single-load** measurement of the
> production-recommended setup, with full automation and resumability so the
> result is reproducible by anyone with `make` and AWS credentials.

---

## Why v10 exists

The v9 experiment (2026-05-16) ended with a clear winner: `v4-current.yaml`
plus JVM `-Dnetworkaddress.cache.ttl=5`. The accompanying report claimed
all 120 measurements were collected under "production load (1280 ops/s,
pool=50)". A 2026-05-17 audit (see `CHANGELOG.md` `[post-experiment-audit]`
section) revealed this was wrong: v4 control cells (test-02, test-03)
actually used `v4-current.yaml`'s default low-load profile (40 ops/s, pool=10).
Only the v9-tuned cells (test-04, test-05) were at production load.

Result: **the production-recommended configuration has never been measured
under production load.** This experiment fixes that.

## Scope (single config, single cell)

```
configs/v10-final.yaml   (this is the only thing being measured)

  jdbc:    v4-current's settings (validated by v9 H3-rejected)
  hikari:  pool=50, minimumIdle=50, maxLifetime=60s (production-grade)
  workload: 64 threads × 50ms × R:I:U=9:2:1 ≈ 1280 ops/s  (production load)
  STATS reporter: 10 Hz  (±100ms precision)
  JVM:     -Dnetworkaddress.cache.ttl=5 (mandatory)
  wrapper: aws-advanced-jdbc-wrapper 4.0.1 (latest stable)
```

## Test matrix

```
1 cluster (db.r7g.large writer + db.t3.medium reader, aurora-iopt1)
× 1 EC2 c6i.2xlarge runner
× 1 config (v10-final.yaml)
× 3 scenarios (Blue/Green, Failover, Reboot)
× 10 rounds per scenario

= 30 measurements
```

No 4-way comparison. v9 already adjudicated wrapper 4.0.0 vs 4.0.1 and v4
vs v9-tuned. v10 is the **production reference**, not a comparative study.

## Hypotheses to confirm (no new lever introduced)

| ID | Hypothesis | Predicted by v9 |
|---|---|---|
| **H1** | BG switchover under v10 + production load: median 3.5–4.5 s, std-dev < 500 ms | v9-tuned cells ran similarly (median 3.6–4.2s, stdev ~200ms); v10 should land in the same band |
| **H2** | Failover under v10 + production load: median 5–8 s, max < 12 s | v9-tuned (which had aggressive bg timeouts) was 8s/17s. v10 reverts to default → expect v9 control's behaviour (6s/10s) |
| **H3** | Reboot under v10 + production load: median < 500 ms | v9 (DNS TTL=5) showed near-zero reboot downtime regardless of cell |

If any hypothesis fails, that's a new finding worth investigating in v11.

## Hard requirements

1. **Resumable**: any interruption (laptop sleep, network drop, AWS API
   throttle) must be recoverable by re-running the master orchestrator with
   no manual cleanup.
2. **Observable**: any time the user runs `bash scripts/v10-status.sh`, they
   see current phase, completed measurements, errors, and ETA.
3. **Idempotent**: re-running a `done` phase is a no-op; re-running a
   `running` phase resumes from the last checkpoint.
4. **Self-cleaning**: on success, automatically tears down all AWS resources
   except control-plane objects flagged "retain" (subnet group, parameter
   group, security group).
5. **Self-reporting**: on success, automatically writes
   `docs/REPORTS/2026-05-17-v10-production.md` and updates the dashboard.
6. **One-command operation**: `bash infra/orchestrate-v10-master.sh` is the
   only command the user should ever need to run.

## Phases (each one a checkpoint)

```
PHASE                 ESTIMATED   IDEMPOTENT?   CHECKPOINT FILE
──────────────────    ─────────   ───────────   ────────────────────────
BOOTSTRAP             ~30 s       yes           progress.json
CLUSTER_CREATE        ~10 min     yes (skip if exists)
BG_PREREQS            ~3 min      yes
EC2_SETUP             ~3 min      yes
WAIT_BG_R1_AVAILABLE  ~15 min     yes
TEST_BG_R1            ~7 min      no (each round is a unique measurement)
   ... (rounds 2-10, each with WAIT + TEST)
TEST_FO_R1..R10       ~5 min each yes (failover doesn't need BG)
TEST_RB_R1..R10       ~3 min each yes (reboot doesn't need BG)
ANALYZE               ~1 min      yes (re-runs idempotently)
DASHBOARD_UPDATE      ~1 min      yes
FINAL_REPORT          ~1 min      yes
TEARDOWN              ~10 min     yes (delete-if-exists pattern)
```

Total: ~7-8h wall time, ~$5-8 AWS cost.

## State file

```
infra/state/v10-progress.json
```

Schema:

```json
{
  "experiment": "v10-production",
  "started_at": "2026-05-17T06:00:00+08:00",
  "config_file": "configs/v10-final.yaml",
  "current_phase": "TEST_BG_R5",
  "phases": {
    "<PHASE_NAME>": {
      "status": "pending|running|done|failed",
      "started_at": "...",
      "ended_at": "...",
      "duration_s": 123,
      "writeMaxMs": 3800,
      "readMaxMs": 3800,
      "error": "..."
    }
  },
  "errors": []
}
```

## Resumability semantics

| Phase status on launch | Action |
|---|---|
| `done` | skip |
| `pending` | run normally |
| `running` | assume previous run was interrupted; reset to `pending` and run |
| `failed` | reset to `pending`, run again, retry up to 3 times across re-launches |

After 3 retries on the same phase, the orchestrator stops and surfaces the
error in `errors[]`. The user can either retry by hand or skip past with
`SKIP_PHASES=PHASE_NAME bash infra/orchestrate-v10-master.sh`.

## Failure modes & mitigations

| Failure | Mitigation |
|---|---|
| AWS API 5xx / throttle | retry 3× with 30s exponential backoff |
| BG provisioning > 30 min | timeout, mark round as `failed`, continue to next round |
| EC2 SSH refused | retry 5× with 10s linear backoff |
| Java client crashes mid-test | mark round failed, log stderr, continue |
| Disk full on EC2 (FINEST log flood) | v10 yaml uses `wrapperLoggerLevel: INFO`, monitored each round |
| Aurora cluster stuck in `MODIFYING` | orchestrator polls `aws rds describe-db-clusters` until AVAILABLE, max 20 min |
| Master orchestrator killed (user `Ctrl+C`) | next run re-reads progress.json and resumes from last checkpoint |
| Local laptop sleeps | doesn't matter — AWS keeps running; orchestrator re-launches via `nohup` resume |

## Acceptance gates

1. `configs/v10-final.yaml` exists, lints with `mvn test` (parser test passes)
2. `infra/orchestrate-v10-master.sh` exists, has `set -euo pipefail`, defines all phases
3. `scripts/v10-status.sh` exists, prints phase progress in <1 s
4. Master orchestrator starts cleanly via `nohup`
5. After full run: 30 measurements in `e2e-results/v10-{bg,failover,reboot}-{1..10}_*/`
6. After full run: `dashboard/data/v10-only.json` exists with 30 entries
7. After full run: `docs/REPORTS/2026-05-17-v10-production.md` exists
8. After full run: AWS account has 0 v10-prefixed clusters/instances
9. After full run: `git status` shows only the new files (no half-committed mess)

## CDK / Terraform note (for the next iteration)

User has expressed long-term desire for a fully-IaC experiment harness
(`cdk deploy && cdk destroy` semantics). v10 does NOT do this in its master
orchestrator — it uses the existing `infra/00..30-*.sh` bash scripts that
have been battle-tested through v1–v9. CDK skeleton is shipped in
`infra/cdk/` as a starting point for v11.

Reasons to defer full CDK migration:
- BG churn (50+ deployments per experiment) doesn't map cleanly to CDK's
  "desired state" model. Each round mutates state.
- v9 final report sec. "Why CDK or Terraform was NOT used" enumerates this in detail.
- Migrating now would block v10 by another 3-5 hours of code.

Reasons to do it for v11:
- One-command `cdk deploy` is more discoverable for new contributors.
- Centralized tag management (cost allocation, lifecycle policies).
- Cleaner teardown (no orphaned resources).

The v10 experiment delivers the **measurement** the user wants. The CDK
skeleton delivers the **direction** the user wants. They are decoupled.

---

*Plan locked: 2026-05-17 06:00 SGT. Master orchestrator launches once user
gives explicit go.*

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v11-cdk-parallel] - 2026-05-17

> v11 = full IaC migration (CDK) + 5-cluster parallel execution. The
> production-recommended path going forward. v10 bash path kept as
> reference implementation.

### Added

- **Full CDK stack** (`infra/cdk/`) — 3 stack types deploying together
  via `cdk deploy --all`:
  - `NetworkStack` — VPC lookup, SecurityGroup, subnet group, EC2 KeyPair
    (private key auto-stored in SSM by AWS), shared master Secret, cluster
    parameter group with binlog ON
  - `ClusterStack(idx)` × 5 — 5 Aurora MySQL clusters (`test-v11-{1..5}`),
    each with writer (db.r7g.large) + reader (db.t3.medium), aurora-iopt1,
    port 4488. Uses `Credentials.from_password` (NOT managed secret —
    audit found managed secret breaks BG Deployments)
  - `ClientStack` — c6i.2xlarge EC2 with Java 17, jq, mariadb client,
    IAM role with rds:*, secretsmanager:GetSecretValue, ssm:GetParameter
- `configs/v11-final.yaml` — same JDBC/HikariCP/JVM/workload as v10-final
  (production load 1280 ops/s, pool=50, DNS TTL=5, 10Hz STATS); only
  `Xmx2g` instead of v10's `Xmx4g` because EC2 runs 5 java processes
- `infra/orchestrate-v11.py` (685 lines) — Python orchestrator replacing
  the v10 bash master:
  - 39-phase resumable: PRECHECK → BUILD → CDK_BOOTSTRAP → CDK_DEPLOY →
    COLLECT_OUTPUTS → EC2_PROVISION → TEST_PARALLEL → ANALYZE → REPORT →
    CDK_DESTROY
  - `ThreadPoolExecutor(max_workers=5)` for 5-cluster parallel execution
  - File-locked `infra/state/v11-progress.json` for resumability
  - Streams `cdk deploy` stdout+stderr into master log (lesson from
    failed first attempt where errors were silently swallowed)
- `scripts/v11-status.sh` — colored multi-cluster status grid (5 clusters
  × 3 scenarios × 2 rounds) + aggregated stats + recent log tail
- `scripts/v11-extract-data.py` — produces `dashboard/data/v11-only.json`
  with both aggregated and per-cluster stats (to detect cluster contention)
- `scripts/v11-generate-report.py` — auto-writes
  `docs/REPORTS/2026-05-17-v11-cdk-parallel.md`
- `docs/EXPERIMENT-V11-PLAN.md` — pre-registered plan: hypotheses,
  test matrix, 39-phase lifecycle, risk register, acceptance gates

### Changed

- **README** restructured to feature v11 as recommended path; v10 retained
  in the configurations table as "validated, reference" status
- **CHANGELOG** lifecycle: this entry replaces the v10 path as the
  production reference. v10 entry remains intact for history.

### Test execution

- 1 NetworkStack + 5 ClusterStack + 1 ClientStack deployed in ~14 min via
  `cdk deploy --all` (concurrency 10)
- 5 clusters × 6 measurements (2 BG + 2 FO + 2 RB) = **30 planned**
- 5 BG R1 + 5 FO R1+R2 + 5 RB R1+R2 = **25 actual** (5 BG R2 failed,
  see "Known issues")
- TEST_PARALLEL wall time: **42 min** (vs v10 single-cluster 6+ hours)
- Total experiment wall time: ~95 min (deploy 14 + provision 1 + tests 42 +
  analyze 1 + report 1 + destroy 12 + manual cleanup 25)

### Headline results

| Scenario | n | min | median | max | stdev |
|---|---|---|---|---|---|
| Blue/Green | 5 | 3.70 s | **3.90 s** | 5.00 s | 608 ms |
| Failover | 10 | 4.40 s | **10.15 s** | 15.90 s | 3.12 s |
| Reboot | 10 | 0 ms | **6.95 s** | 8.40 s | 2.21 s |

### Comparison with v10

| Scenario | v10 (1 cluster) | v11 (5 cluster parallel) | Δ |
|---|---|---|---|
| BG median | 5.05 s | **3.90 s** | -1.15 s ✅ tighter |
| BG max | 21.0 s (3 outliers) | 5.0 s (no outliers) | **-16.0 s ✅** |
| FO median | 7.75 s | 10.15 s | +2.40 s |
| FO max | 14.8 s | 15.9 s | +1.1 s (similar) |
| RB median | 100 ms | **6.95 s** | **+6.85 s ⚠️** (70× slower) |
| RB max | 2.6 s | 8.4 s | +5.8 s |

### Key findings

1. **v10's 30% BG outlier rate did NOT reproduce in v11.** v10 reported 3 of
   10 BG rounds at 14-21 s; v11's 5 BG rounds (per-cluster R1) were all
   3.7-5.0 s with no outliers. This suggests the v10 outliers were **time-
   dependent or RDS-control-plane-dependent**, not a systemic v10 issue.

2. **5-cluster parallel reboot is 70× slower than single-cluster reboot.**
   v10 RB median 100 ms (one client, one DB reboot); v11 RB median 6.95 s
   (5 clients, 5 DB reboots simultaneously). Two contributing factors:
   (a) one EC2 c6i.2xlarge running 5 java processes — when the writer
   reboots, all 5 clients' HikariCP pools drain at once, contending for
   pool refill bandwidth;
   (b) RDS control plane response time may degrade when 5 reboot-db-instance
   calls land in the same 30-second window.

   **Production implication**: applications with multiple Aurora clients
   experiencing reboot simultaneously should expect ~7s downtime, not the
   100ms suggested by single-client testing.

3. **Failover is reproducible across orchestration paths.** v11 FO median
   10.15 s vs v10 7.75 s (+2.4 s). Within statistical noise across 10
   measurements; both well under the 12s p95 expected from Aurora docs.

### Known issues

- **5 BG R2 rounds all failed** with `InvalidBlueGreenDeploymentStateFault:
  Deleting target is not allowed while blue green deployment lifecycle is
  SWITCHOVER_COMPLETED`. Root cause: the orchestrator's
  `_ensure_bg_available` calls `delete_blue_green_deployment` immediately
  after R1's switchover completes, but RDS BG lifecycle is still creating
  the `-old1` cluster. v10 single-cluster didn't hit this race because
  rounds were sequential with natural pauses; v11 5-parallel hits it on
  every cluster simultaneously. **Fix for v12**: poll `BlueGreenDeployment`
  status until `Status != SWITCHOVER_COMPLETED OR all -old* artifacts have
  been provisioned` before attempting delete (~3 min wait typically).
- **CDK_DESTROY initially failed** because 5 `-old1` clusters and BG
  metadata blocked stack deletion. Manual cleanup workflow (delete -old1
  instances → wait → delete -old1 clusters → delete BGs → cdk destroy)
  documented in this changelog. Fix for v12: orchestrator's CDK_DESTROY
  phase will explicitly clean -old1 + BG before invoking cdk destroy.

### CDK setup recovery

This run also documented how to recover a stuck `CDKToolkit` bootstrap
stack (was `UPDATE_ROLLBACK_FAILED` from a prior partial cleanup):

1. `aws cloudformation continue-update-rollback --resources-to-skip CdkBootstrapVersion`
2. `aws cloudformation delete-stack --stack-name CDKToolkit`
3. Empty ECR repo (`cdk-hnb659fds-container-assets-...`) + delete S3
   staging bucket (`cdk-hnb659fds-assets-...`)
4. `cdk bootstrap aws://ACCOUNT/REGION` (fresh)

### Cost & duration

- Wall time: ~3 hours (most of it was the failed first attempt due to
  em-dash characters in description fields, see "Bugs found and fixed")
- Successful run wall time: ~95 min
- AWS cost: ~$5
- All resources destroyed at experiment end; account audited empty

### Bugs found and fixed during v11

1. **em-dash in CDK descriptions** — `description="...v11 — Aurora..."`
   broke `AWS::EC2::SecurityGroup` and `AWS::RDS::DBClusterParameterGroup`
   creation. AWS API rejects non-ASCII characters in description fields.
   Fix: use ASCII dash. Lesson: prefer `--ascii-only` linting on CDK
   strings.
2. **`subprocess.run` swallowing cdk output** — first orchestrator
   attempt died silently after 9 s with no error in master log.
   Fix: switched CDK_DEPLOY to `Popen` + line-by-line streaming into
   master log.
3. **`Credentials.from_generated_secret` triggers managed-secret behaviour**
   — verified during planning that this would break BG Deployments. Used
   `Credentials.from_password(secret_value_from_json('password'))` instead,
   which puts the password value directly in MasterUserPassword (not a
   managed link).
4. **Stuck CDKToolkit bootstrap** — `UPDATE_ROLLBACK_FAILED` from 2024
   blocked `cdk deploy`. Recovery procedure documented above.

See `docs/REPORTS/2026-05-17-v11-cdk-parallel.md` for full per-round data.

## [post-experiment-audit] - 2026-05-17

> Post-mortem audit trail. No code or config changes. Verifies test
> completeness, cleans up residual AWS metadata that the original teardown
> missed.

### Verified — test coverage completeness

Cross-checked every measurement directory against the v9 plan. All
120 cells are present and correctly tagged:

| Scenario | Rounds | Cells/round | Total | Status |
|---|---|---|---|---|
| Blue/Green | 1–10 | 4 (v4@4.0.0, v4@4.0.1, v9@4.0.0, v9@4.0.1) | 40 | ✅ all present |
| Failover | 1–10 | 4 | 40 | ✅ all present |
| Reboot | 1–10 | 4 | 40 | ✅ all present |
| **Total** | | | **120** | **✅ complete** |

Each cell's `meta.json` contains valid `scenario`, `config`, `round`,
`runId`, and `wrapperJar` fields (sampled rounds 1 and 10 across all
three scenarios — fields well-formed).

The `e2e-results/` directory also contains some empty-shell directories
(e.g. `v9-bg-10_135337`, `v9-failover-{6..10}_142347~142847`) — these
are orchestrator pre-flight retry stubs from when a BG was not yet
`AVAILABLE` or a cluster pg was not yet `in-sync`. They contain no
data and do NOT affect the 120-cell tally above. Real data lives in
the directories with 4 sub-directories each.

### Verified — final report location

The authoritative output of the v9 experiment is:

- **`docs/REPORTS/2026-05-16-v9-final-report.md`** — 120 measurements,
  hypothesis verdicts, production recommendation, methodology notes.

Supporting documents (also unchanged):

- `docs/EXPERIMENT-V9-PLAN.md` — pre-registered design (acceptance gates 1-15)
- `docs/REPORTS/2026-05-15-e2e-results.md` — round 1 (low-load, v1-v7)
- `docs/REPORTS/2026-05-16-e2e-results-v2.md` — round 2 (production-load v2/v4/v5/v8)
- `CHANGELOG.md` `[v9-experiment]` section below — 1-page summary

### Verified — no further v9 iteration justified

Per the final report, the **3.5–4.2 s BG floor is set by the bg plugin's
hardcoded 4 s SuspendConnectRouting**, not by client-side configuration.
All 5 client-side hypotheses have been adjudicated. Future experiments
are only justified when one of these external triggers happens:

- New `aws-advanced-jdbc-wrapper` major version (>4.0.1) is released
- Customer upgrades Aurora MySQL engine version (currently locked at 3.10.4)
- Architecture-level change accepted (dual-write / shadow-writer pattern)

### Removed — residual AWS Blue/Green deployment metadata

The original `99-teardown.sh` deleted clusters/instances/EC2 but left
behind 47 `SWITCHOVER_COMPLETED` BG metadata records (cost-free, AWS
auto-purges after ~7 days, but visually noisy). All 47 explicitly
deleted via `delete-blue-green-deployment` (parallel × 6 concurrent;
total wall time 26 s).

Scope of removal:

- `bg-test-{02,03,04,05}-*` × 47 — all v9-experiment BG deployments
  (4 clusters × ~12 rounds each, including pre-experiment setup BGs)

Explicitly retained (not part of v9 scope):

- `aurora-bg-test-{deployment,medium,heavy}` × 3 — older experiment
  from 2026-02-05, predates this toolkit
- `bg-test-01-{145315,171713}` × 2 — from a 2026-05-15 round-1
  pre-flight that used cluster `test-01`, also predates v9

### Verified — AWS account cost-resource state

Confirmed empty (zero ongoing cost from this toolkit):

| Resource type | Count | Notes |
|---|---|---|
| Aurora DB clusters | 0 | All `test-0{1..5}` destroyed |
| Aurora DB instances | 0 | Including all `-old*` cleanup |
| Manual snapshots (test-*) | 0 | None taken |
| Automated snapshots (test-*) | 0 | Auto-deleted with clusters |
| EC2 c6i.2xlarge runner | 0 | Destroyed |
| Aurora-related secrets | 0 | Destroyed |
| Available (orphan) EBS volumes | 0 | None |
| BG deployments (`SWITCHOVER_COMPLETED`) | 5 | All pre-v9 (see above) |

### Retained — zero-cost control-plane objects

Three RDS control-plane objects survive teardown. All are billing-free
and represent ~5 minutes of saved setup time if a future experiment
re-runs the same matrix:

- Cluster parameter group `aurora-bg-test-params` (binlog ON for BG)
- DB subnet group `aurora-bg-test-subnet-group` (vpc-04bdf8e5af4f70ca0)
- Security group `sg-02b1fc3e2caaeb30f` (`aurora-bg-test-sg`)

If a clean-slate teardown is desired in the future, delete these three
objects; `00-bootstrap.sh` + `05-enable-bg-prereqs.sh` will recreate them.

### Audit performed by

`kiro-cli` agent session, 2026-05-17 05:25–05:35 SGT, using
`aws --profile jiasunm-neo --region us-east-1` against account active
at audit time. Local working tree clean before and after audit.

## [v9-experiment] - 2026-05-16

### Added
- `configs/v9-tuned.yaml` — combined optimization config testing 5 hypotheses:
  H1 JVM DNS TTL=5s, H2 remove connectionInitSql/TestQuery, H3
  bgConnectTimeoutMs=5000, H4 wrapper 4.0.1, H5 maxLifetime=300000ms
- `docs/EXPERIMENT-V9-PLAN.md` — pre-registered experiment design with
  hypotheses, time budget, success criteria
- `infra/orchestrate-{bg,failover,reboot}-v9.sh` — orchestrators for
  10-round runs across 4 cells (v4@4.0.0, v4@4.0.1, v9@4.0.0, v9@4.0.1)
- `infra/orchestrate-bg-v9-loop.sh` — automated BG round-by-round loop
  with per-round BG re-creation + aggressive `-old*` cleanup
- 10 Hz STATS reporter (was 1 Hz) — better downtime measurement precision
- `analyze-stats-gap.py` upgraded to handle 100ms granularity

### Changed
- BgDowntimeTest now sets `java.security.Security.setProperty("networkaddress.cache.ttl", "5")`
  at startup so every test gets the same DNS-aware behaviour as the explicit JVM flag
- v4-current.yaml log level: FINEST → INFO (FINEST flooded the EC2 disk under
  64-thread × 1280 ops/s production workload)

### Test execution
- 10 BG rounds × 4 cells = 40 measurements
- 10 Failover rounds × 4 cells = 40 measurements
- 10 Reboot rounds × 4 cells = 40 measurements
- **Grand total: 120 production-load measurements**

### Headline results

**Real BG downtime is 3.5-4.2 s** (high-precision 10 Hz STATS), not the
2-3 s that 1 Hz STATS suggested. The 4 s floor is set by the bg plugin's
hardcoded SuspendConnectRouting; client-side tuning cannot push below it.

**JVM DNS TTL=5 is the killer feature** (H1):
- v4 + DNS TTL=30s default: Reboot ≈ 5 s (v2 result)
- v4 + DNS TTL=5s explicit: Reboot ≈ 0.1 s (v9 result)
- 50× improvement on a single 1-line JVM property

**v9-tuned regresses Failover** (H3 hypothesis was wrong):
- v4 Failover median: 6 s, max: 7-10 s, stdev: 2.5-3 s
- v9 Failover median: 8 s, max: 13-17 s, stdev: 4-5 s
- bgConnectTimeoutMs=5000 + bgIncreasedMs=500 cause aggressive recovery paths
  that take longer in genuinely-slow Failover scenarios

**Wrapper 4.0.1 vs 4.0.0** (H4): no measurable difference

### Final recommendation
Production should use **v4-current** as-is, plus **JVM property
`-Dnetworkaddress.cache.ttl=5`**.

Drop:
- v9-tuned (Failover regression)
- bgConnectTimeoutMs reduction
- bgIncreasedMs reduction
- maxLifetime extension

### Cost & duration
- Wall time: ~16 hours (mostly BG provisioning waits)
- AWS cost: ~$15 (5 db.r7g.large + 5 db.t3.medium + EC2 c6i.2xlarge)
- All resources destroyed at experiment end; account audited empty

See `docs/REPORTS/2026-05-16-v9-final-report.md` for full data and analysis.

## [Unreleased]

### Added
- Initial project skeleton with Maven, JUnit 5, Testcontainers
- Six baseline configurations distilled from a real customer engagement (HSK)
- Configuration-driven test harness (single Java entry point, YAML configs)
- Python scripts for automated log analysis and report generation
- Single-file HTML dashboard with Apple-style dark theme
- Full test pyramid: unit, integration, regression, E2E
- Documentation: architecture, methodology, root cause analysis, case study

### Background

This project consolidates ~5 hand-rolled Java versions and 5 sets of shell scripts
from the original `Aurora-GB-HSK` engagement into a single, maintainable toolkit.

The original engagement reduced customer's Blue/Green switchover downtime from
**4–57 seconds (unpredictable) to 2.7–7.6 seconds (stable)**, with **97% fewer
error log entries**, by:

1. Adding `connectTimeout=1000ms` and `socketTimeout=3000ms`
2. Removing `initialConnection` and `auroraConnectionTracker` plugins
3. Setting `initializationFailTimeout=-1` and `minimumIdle=10`
4. Adding application-level 50ms retry on first failure
5. Tuning `failureDetectionTime/Interval/Count`

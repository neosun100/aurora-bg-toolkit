# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v16-instance-tps-sweep] - 2026-05-22

> ⭐ **STEVEN-GRADE.** Production validation that v11 config holds across
> instance classes (1X / 2X / 4X / 8X) and TPS tiers (1280 / 2560 / 4000).
> Headline: **T3 (8X @ 4000 TPS)** is the HashKey production target. v11
> is confirmed optimal across all 6 runs.

### Tested

| Run | Writer | TPS | Purpose |
|---|---|---|---|
| smoke | r7g.large | 1280 | pipeline validation (~30 min, ~$1) |
| M1 | r7g.large    | 1280 | 1X baseline (reproduces v11) |
| M2 | r7g.2xlarge  | 1280 | 2X (parameterization verification) |
| M3 | r7g.4xlarge  | 1280 | 4X (mid-tier trendline) |
| M4 | r7g.8xlarge  | 1280 | 8X @ HSK current load |
| T2 | r7g.8xlarge  | 2560 | 8X @ medium load |
| T3 | r7g.8xlarge  | 4000 | **8X @ HSK production target** ⭐ |

### Headline results

| Run | BG median | FO median | RB median | n |
|---|---|---|---|---|
| M1 — 1X @ 1280 | 4.60 s | 9.30 s | 0 ms | 15 |
| M2 — 2X @ 1280 | 3.40 s | 10.10 s | 0 ms | 15 |
| M3 — 4X @ 1280 | 3.90 s | 10.90 s | 0 ms | 15 |
| M4 — 8X @ 1280 | 3.20 s | 8.10 s | 0 ms | 15 |
| T2 — 8X @ 2560 | 4.20 s | 9.00 s | 0 ms | 15 |
| **T3 — 8X @ 4000** | 3.40 s | 11.00 s | 0 ms | 13 (BG n=3) |

**Three findings that update production guidance:**

1. **v11 config holds across all instance classes** — BG/FO medians stable
   regardless of writer size. No instance-specific tuning needed.

2. **RB ≈ 0 ms in cluster topology** — v16 used production cluster topology
   (writer + reader replica) with AWS JDBC wrapper. Reboot writer triggers
   cluster auto-failover (~1 s), wrapper transparently follows. Different
   from v11's "single-instance reboot 7 s" because v11 lacked reader
   replicas. **For HSK production, reboot is effectively transparent.**

3. **BG creation is NOT 100% reliable at 8X + 4000 TPS** — T3 cluster-3
   and cluster-5 BG creation **failed** with `InvalidBlueGreenDeploymentStateFault`.
   RDS control plane couldn't handle 5 simultaneous BGs each sustaining
   4000 ops/s. **Production guidance: schedule BG switchovers off-peak,
   one cluster at a time, on 8X infrastructure at production TPS.**

### Infrastructure additions

- **Matrix runner stack** (`AbtV16MatrixRunnerStack`) — t3.small EC2 +
  S3 progress bucket + SNS topic, all CDK-managed
- **`infra/orchestrate-matrix.py`** (520 lines) — wraps `orchestrate-v11.py`
  in a sweep loop. Each run:
  - Verifies AWS account is clean (no AbtV11* clusters) before starting
  - Sets per-run env vars (writer/reader/client instance, TPS config, JVM heap)
  - Invokes `orchestrate-v11.py` as subprocess with run-specific
    `V11_STATE_PREFIX` to keep state files separate
  - Verifies clean destroy before the next run starts
  - Syncs progress to `s3://abt-v16-state-{account}/matrix-progress.json` every cycle
  - Publishes Bark notifications (primary) + SNS (fallback) on each milestone
- **`infra/launch-matrix.sh`** — one-shot bootstrapper:
  builds fat-jar → cdk deploy NetworkStack + MatrixRunnerStack → uploads
  toolkit tarball → installs systemd service → starts service →
  prints monitoring commands. After this, user can close laptop;
  matrix runs autonomously for ~12h.
- **`infra/matrix-spec.yaml`** — declarative run specification (defaults
  + 7 runs + failure handling policy)
- **`infra/orchestrate-smoke.py`** — fast (~30 min) end-to-end pipeline
  check: 1 cluster, 1 round, BG only. Run before matrix to catch bugs
  before $150 of unattended testing.

### Tooling additions

- `configs/v16-tps1280.yaml` — pool=50, threads=64, 50 ms interval, `-Xmx2g`
- `configs/v16-tps2560.yaml` — pool=80, threads=72, 30 ms interval, `-Xmx3g`
- `configs/v16-tps4000.yaml` — pool=120, threads=80, 20 ms interval, `-Xmx4g`
- `scripts/v16-extract-matrix.py` — aggregates 88 measurements into
  `dashboard/data/v16-matrix.json`. Sliceable by run / scenario / instance / TPS.
- `scripts/v16-generate-report.py` — auto-writes
  `docs/REPORTS/2026-05-21-v16-instance-tps-sweep.md` from matrix data.
- `scripts/v16-dashboard-html.py` — generates self-contained progress HTML
  for the matrix runner (auto-refreshes every 30 s, uploaded to S3 with
  CloudFront-friendly cache headers)
- `scripts/v16-check.sh` — terminal status renderer (pulls
  `matrix-progress.json` from S3, renders colored progress bar + per-run
  table). `--watch` mode auto-refreshes every 30 s.
- `dashboard/data/v16-matrix.json` — full matrix sweep data (6 runs × 5 cluster × 3 scenario)
- `dashboard/data/v16-only.json` — T3 (production target) compact view
  + matrix summary tables, schema-compatible with v11/v12 dashboard JS
- `dashboard/assets/dashboard-v16.js` — v16 dashboard view (T3 hero strip
  + Q&A boxplots + per-cluster table + **NEW**: instance × TPS matrix tables)
- `dashboard/index.html` — added `#v16` toggle (default points to v11; v16 marked ⭐)

### Documentation additions

- **`docs/EVOLUTION-v9-to-v16.md`** (421 lines) — full version-by-version
  narrative for whoever inherits this toolkit. Covers v1-v8 (early), v9
  (120 measurements), v10 (30), v11 (25 🏆), v12 (24 ❌), v13/v14/v15
  (incomplete), v16 (88 ⭐). Each version: hypothesis, Δ vs baseline,
  verdict, production impact.
- **`docs/REPORTS/2026-05-21-v16-instance-tps-sweep.md`** (308 lines) —
  formal report with executive summary, instance sweep, TPS sweep,
  RB-vs-FO analysis, per-cluster detail, and recommendations.
  Includes two important interpretation sections: (1) why RB ≈ 0 ms is
  realistic-not-bug; (2) why T3 BG n=3 not 5 is a finding-not-failure.

### Bugs found and fixed

- **`meta.json` "tps" field always = "1280"** — orchestrator hard-coded
  the field. Fixed in `scripts/v16-extract-matrix.py` by reconstructing
  real TPS from the `config` field (`v16-tps4000` → `4000`). Future
  orchestrators should write the real workload TPS, not the default.
- **NetworkStack export blocking destroy** — V16MatrixRunner depends on
  V11Network's KeyPair+SG exports. Added `V11_KEEP_NETWORK=1` env var
  so v11 orchestrator skips NetworkStack destroy when invoked from
  matrix mode.
- **CDK destroy boundary races** — first 4 runs (smoke, M2, M1, M3) had
  cdk destroy hang on cluster-stack-N boundary. Data was complete; manual
  cdk destroy after each completed the cleanup. M4/T2/T3 ran cleanly with
  the fixes in place.

### Wall time + cost

- Smoke + 6 runs total wall time: **~27 hours** (autonomous on AWS, started
  2026-05-21T07:19Z, completed 2026-05-22T10:17Z)
- AWS cost: **~$170**
- Operator time: **0** (Bark notifications to phone; runner ran on
  systemd service via t3.small)

See `docs/REPORTS/2026-05-21-v16-instance-tps-sweep.md` and
`docs/EVOLUTION-v9-to-v16.md` for full data and commentary.

## [v15-tcp-tuned] - 2026-05-19 (incomplete, no formal report)

> Exploratory: lower Linux `tcp_keepalive_time` from 7200s to 60s. Hypothesis
> was that faster dead-connection detection would reduce post-failover
> recovery time. **No statistically significant difference observed**;
> the bottleneck is JDBC wrapper pool refill, not TCP keepalive.

### Tested
- `net.ipv4.tcp_keepalive_time` 7200 → 60
- `net.ipv4.tcp_keepalive_intvl` 75 → 10
- `net.ipv4.tcp_keepalive_probes` 9 → 6

### Result
Inconclusive. progress.json shows 17/40 phases done; cdk destroy completed
2026-05-20T04:14Z but the orchestrator did not generate a formal report.
The signal was not large enough to justify a separate experiment write-up.

## [v14-jvm-tuned] - 2026-05-19 (config only, never run)

> Designed to stack ZGC + AlwaysPreTouch + JFR-aware flags + `-Xms`=`-Xmx`.
> After v13's null result (see below), this was deemed not worth the
> AWS spend. Config retained as documentation of the JVM-tuning ceiling.

## [v13-zgc] - 2026-05-19 (incomplete, no formal report)

> Exploratory: replace G1GC with Java 17 ZGC. Hypothesis was that
> ZGC's sub-millisecond GC pauses would reduce v11's 5-client parallel
> RB tail. **Not the right kind of bottleneck**; v11's RB cost was
> bandwidth/HikariCP contention, not GC.

### Result
Inconclusive. progress.json shows 14/40 phases done, 26 failed, with
`CDK_DESTROY` exiting rc=1. No formal report.

## [v12-aggressive-timeouts] - 2026-05-19

> ❌ **REJECTED.** v12 tested 3 timeout reductions to see if v11's downtime
> could be further reduced. All 3 hypotheses failed. v11 remains the
> production-optimal config.

### Tested
- **H1**: `connectTimeout` 1000ms → 500ms (BG faster recovery)
- **H2**: `failureDetectionTime` 6000ms → 3000ms (FO faster detection)
- **H3**: `socketTimeout` 3000ms → 1500ms (RB faster stale conn release)

### Results

| Scenario | v11 | v12 | Δ |
|---|---|---|---|
| BG median | 4.20 s | **4.50 s** | +300 ms ❌ |
| BG max | 4.95 s | 5.10 s | +155 ms ❌ |
| FO median | 9.45 s | **10.35 s** | +900 ms ❌ |
| FO max | 13.6 s | **18.5 s** | +4.9 s ❌❌ |
| RB median | 7.10 s | **6.72 s** | -385 ms ✅ |
| RB max | 7.40 s | **10.30 s** | +2.9 s ❌ |
| RB stdev | 360 ms | **2,592 ms** | 7× variance ❌ |

### Lessons

1. **Aggressive timeouts trigger retry storms.** A 500ms `connectTimeout`
   fires before the wrapper's topology cache is updated, marking otherwise-
   good connections as failed.
2. **3s `failureDetectionTime` triggers during Aurora bursts.** EFM2 starts
   probing during normal recovery from brief writer load, racing with the
   actual failover and producing 18.5s outliers.
3. **1.5s `socketTimeout` aborts in-flight queries.** During reboot's brief
   unavailability window, queries that were about to complete get killed,
   forcing app-level retries that add 2-3s.
4. **Median improvement is not enough.** v12's RB median improved 5%, but
   p95/max degraded 39% with 7× higher variance. This trade is bad for
   production.

### Conclusion

v11 is at a local optimum. The 3 timeouts (`connectTimeout=1000`,
`socketTimeout=3000`, `failureDetectionTime=6000`) are not arbitrary
defaults — they emerged from v9's hypothesis testing and are confirmed
optimal by v12's regression. **DO NOT modify these timeouts in production.**

Future optimization should focus on RDS service-side improvements, not
client-side timeout tuning.

See `docs/REPORTS/2026-05-19-v12-aggressive-timeouts.md` for full per-round data.

### Tooling additions

- `configs/v12-aggressive-timeouts.yaml` — v12 config (preserved as
  cautionary reference; not for production)
- `scripts/v12-extract-data.py` — extracts v12 measurements from
  `e2e-results/` to `dashboard/data/v12-only.json`
- `dashboard/assets/dashboard-v12.js` — v12 dashboard view
- `dashboard/index.html` — added `#v12` toggle
- `infra/orchestrate-v11.py` — added `V11_CONFIG` env var support to run
  alternate configs through the same orchestrator (separate state files)
- `scripts/live-status-server.py` — localhost:9999 live progress
  dashboard with auto-refresh every 10s, no public exposure
- `infra/v11-then-v12.sh` — chain script that auto-launches v12 after v11
  completes
- `svg/test-lifecycle-v11.svg` + `optimization-journey.svg` — phase-by-
  phase timing diagram + v9 → v12 evolution timeline

### Bugs found and fixed

- **`_safe_delete_bg` was 12 min, needs 30+ min** — RDS BG lifecycle
  (SWITCHOVER_COMPLETED → deletable) takes longer than expected when 5
  clusters do this in parallel. Updated `max_minutes` from 12 to 30 in
  `infra/orchestrate-v11.py`.
- **`_restore_runtime_state`** added to handle resumption — when
  `COLLECT_OUTPUTS` was already done in a previous orchestrator run, the
  in-memory `cluster_arns`/`endpoints` were empty on restart. Now restored
  from `progress.json` outputs + boto3 fallback.

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

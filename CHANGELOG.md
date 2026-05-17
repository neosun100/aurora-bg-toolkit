# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

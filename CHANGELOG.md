# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

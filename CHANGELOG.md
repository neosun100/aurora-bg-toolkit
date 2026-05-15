# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v9-experiment] - 2026-05-16 (in progress)

### Added
- `configs/v9-tuned.yaml` — combined optimization config testing 5 hypotheses:
  H1 JVM DNS TTL=5s, H2 remove connectionInitSql/TestQuery, H3
  bgConnectTimeoutMs=5000, H4 wrapper 4.0.1, H5 maxLifetime=300000ms
- `docs/EXPERIMENT-V9-PLAN.md` — pre-registered experiment design with
  hypotheses, time budget, success criteria
- `infra/orchestrate-{bg,failover,reboot}-v9.sh` — orchestrators for
  10-round runs across 4 cells (v4@4.0.0, v4@4.0.1, v9@4.0.0, v9@4.0.1)
- 10 Hz STATS reporter (was 1 Hz) — better downtime measurement precision
- `analyze-stats-gap.py` upgraded to handle 100ms granularity

### Fixed
- (TBD) … will add bug fixes discovered during v9 execution

### Changed
- BgDowntimeTest now sets `-Dnetworkaddress.cache.ttl=5` at startup so
  every test gets the same DNS-aware behaviour as the explicit JVM flag

### Background
v9 explores whether v4-current (the existing production recommendation) can
be improved on under the production-grade workload (1280 ops/s, pool=50)
discovered in v2. Five untested optimization levers + wrapper version
A/B = 120 measurements across 30 rounds.

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

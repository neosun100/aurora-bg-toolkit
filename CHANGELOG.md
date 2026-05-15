# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

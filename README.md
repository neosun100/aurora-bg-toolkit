# Aurora BG Toolkit

[![CI](https://github.com/neosun100/aurora-bg-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/neosun100/aurora-bg-toolkit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Java](https://img.shields.io/badge/Java-17-orange.svg)](https://openjdk.org/projects/jdk/17/)
[![Maven](https://img.shields.io/badge/Maven-3.9+-blue.svg)](https://maven.apache.org/)

> **A reproducible, configuration-driven test harness for measuring Aurora MySQL Blue/Green switchover, Failover, and Reboot downtime — with full test pyramid, automated analysis, and a visual dashboard.**

---

## What is this?

When AWS customers experience long blackout windows during Aurora MySQL Blue/Green switchovers (sometimes 30s–60s instead of the expected 3s–5s), the root cause is almost always a combination of:

1. JDBC client configuration (missing `connectTimeout`, wrong wrapper plugin chain)
2. DNS propagation timing
3. HikariCP connection pool behavior under failover

This toolkit lets you **reproduce, measure, and optimize** these scenarios end-to-end:

- Spin up an Aurora cluster (or point to an existing one)
- Run a controlled mixed workload (read/insert/update with configurable ratios)
- Trigger Blue/Green switchover, Failover, or Reboot
- Automatically parse logs and compute downtime windows
- Generate a Markdown report and an interactive dashboard

Originally built to diagnose a 4-57s downtime issue at a customer (HashKey), now generalized as a reusable toolkit.

---

## Quick Start

```bash
# 1. Clone & build
git clone https://github.com/neosun100/aurora-bg-toolkit.git
cd aurora-bg-toolkit
mvn clean package

# 2. Set credentials
export DB_PASSWORD='your-db-password'

# 3. Run a test against an existing Aurora cluster
./scripts/run-test.sh \
  --endpoint test-01.cluster-xxx.us-east-1.rds.amazonaws.com \
  --config configs/v4-current.yaml

# 4. After triggering Blue/Green switchover, stop and analyze
./scripts/stop-test.sh
python3 scripts/analyze-logs.py logs/test-01_v4_*/
python3 scripts/generate-report.py --output docs/REPORTS/$(date +%Y-%m-%d)-results.md

# 5. Open the dashboard
open dashboard/index.html
```

## Features

- **Configuration-driven** — All test parameters in YAML; no code changes between runs
- **Multi-platform deployment** — EC2 (local Java) + EKS (Kubernetes) parallel execution
- **Multi-version testing** — Run AWS JDBC Wrapper 3.3.0 and 4.0.0 side-by-side
- **Mixed realistic workload** — Configurable read/insert/update ratios with per-thread timing
- **Automated analysis** — Python scripts compute downtime windows from logs
- **Visual dashboard** — Single-file HTML, Apple-style dark theme, share via email
- **Full test pyramid** — Unit (JUnit 5) + Integration (Testcontainers) + Regression (replay logs) + E2E (real Aurora)

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — Component design and data flow
- [Methodology](docs/METHODOLOGY.md) — How to design and run a downtime test
- [Root Cause Analysis](docs/ROOT-CAUSE-ANALYSIS.md) — Why customer configurations hang for 30+ seconds
- [HSK Case Study](docs/HSK-CASE-STUDY.md) — End-to-end story from customer's original config to verified optimization
- [Reports](docs/REPORTS/) — Historical test results

## Configurations

| Config | Status | Use Case |
|---|---|---|
| `customer-baseline.yaml` | Reference | Reproduce customer's original problem |
| `v1-optimized.yaml` | Validated | First optimization pass |
| `v2-tighter-timeout.yaml` | Validated | `connectTimeout` 3000→2000 |
| `v3-aggressive-timeout.yaml` | Validated | `connectTimeout` 2000→1000 |
| `v4-current.yaml` | **Recommended** | Production-ready (2.7-7.6s downtime) |
| `v5-experimental.yaml` | Experimental | Stability-focused tuning |

## Test Pyramid

```
                  ╱╲   E2E (real Aurora cluster)
                 ╱  ╲     - 6 cluster scenarios × 4 wrapper combos
                ╱────╲
               ╱      ╲   Regression (replay historical logs)
              ╱────────╲    - Reproduce every number in past reports
             ╱          ╲
            ╱            ╲ Integration (Testcontainers MySQL)
           ╱──────────────╲   - JDBC, Hikari, retry, mixed workload
          ╱                ╲
         ╱                  ╲ Unit (pure Java)
        ╱────────────────────╲  - LogParser, ConfigLoader, Stats
```

## License

[MIT](LICENSE) © 2026 Neo Sun

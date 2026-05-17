<div align="center">

# Aurora BG Toolkit

**A reproducible, fully-automated test harness for measuring Aurora MySQL Blue/Green switchover, Failover, and Reboot downtime — at production load, with high-precision STATS, resumable orchestration, and a self-contained dashboard.**

[![CI](https://github.com/neosun100/aurora-bg-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/neosun100/aurora-bg-toolkit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Java 17](https://img.shields.io/badge/Java-17-orange.svg?logo=openjdk&logoColor=white)](https://openjdk.org/projects/jdk/17/)
[![Maven](https://img.shields.io/badge/Maven-3.9+-blue.svg?logo=apachemaven&logoColor=white)](https://maven.apache.org/)
[![Aurora MySQL](https://img.shields.io/badge/Aurora%20MySQL-3.10.4-2997ff.svg?logo=amazonaws&logoColor=white)](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraMySQLReleaseNotes/)
[![JDBC Wrapper](https://img.shields.io/badge/aws--advanced--jdbc--wrapper-4.0.1-30d158.svg)](https://github.com/awslabs/aws-advanced-jdbc-wrapper)
[![HikariCP](https://img.shields.io/badge/HikariCP-4.0.3-bf5af2.svg)](https://github.com/brettwooldridge/HikariCP)
[![Tests](https://img.shields.io/badge/tests-42%2B%20unit%20%2B%2030%20E2E-brightgreen.svg)](#test-pyramid)

[Quick Start](#-quick-start) ·
[Latest Results](#-latest-results-v10-production) ·
[Architecture](#-architecture) ·
[Lifecycle](#-experiment-lifecycle) ·
[Reports](docs/REPORTS) ·
[Dashboard](dashboard/index.html)

</div>

---

## 📋 Table of Contents

- [What is this?](#-what-is-this)
- [Latest Results: v10-production](#-latest-results-v10-production)
- [Architecture](#-architecture)
- [Quick Start](#-quick-start)
- [Configurations](#-configurations)
- [Experiment Lifecycle](#-experiment-lifecycle)
- [Test Pyramid](#-test-pyramid)
- [Documentation](#-documentation)
- [Project Layout](#-project-layout)
- [Contributing](#-contributing)
- [License](#-license)

---

## 🎯 What is this?

When AWS customers experience **long blackout windows during Aurora MySQL Blue/Green switchovers** (sometimes 30s–60s instead of the expected 3s–5s), the root cause is almost always a combination of:

1. **JDBC client configuration** — missing `connectTimeout`, wrong wrapper plugin chain
2. **DNS propagation timing** — JVM's default 30s DNS cache holds stale writer IP after switchover
3. **HikariCP connection pool behaviour under failover** — pool drains faster than RDS can refill

This toolkit lets you **reproduce, measure, and optimize** these scenarios end-to-end:

- 🚀 **One command** spins up Aurora cluster + EC2 client, runs 30 measurements across 3 scenarios, generates dashboard + report, then tears everything down
- 📊 **High-precision measurement** (10 Hz STATS = ±100ms accuracy) under realistic production load (1280 ops/s, pool=50)
- 🔄 **Fully resumable** — any interruption (laptop sleep, network drop, AWS API throttle) re-runs from the last checkpoint
- 📉 **Self-contained dashboard** — single HTML file, Apple-style dark theme, SVG box plots, share via email
- ✅ **Full test pyramid** — 42+ unit + Testcontainers integration + log-replay regression + real-Aurora E2E

Originally built to diagnose a 4–57s downtime issue at a customer (HashKey), now generalised as a reusable toolkit with rigorous statistical methodology.

---

## 🏆 Latest Results: v10-production

> **The first measurement of the production-recommended configuration at production load.**  
> Running `v4-current` tuning at `1280 ops/s, pool=50` with mandatory `JVM -Dnetworkaddress.cache.ttl=5`.  
> 30 measurements (10 BG + 10 Failover + 10 Reboot) collected on 2026-05-17 in ~7h, ~$5 AWS cost.

<p align="center">
  <img src="https://img.aws.xin/uPic/v10-vs-v9-results.png" alt="v10 vs v9 production-load downtime comparison" width="100%"/>
</p>

| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |
|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|
| **Blue/Green**    | 10  | 4.50 s   | **5.05 s** | 8.76 s    | 19.7 s    | 21.0 s    | 6.17 s    |
| **Failover**      | 10  | 0 ms     | **7.75 s** | 7.94 s    | 13.1 s    | 14.8 s    | 3.69 s    |
| **Reboot**        | 10  | 0 ms     | **100 ms** | 1.02 s    | 2.56 s    | 2.6 s     | 1.19 s    |

### 🔑 Key findings

1. ✅ **JVM DNS TTL=5 is the single biggest win.** Reboot drops from ~5s to median 100ms — a 50× improvement from one JVM property:
   ```
   -Dnetworkaddress.cache.ttl=5
   -Dnetworkaddress.cache.negative.ttl=2
   ```

2. 🚨 **Production load reveals BG long-tail behaviour that low-load testing missed.** v9's "v4 control" cells were inadvertently measured at 40 ops/s; v10 at 1280 ops/s shows:
   - BG median is **5.05 s** (vs v9's reported 3.8–4.0 s) — +1 s slower
   - **30% of BG rounds exceed 14 s** (3 outliers: 14.8s · 18.0s · 21.0s) — a long tail v9 didn't see
   - **Production timeout configuration must be ≥ 25 s**, not the 5–10 s the literature suggests

3. ✅ **Failover is reproducible.** v10 median 7.75 s, max 14.8 s, stdev 3.7 s — almost identical to v9-tuned and Aurora documentation.

### 📂 Full data

- 📄 **Final report**: [`docs/REPORTS/2026-05-17-v10-production.md`](docs/REPORTS/2026-05-17-v10-production.md)
- 📊 **Dashboard data**: [`dashboard/data/v10-only.json`](dashboard/data/v10-only.json) — open `dashboard/index.html` to visualise
- 🔬 **Pre-registered experiment design**: [`docs/EXPERIMENT-V10-PLAN.md`](docs/EXPERIMENT-V10-PLAN.md)
- 📝 **Configuration**: [`configs/v10-final.yaml`](configs/v10-final.yaml)
- 📜 **Audit trail**: [`CHANGELOG.md`](CHANGELOG.md)

---

## 🏗 Architecture

<p align="center">
  <img src="https://img.aws.xin/uPic/architecture.png" alt="System architecture: VPC + Aurora cluster + EC2 client + BG deployment + IAM + parameter group" width="100%"/>
</p>

The toolkit deploys a self-contained AWS environment:

| Component | Purpose |
|-----------|---------|
| **Aurora MySQL 3.10.4** cluster | `db.r7g.large` writer + `db.t3.medium` reader (`aurora-iopt1` storage, port 4488) |
| **EC2 c6i.2xlarge** runner | Java 17 + `aws-advanced-jdbc-wrapper` 4.0.1 + HikariCP pool=50 + your config |
| **Blue/Green Deployment** | Auto-created per BG round; `--delete-target` cleanup between rounds |
| **Cluster Parameter Group** | `binlog_format=ROW` + `aurora_enhanced_binlog=1` (BG prerequisites) |
| **IAM Role / Instance Profile** | EC2 → RDS API permissions (switchover, failover, reboot) |
| **Test triggers** | `switchover-blue-green-deployment` · `failover-db-cluster` · `reboot-db-instance` |

**Measurement principle**: each round, the Java client emits a `STATS write_ok=N read_ok=N` log line every 100 ms. After the test trigger fires, we measure the longest contiguous window of `write_ok=0` to compute downtime — accurate to ±100 ms.

---

## 🚀 Quick Start

### One-command end-to-end run

```bash
git clone https://github.com/neosun100/aurora-bg-toolkit.git
cd aurora-bg-toolkit
nohup bash infra/orchestrate-v10-master.sh > /tmp/v10-launch.log 2>&1 &
```

That's it. The orchestrator will:

1. ✓ Verify dependencies (`aws`, `mvn`, `java`, `python3`, `jq`)
2. ✓ Build the fat-jar (`mvn package -Pwrapper-4.1`)
3. ✓ Bootstrap AWS infra (VPC, SG, subnet group, key pair)
4. ✓ Create Aurora cluster (writer + reader) and wait until available
5. ✓ Apply BG prerequisites (binlog params + reboot writer)
6. ✓ Launch EC2 c6i.2xlarge runner and upload the fat-jar
7. ✓ Run **10 BG + 10 Failover + 10 Reboot rounds** (30 measurements total)
8. ✓ Aggregate stats into `dashboard/data/v10-only.json`
9. ✓ Write the final report to `docs/REPORTS/2026-05-17-v10-production.md`
10. ✓ Tear down all AWS resources (zero residual cost)

Total wall time: ~7-8h. Total AWS cost: ~$5-8.

### Watch progress in real time

```bash
# Pretty-printed status (phase progress, per-round measurements, ETA)
bash scripts/v10-status.sh

# Auto-refresh every 30s
bash scripts/v10-status.sh --watch

# Tail the master log
tail -f infra/state/v10-master.log
```

### Resume after interruption

```bash
# Just re-run the master orchestrator. It reads progress.json,
# skips done phases, retries failed/pending ones.
bash infra/orchestrate-v10-master.sh
```

### Open the dashboard

```bash
python3 -m http.server 8765 --directory . &
open http://localhost:8765/dashboard/index.html
```

### Manual run against an existing cluster

```bash
mvn clean package -Pwrapper-4.1
export DB_PASSWORD='your-db-password'
DB_ENDPOINT=test-01.cluster-xxx.us-east-1.rds.amazonaws.com \
  java -Dnetworkaddress.cache.ttl=5 -Dnetworkaddress.cache.negative.ttl=2 \
       -jar target/aurora-bg-toolkit-1.0.0-SNAPSHOT-all.jar \
       configs/v10-final.yaml
# In another terminal: trigger switchover/failover/reboot via AWS CLI
# Then: python3 scripts/analyze-stats-gap.py /path/to/wrapper.log
```

---

## ⚙️ Configurations

The toolkit ships 10 configurations representing the full optimization journey from the original customer baseline to the current production reference.

| Config | Status | When to use | BG result (production load) |
|--------|--------|-------------|-----------------------------|
| `customer-baseline.yaml` | reference | Reproduce the original 30–60s problem | 4–57s, unstable |
| `v1-optimized.yaml` | validated | First fix — remove harmful plugins, add timeouts | 3.1–7.6s |
| `v2-tighter-timeout.yaml` | validated | Add warm pool + app retry | 3.3–6.0s |
| `v3-aggressive-timeout.yaml` | validated | `connectTimeout` 2s → 1s | similar to v2 |
| `v4-current.yaml` | validated | Explicit failureDetection tuning | low-load only |
| `v5-experimental.yaml` | rejected | Pool=20, no test queries | regression |
| `v6-aggressive.yaml` | rejected | `connectTimeout=500` | breaks |
| `v7-dns-warmup.yaml` | rejected | App-level DNS warmup thread | overengineered |
| `v8-prod-load.yaml` | validated | Pool=50 at production load | 6–11 s |
| `v9-tuned.yaml` | partial | 5 untested levers; only H1 (DNS TTL=5) survived | 3.5–4.2s @ 1280 ops/s |
| **`v10-final.yaml`** | **🏆 RECOMMENDED** | **Production reference, validated under 1280 ops/s** | **median 5.05s, max 21s** |

See [`configs/README.md`](configs/README.md) for YAML schema and how to add a new config.

---

## 🔄 Experiment Lifecycle

<p align="center">
  <img src="https://img.aws.xin/uPic/lifecycle.png" alt="v10 experiment lifecycle: 39 phases, resumable, ~7-8h end-to-end" width="100%"/>
</p>

The master orchestrator (`infra/orchestrate-v10-master.sh`) executes **39 phases** with full checkpoint persistence:

| Group | Phases | Duration | Resumable |
|-------|--------|----------|-----------|
| **Setup** | PRECHECK · BUILD · BOOTSTRAP · CLUSTER_CREATE · BG_PREREQS · EC2_SETUP | ~12 min | ✓ |
| **Measurements** | TEST_BG_R{1..10} · TEST_FO_R{1..10} · TEST_RB_R{1..10} | ~6 h | ✓ |
| **Wrap-up** | ANALYZE · REPORT · TEARDOWN | ~12 min | ✓ |

**Resumability semantics** (in `infra/state/v10-progress.json`):

```json
{
  "experiment": "v10-production",
  "current_phase": "TEST_BG_R5",
  "phases": {
    "TEST_BG_R5": {
      "status": "running",
      "started_at": "2026-05-17T01:32:00Z",
      "attempts": 1
    }
  }
}
```

| Status on launch | Action |
|------------------|--------|
| `done` | skip |
| `pending` | run |
| `running` | assume previous run was killed; reset to pending and run |
| `failed` | retry up to 3 times across re-launches |

---

## 🧪 Test Pyramid

<p align="center">
  <img src="https://img.aws.xin/uPic/test-pyramid.png" alt="Test pyramid: unit + integration + regression + E2E" width="100%"/>
</p>

| Layer | Count | Cost | Speed | Purpose |
|-------|-------|------|-------|---------|
| **Unit** (JUnit 5) | 42+ | $0 | ~0.1s | YAML parsing, URL building, log analysis, stats |
| **Integration** (Testcontainers MySQL) | runs in CI | $0 | ~10s | JDBC + HikariCP + retry path |
| **Regression** (replay historical logs) | ~5 | $0 | ~5s | Catch LogParser drift via reference log replay |
| **E2E** (real Aurora) | 30 measurements | ~$5-8 | ~7-8h | Production-grade reality check (= v10) |

```bash
# Quick local feedback
mvn test                                  # unit + integration + regression
bash infra/orchestrate-v10-master.sh     # full E2E (creates AWS resources)
```

---

## 📚 Documentation

| Document | Topic |
|----------|-------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component design and data flow |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | How to design and run a downtime test |
| [docs/ROOT-CAUSE-ANALYSIS.md](docs/ROOT-CAUSE-ANALYSIS.md) | Why customer configurations hang for 30+ seconds |
| [docs/HSK-CASE-STUDY.md](docs/HSK-CASE-STUDY.md) | End-to-end story from customer's original config to v10 |
| [docs/EXPERIMENT-V10-PLAN.md](docs/EXPERIMENT-V10-PLAN.md) | v10 pre-registered design |
| [docs/REPORTS/](docs/REPORTS/) | Historical test results (v1, v2, v9, v10) |
| [infra/cdk/README.md](infra/cdk/README.md) | CDK migration roadmap (v11 target) |

---

## 📁 Project Layout

```
aurora-bg-toolkit/
├── configs/                # 10 YAML configurations (customer-baseline → v10-final)
├── src/                    # Java code (BgDowntimeTest, ConfigLoader, MixedWorkload, ...)
│   ├── main/java/         
│   └── test/java/          # 42+ unit tests
├── infra/                  # Bash orchestrators + CDK skeleton
│   ├── 00..30-*.sh         # Bootstrap → cluster → BG → EC2 (foundation)
│   ├── orchestrate-v10-*.sh # v10 master + per-scenario orchestrators
│   └── cdk/                # Skeleton for v11 IaC migration
├── scripts/                # Python analysis + dashboard tools
│   ├── analyze-stats-gap.py    # Compute downtime windows from STATS lines
│   ├── v10-extract-data.py     # Aggregate measurements into dashboard JSON
│   ├── v10-generate-report.py  # Auto-write final markdown report
│   └── v10-status.sh           # Real-time progress viewer
├── dashboard/              # Single-file HTML dashboard
│   ├── index.html          # v10-only view: hero + config + box plots + tables
│   ├── assets/
│   │   └── dashboard-v10.js
│   └── data/
│       └── v10-only.json   # 30 measurements, generated by v10-extract-data.py
├── docs/
│   ├── REPORTS/            # Final markdown reports (v9, v10, ...)
│   ├── ARCHITECTURE.md
│   ├── METHODOLOGY.md
│   ├── ROOT-CAUSE-ANALYSIS.md
│   ├── HSK-CASE-STUDY.md
│   └── EXPERIMENT-V10-PLAN.md
├── lib/                    # Vendored aws-advanced-jdbc-wrapper jars (3.3.0/4.0.0/4.0.1)
├── samples/reference-logs/ # Historical logs for regression tests
├── svg/                    # Diagram sources (architecture, lifecycle, results, pyramid)
├── e2e-results/            # All measurement results (v1 → v10), per round
├── pom.xml                 # Maven build (Java 17, profiles for wrapper version)
├── CHANGELOG.md            # All experiments + audit trail
└── README.md               # this file
```

---

## 🤝 Contributing

PRs welcome! Especially for:

- 🆕 **New config experiments** — drop a YAML in `configs/` and run `infra/orchestrate-v10-master.sh` (modified to point at your config) to validate
- 🔧 **CDK migration** — see [`infra/cdk/README.md`](infra/cdk/README.md) for the v11 plan
- 📈 **Additional dashboards** — the v10 dashboard is the latest; older scatter-plot view exists in `dashboard/assets/dashboard.js`
- 🐛 **Bug reports** — please include the affected `e2e-results/` directory + `infra/state/v10-progress.json`

Before pushing:

```bash
mvn test                                  # all unit + integration + regression
bash -n infra/orchestrate-v10-master.sh  # bash syntax
python3 -c "import ast; ast.parse(open('scripts/v10-extract-data.py').read())"
```

---

## 🙏 Acknowledgements

- Aurora MySQL team for engaging on the original HashKey customer ticket
- The `aws-advanced-jdbc-wrapper` team for clear plugin architecture
- HikariCP for being remarkably stable under abusive testing conditions

## 📜 License

[MIT](LICENSE) © 2026 Neo Sun

---

<div align="center">
  <sub><b>Aurora BG Toolkit</b> · Built for measuring what matters · <a href="https://github.com/neosun100/aurora-bg-toolkit">github.com/neosun100/aurora-bg-toolkit</a></sub>
</div>

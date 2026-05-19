<div align="center">

# Aurora BG Toolkit

**Production-grade reproducible test harness for Aurora MySQL Blue/Green switchover, Failover, and Reboot downtime — full IaC, 5-cluster parallel, ±100ms precision, ~$5 per run.**

[![CI](https://github.com/neosun100/aurora-bg-toolkit/actions/workflows/ci.yml/badge.svg)](https://github.com/neosun100/aurora-bg-toolkit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Java 17](https://img.shields.io/badge/Java-17-orange.svg?logo=openjdk&logoColor=white)](https://openjdk.org/projects/jdk/17/)
[![CDK 2.x](https://img.shields.io/badge/AWS%20CDK-2.x-FF9900.svg?logo=amazonaws&logoColor=white)](https://aws.amazon.com/cdk/)
[![Aurora MySQL](https://img.shields.io/badge/Aurora%20MySQL-3.10.4-2997ff.svg?logo=amazonaws&logoColor=white)](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraMySQLReleaseNotes/)
[![JDBC Wrapper](https://img.shields.io/badge/aws--advanced--jdbc--wrapper-4.0.1-30d158.svg)](https://github.com/awslabs/aws-advanced-jdbc-wrapper)
[![HikariCP](https://img.shields.io/badge/HikariCP-4.0.3-bf5af2.svg)](https://github.com/brettwooldridge/HikariCP)
[![Tests](https://img.shields.io/badge/measurements-162-brightgreen.svg)](#optimization-journey)

[Quick Start](#-quick-start) ·
[Optimal Config](#-optimal-config-v11) ·
[Lifecycle](#-test-lifecycle--single-run-2h) ·
[Optimization Journey](#-optimization-journey) ·
[Reports](docs/REPORTS) ·
[Dashboard](dashboard/index.html)

</div>

---

## 📋 Table of Contents

- [What is this?](#-what-is-this)
- [Optimal Config (v11)](#-optimal-config-v11)
- [Quick Start](#-quick-start)
- [Live Status Monitoring](#-live-status-monitoring)
- [Test Lifecycle — Single Run ~2h](#-test-lifecycle--single-run-2h)
- [Optimization Journey](#-optimization-journey)
- [Why these parameters?](#-why-these-parameters)
- [Reports & Data](#-reports--data)
- [Project Layout](#-project-layout)
- [License](#-license)

---

## 🎯 What is this?

When AWS customers experience **30–60 second blackouts during Aurora MySQL Blue/Green switchovers** (vs. the documented 3–5 seconds), the root cause is almost always JDBC client misconfiguration interacting with DNS caching and HikariCP pool behavior.

This toolkit lets you **reproduce, measure, and optimize** these scenarios end-to-end:

- 🚀 **One CDK command** — spins up infrastructure, runs 30 measurements (5 BG + 10 FO + 10 RB), generates dashboard + report, tears everything down. ~2h wall, ~$5 AWS.
- 📊 **High-precision measurement** — 10 Hz STATS reporter = ±100ms accuracy under realistic production load (1280 ops/s, pool=50)
- 🔄 **Fully resumable** — laptop sleep, network drop, AWS API throttle? Re-run picks up at last checkpoint
- 📉 **Self-contained dashboard** — single HTML, Apple-style dark theme, SVG box plots, share via email
- 🔬 **162 measurements across 4 versions** — v9 → v10 → v11 → v12 — with v11 emerging as **production-optimal**
- 🛡 **No public exposure** — live status server is `localhost`-only; no Lambda, no public endpoints

Originally built to diagnose a 4–57s downtime issue at a digital-asset exchange customer; now generalized as a reusable benchmark with rigorous statistical methodology.

---

## 🏆 Optimal Config (v11)

> **After 162 measurements across 4 versions over 5 months, the production-optimal configuration is `configs/v11-final.yaml`. v12 attempted further timeout reductions; all 3 hypotheses regressed. v11 is at a local optimum — DO NOT modify these timeouts.**

<p align="center">
  <img src="https://img.aws.xin/uPic/optimization-journey.png" alt="v9 → v12 optimization journey, 162 measurements, v11 emerged as production-optimal" width="100%"/>
</p>

### Final benchmark — v11 (recommended)

| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |
|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|
| **Blue/Green**    | 5   | 4.00 s   | **4.20 s** | 4.30 s    | 4.94 s    | 4.95 s    | 372 ms    |
| **Failover**      | 10  | 7.20 s   | **9.45 s** | 10.04 s   | 12.57 s   | 13.60 s   | 2.08 s    |
| **Reboot**        | 10  | 6.50 s   | **7.10 s** | 7.07 s    | 7.36 s    | 7.40 s    | 360 ms    |

### v11 production-locked parameters

```yaml
# configs/v11-final.yaml — production reference
jdbc:
  wrapperPlugins: [failover2, efm2, bg]
  connectTimeout: 1000          # ⚠️ leave at 1s (v12 proved 500ms regresses)
  socketTimeout: 3000           # ⚠️ leave at 3s (v12 proved 1.5s regresses tail)
  failureDetectionTime: 6000    # ⚠️ leave at 6s (v12 proved 3s regresses FO)
  failureDetectionInterval: 1000
  failureDetectionCount: 3
  bgHighMs: 50

hikari:
  maximumPoolSize: 50
  minimumIdle: 50
  maxLifetimeMs: 60000
  keepaliveTimeMs: 60000
  connectionInitSql: "select 1 from dual"
  connectionTestQuery: "SELECT 1"

# MANDATORY JVM flags (v9 H1 — DNS TTL is the killer feature):
# -Dnetworkaddress.cache.ttl=5
# -Dnetworkaddress.cache.negative.ttl=2
# Without these, Reboot will be 50× slower (~5s vs 100ms in single-client mode)
```

---

## 🚀 Quick Start

### One-command end-to-end

```bash
git clone https://github.com/neosun100/aurora-bg-toolkit.git
cd aurora-bg-toolkit

# One-time CDK setup (per AWS account/region)
cd infra/cdk
uv venv .venv
uv pip install -r requirements.txt
cdk bootstrap
cd ../..

# End-to-end run (~2h wall, ~$5 AWS)
nohup python3 infra/orchestrate-v11.py > /tmp/v11-launch.log 2>&1 &

# Watch live progress (optional, recommended)
python3 scripts/live-status-server.py &
open http://localhost:9999
```

That's it. The orchestrator will:

1. ✓ Verify dependencies (`aws`, `cdk`, `mvn`, `java`, `python3`, `jq`)
2. ✓ Build the fat-jar (`mvn package -Pwrapper-4.1`)
3. ✓ `cdk bootstrap` (idempotent)
4. ✓ `cdk deploy --all` (NetworkStack + 5 ClusterStack + ClientStack, parallel)
5. ✓ Collect outputs (cluster endpoints, EC2 IP, master secret) via boto3
6. ✓ Upload fat-jar + configs to EC2 c6i.2xlarge runner
7. ✓ Run **5 clusters in parallel**, each 1×BG + 2×FO + 2×RB rounds
8. ✓ Aggregate stats into `dashboard/data/v11-only.json`
9. ✓ Write the final report to `docs/REPORTS/2026-05-17-v11-cdk-parallel.md`
10. ✓ `cdk destroy --all` (zero residual cost)

### Resume after interruption

`progress.json` checkpoint state under `infra/state/v11-progress.json`. Re-running the same launch command resumes from the last completed phase.

### Open the dashboard

```bash
python3 -m http.server 8765 --directory . &
open http://localhost:8765/dashboard/index.html
# Toggle between v10 / v11 / v12 views via URL hash (#v11 default)
```

---

## 📺 Live Status Monitoring

While the orchestrator runs (~2h), you can watch real-time progress at `http://localhost:9999`:

```bash
python3 scripts/live-status-server.py &
open http://localhost:9999
```

**Features:**
- Auto-refresh every 10 seconds
- Progress bar with phase counts (done / running / failed)
- Per-cluster grid showing 5 × 6 measurements live
- Setup + Wrap-up phase status with durations
- Recent error display
- **Pure localhost — no Lambda, no public endpoint, no security risk**

---

## ⏱ Test Lifecycle — Single Run ~2h

<p align="center">
  <img src="https://img.aws.xin/uPic/test-lifecycle-v11.png" alt="Single test run lifecycle: 9 phases, ~2h wall clock, $5 AWS, 30 measurements" width="100%"/>
</p>

### Phase-by-phase timing

| # | Phase | Duration | What happens |
|---|---|---|---|
| ① | **Pre-flight** | ~1 min | Dependency check + maven build + cdk bootstrap |
| ② | **CDK Deploy** | **~14 min** | 7 stacks deploy in parallel (1 Network + 5 Cluster + 1 Client). Aurora cluster create dominates. |
| ③ | **Provision** | ~30 sec | Collect CFN outputs, SSH into EC2, upload fat-jar |
| ④ | **BG Round 1+2** | **~70 min** | Per cluster: create BG (22m) → switchover (5m) → `_safe_delete_bg` wait (15-30m) → R2. **The single biggest time bucket.** |
| ⑤ | **Failover R1+R2** | ~12 min | Trigger writer demote, measure write_ok=0 gap |
| ⑥ | **Reboot R1+R2** | ~8 min | Reboot writer instance, measure write_ok=0 gap |
| ⑦ | **Analyze** | ~1 min | Parse 30 stats logs, aggregate to JSON |
| ⑧ | **Report** | ~1 sec | Generate markdown + dashboard data |
| ⑨ | **CDK Destroy** | **~14 min** | Delete BGs (with safe-retry) + tear down 7 stacks |
| | **Total wall-clock** | **~2h 10min** | |

> **Note**: BG R2 lifecycle wait is intrinsic to RDS — when a BG is in `SWITCHOVER_COMPLETED`, RDS still creates the `-old1` cluster in the background, and `delete_blue_green_deployment` is rejected until that completes. We mitigate by actively cleaning `-old*` instances in `_safe_delete_bg`, but the underlying 15-30 min wait is RDS control-plane time, not client time.

### Where the time goes

```
Phase ④ BG (70m)  ████████████████████████████████████  54% ← RDS control plane (BG create + lifecycle)
Phase ② CDK (14m) ████████                              11%
Phase ⑨ Destroy   ████████                              11%
Phase ⑤ FO (12m)  ███████                                9%
Phase ⑥ RB (8m)   █████                                  6%
Phase ① + ③ + ⑦ + ⑧                                      <2%
```

**~75% of time is RDS service-side operations (BG create, BG lifecycle, cluster destroy). Only ~25% is the actual measurement work.**

---

## 📚 Optimization Journey

This toolkit's parameters didn't appear by guessing. They emerged from **162 measurements across 4 versions over 5 months**, with each version testing specific hypotheses:

### v9 — multi-lever exploration (120 measurements)
Tested 5 hypotheses in one config under low load (40 ops/s):
- ✅ **H1**: JVM `-Dnetworkaddress.cache.ttl=5` (50× improvement on Reboot — the killer feature)
- ❌ H2: removing `connectionInitSql` / `TestQuery` (no improvement)
- ❌ **H3**: `bgConnectTimeoutMs=5000` (regresses Failover by 30%)
- ❌ H4: wrapper 4.0.0 → 4.0.1 (no measurable difference)
- ❌ H5: `maxLifetime` 60s → 5min (no improvement)

### v10 — production load (30 measurements, ~7h)
Same as v9 but at production load (1280 ops/s). **Reproduced the customer's pain**:
- BG median 5.05s, **max 21s** — 30% outlier rate
- Production timeouts must be ≥25s, not the 5-10s docs suggest

### v11 — full IaC + 5-cluster parallel (25 measurements, ~2h) 🏆
Same workload as v10 but using CDK + Python orchestrator + 5 clusters in parallel:
- BG median **4.20s**, max **4.95s** — confirmed v10 outliers were **time-dependent** (not systemic)
- RB median 7.10s — slower than v10's 100ms, but this is realistic 5-client parallel cost
- **Wall time 2h** vs v10's 7h → 3.5× speedup from CDK + parallel orchestration
- **Production-recommended config**

### v12 — aggressive timeouts (24 measurements, ~2h) ❌ REJECTED
Tested 3 timeout reductions vs v11:
- ❌ H1: `connectTimeout` 1000→500ms — BG +300ms median, +155ms max
- ❌ H2: `failureDetectionTime` 6000→3000ms — FO **+900ms median, +4900ms max**
- ❌ H3: `socketTimeout` 3000→1500ms — RB max +2900ms (high variance)

**Lesson**: aggressive timeouts trigger retry storms that race with Aurora's own recovery, producing longer + higher-variance downtime. The intuition "shorter timeout = faster recovery" is wrong when the timeout's purpose is to upper-bound *legitimate RDS control plane operations*, not stuck waits.

**v11 is at a local optimum.** Future work should focus on RDS service-side improvements, not client-side timeout tuning.

---

## 🔬 Why these parameters?

### `connectTimeout = 1000ms`
TCP-level connect timeout. Lower (500ms) fires before the wrapper's topology cache catches up after a switchover, causing redundant reconnect storms (v12 H1 — proven harmful).

### `socketTimeout = 3000ms`
Read/write timeout on established connections. Lower (1500ms) occasionally aborts in-flight queries during the brief reboot unavailability window, causing extra application retries (v12 H3 — proven harmful).

### `failureDetectionTime = 6000ms` + `failureDetectionInterval = 1000ms` + `failureDetectionCount = 3`
EFM2 plugin parameters. Total detection window = 6000 + 3×1000 = 9 seconds. Lower (3000ms) triggers detection during normal Aurora bursts, racing with actual failover and producing 18.5s outliers (v12 H2 — proven harmful).

### `bgHighMs = 50`
Blue/Green plugin: how long to consider a connection "stale" after switchover detection. 50ms is fast enough to switch traffic but slow enough not to thrash.

### `maximumPoolSize = 50` + `minimumIdle = 50`
Production-grade pool sizing. At 1280 ops/s with 64 worker threads, pool=50 keeps connections warm without exhausting the database. Smaller pools (10-20) cause queueing under load.

### `maxLifetimeMs = 60000` + `keepaliveTimeMs = 60000`
1-minute connection rotation. Longer (5min) means stale connections accumulate after BG switchover; shorter (30s) means too much rotation churn. 60s is the sweet spot.

### `connectionInitSql = "select 1 from dual"` + `connectionTestQuery = "SELECT 1"`
Both kept (v9 H2 proved removing them gives no benefit). Belt-and-braces against half-open connections.

### JVM `-Dnetworkaddress.cache.ttl=5` (mandatory)
**The single most important parameter.** Without this, JVM caches DNS for 30 seconds; after RDS DNS swap (failover/reboot), the JVM keeps trying the old IP. This single 1-line flag turns RB from ~5s into ~100ms in single-client mode. Validated by v9 H1.

---

## 📂 Reports & Data

| Asset | Location |
|---|---|
| 📄 **v11 final report (recommended config)** | [`docs/REPORTS/2026-05-17-v11-cdk-parallel.md`](docs/REPORTS/2026-05-17-v11-cdk-parallel.md) |
| 📄 **v12 rejection report (with regression analysis)** | [`docs/REPORTS/2026-05-19-v12-aggressive-timeouts.md`](docs/REPORTS/2026-05-19-v12-aggressive-timeouts.md) |
| 📄 **v10 production-load reference** | [`docs/REPORTS/2026-05-17-v10-production.md`](docs/REPORTS/2026-05-17-v10-production.md) |
| 📊 **v11 dashboard data** | [`dashboard/data/v11-only.json`](dashboard/data/v11-only.json) |
| 📊 **v12 dashboard data** | [`dashboard/data/v12-only.json`](dashboard/data/v12-only.json) |
| 🔬 **v11 pre-registered design** | [`docs/EXPERIMENT-V11-PLAN.md`](docs/EXPERIMENT-V11-PLAN.md) |
| 🐛 **Customer root cause analysis** | [`docs/ROOT-CAUSE-ANALYSIS.md`](docs/ROOT-CAUSE-ANALYSIS.md) |
| 📜 **Audit trail (every change documented)** | [`CHANGELOG.md`](CHANGELOG.md) |
| 📝 **Lessons learned blog (v9 → v11)** | [`docs/BLOG-v9-v10-v11-lessons.md`](docs/BLOG-v9-v10-v11-lessons.md) |

---

## 📁 Project Layout

```
aurora-bg-toolkit/
├── configs/                # Configuration evolution
│   ├── customer-baseline.yaml
│   ├── v9-tuned.yaml       # 5 hypotheses tested
│   ├── v10-final.yaml      # production load (validated)
│   ├── v11-final.yaml      # 🏆 PRODUCTION RECOMMENDED
│   └── v12-aggressive-timeouts.yaml  # ❌ REJECTED
├── src/                    # Java client
│   ├── main/java/          # BgDowntimeTest, JdbcUrlBuilder, MixedWorkload
│   └── test/java/          # 42+ unit tests
├── infra/
│   ├── cdk/                # Python CDK app (NetworkStack + 5 ClusterStack + ClientStack)
│   ├── orchestrate-v11.py  # 685-line Python orchestrator (ThreadPoolExecutor 5)
│   └── orchestrate-v10-master.sh  # Bash orchestrator (preserved as reference)
├── scripts/
│   ├── live-status-server.py  # localhost:9999 progress dashboard
│   ├── v11-status.sh          # Terminal status grid
│   ├── v11-extract-data.py    # Aggregate measurements → JSON
│   ├── v11-generate-report.py # Auto-write markdown report
│   ├── v12-extract-data.py    # Same for v12
│   └── analyze-stats-gap.py   # Compute downtime windows from STATS lines
├── dashboard/
│   ├── index.html             # Toggle v10 / v11 / v12 via #hash
│   ├── assets/dashboard-v{10,11,12}.js
│   └── data/v{10,11,12}-only.json
├── docs/
│   ├── REPORTS/               # All experiment reports (v9 → v12)
│   ├── EXPERIMENT-V11-PLAN.md # Pre-registered design
│   ├── BLOG-v9-v10-v11-lessons.md
│   ├── METHODOLOGY.md
│   └── ROOT-CAUSE-ANALYSIS.md
├── lib/                       # Vendored aws-advanced-jdbc-wrapper jars
├── e2e-results/               # Per-round measurement artifacts (raw logs)
├── svg/                       # Architecture diagrams (SVG sources + PNG)
├── CHANGELOG.md               # Full audit trail
├── pom.xml                    # Maven build (Java 17, wrapper version profiles)
└── README.md                  # this file
```

---

## 🤝 For AWS Customers Hitting Long BG Downtime

1. **Apply the v11 config** in your application:
   - Use the JDBC wrapper params from [`configs/v11-final.yaml`](configs/v11-final.yaml)
   - **Set `-Dnetworkaddress.cache.ttl=5`** at JVM startup (mandatory, biggest win)
2. **Set application timeouts ≥ 25 seconds** (BG max in production load is ~21s)
3. **Implement application-level retry** with exponential backoff for write paths
4. **Reproduce in your environment** by running this toolkit against a non-prod cluster

If you still see >25s downtime after applying v11, file an issue with `e2e-results/` directory + `infra/state/v11-master.log` and we'll investigate.

---

## 🙏 Acknowledgements

- AWS RDS team for engaging on the original customer ticket
- The `aws-advanced-jdbc-wrapper` team for clear plugin architecture
- HikariCP for being remarkably stable under abusive testing conditions

## 📜 License

[MIT](LICENSE) © 2026 Neo Sun

---

<div align="center">
  <sub><b>Aurora BG Toolkit</b> · 162 measurements · v11 production-optimal · ~2h per run · ~$5 per run · <a href="https://github.com/neosun100/aurora-bg-toolkit">github.com/neosun100/aurora-bg-toolkit</a></sub>
</div>

# v11-CDK-Parallel Final Report — 2026-05-17

> **Experiment**: v11-cdk-parallel  
> **Generated**: 2026-05-18T07:20:15Z  
> **Infrastructure**: AWS CDK (Python) — full IaC  
> **Parallelism**: 5 clusters in parallel  
> **N measurements**: BG=5, Failover=10, Reboot=10  

---

## Executive summary

v11 is v10's production reference configuration **re-run on a fully
CDK-managed infrastructure** with **5 clusters in parallel**. The
workload, JDBC config, JVM flags, and analyzer are unchanged from v10
— only the orchestration path differs.

### Aggregated stats

| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |
|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|
| Blue/Green    |   5 | 3.70 s   | 4.41 s    | 4.42 s    | 5.06 s    | 5.10 s    | 527 ms    |
| Failover      |  10 | 5.90 s   | 8.20 s    | 10.81 s   | 21.78 s   | 22.50 s   | 5.63 s    |
| Reboot        |  10 | 6.30 s   | 6.65 s    | 6.67 s    | 6.96 s    | 7.00 s    | 205 ms    |

## Per-cluster breakdown (5-cluster parallel)

Detection of cluster contention: do all 5 clusters land in the same
statistical envelope, or is one slower?

### Blue/Green per cluster

| Cluster      | N | min | median | max | stdev |
|--------------|---|-----|--------|-----|-------|
| test-v11-1   | 1 | 3.70 s | 3.70 s | 3.70 s | 0 ms |
| test-v11-2   | 1 | 4.00 s | 4.00 s | 4.00 s | 0 ms |
| test-v11-3   | 1 | 4.90 s | 4.90 s | 4.90 s | 0 ms |
| test-v11-4   | 1 | 4.41 s | 4.41 s | 4.41 s | 0 ms |
| test-v11-5   | 1 | 5.10 s | 5.10 s | 5.10 s | 0 ms |

### Failover per cluster

| Cluster      | N | min | median | max | stdev |
|--------------|---|-----|--------|-----|-------|
| test-v11-1   | 2 | 7.40 s | 8.25 s | 9.10 s | 850 ms |
| test-v11-2   | 2 | 7.30 s | 7.95 s | 8.60 s | 650 ms |
| test-v11-3   | 2 | 5.90 s | 8.65 s | 11.40 s | 2.75 s |
| test-v11-4   | 2 | 7.80 s | 14.35 s | 20.90 s | 6.55 s |
| test-v11-5   | 2 | 7.20 s | 14.85 s | 22.50 s | 7.65 s |

### Reboot per cluster

| Cluster      | N | min | median | max | stdev |
|--------------|---|-----|--------|-----|-------|
| test-v11-1   | 2 | 6.50 s | 6.75 s | 7.00 s | 249 ms |
| test-v11-2   | 2 | 6.50 s | 6.70 s | 6.90 s | 200 ms |
| test-v11-3   | 2 | 6.30 s | 6.60 s | 6.90 s | 300 ms |
| test-v11-4   | 2 | 6.60 s | 6.60 s | 6.60 s | 0 ms |
| test-v11-5   | 2 | 6.70 s | 6.70 s | 6.70 s | 0 ms |

## v11 vs v10 comparison (sanity check)

v10 reference numbers (production-load, single cluster, bash):
- BG: median 5.05 s, max 21 s, stdev 6.17 s
- Failover: median 7.75 s, max 14.8 s, stdev 3.69 s
- Reboot: median 100 ms, max 2.6 s, stdev 1.19 s

v11 numbers (production-load, 5-cluster parallel, CDK):
- BG: median 4.41 s, max 5.10 s, stdev 527 ms
- Failover: median 8.20 s, max 22.50 s, stdev 5.63 s
- Reboot: median 6.65 s, max 7.00 s, stdev 205 ms

## Test environment

- Aurora MySQL 8.0.mysql_aurora.3.10.4 × 5 (test-v11-1..5)
- Each cluster: db.r7g.large writer + db.t3.medium reader, aurora-iopt1, port 4488
- Region: us-east-1, default VPC, public subnets
- Single c6i.2xlarge EC2 runner (8 vCPU / 16 GiB) running 5 java processes in parallel
- Workload (per cluster): 64 threads × 50ms × R:I:U=9:2:1 ≈ 1280 ops/s
- Aggregate workload: ~6400 ops/s across 5 clusters
- Connection pool: HikariCP `maximumPoolSize=50, minimumIdle=50`
- JVM: `-Dnetworkaddress.cache.ttl=5 -Xmx2g`
- STATS reporter: 10 Hz (±100ms precision)
- Wrapper: aws-advanced-jdbc-wrapper 4.0.1
- Plugins: `[failover2, efm2, bg]`

## Blue/Green — per-round measurements

| Cluster | Round | writeMaxMs | readMaxMs | runId |
|---------|-------|-----------:|----------:|-------|
| test-v11-1 | 1 | 3.70 s | 3.70 s | `test-v11-1_v11-final_v11bg_r1` |
| test-v11-2 | 1 | 4.00 s | 4.00 s | `test-v11-2_v11-final_v11bg_r1` |
| test-v11-3 | 1 | 4.90 s | 4.90 s | `test-v11-3_v11-final_v11bg_r1` |
| test-v11-4 | 1 | 4.41 s | 4.41 s | `test-v11-4_v11-final_v11bg_r1` |
| test-v11-5 | 1 | 5.10 s | 5.10 s | `test-v11-5_v11-final_v11bg_r1` |

## Failover — per-round measurements

| Cluster | Round | writeMaxMs | readMaxMs | runId |
|---------|-------|-----------:|----------:|-------|
| test-v11-1 | 1 | 7.40 s | 7.40 s | `test-v11-1_v11-final_v11fo_r1` |
| test-v11-1 | 2 | 9.10 s | 9.10 s | `test-v11-1_v11-final_v11fo_r2` |
| test-v11-2 | 1 | 7.30 s | 7.30 s | `test-v11-2_v11-final_v11fo_r1` |
| test-v11-2 | 2 | 8.60 s | 8.60 s | `test-v11-2_v11-final_v11fo_r2` |
| test-v11-3 | 1 | 5.90 s | 5.90 s | `test-v11-3_v11-final_v11fo_r1` |
| test-v11-3 | 2 | 11.40 s | 11.30 s | `test-v11-3_v11-final_v11fo_r2` |
| test-v11-4 | 1 | 7.80 s | 7.70 s | `test-v11-4_v11-final_v11fo_r1` |
| test-v11-4 | 2 | 20.90 s | 20.80 s | `test-v11-4_v11-final_v11fo_r2` |
| test-v11-5 | 1 | 7.20 s | 7.20 s | `test-v11-5_v11-final_v11fo_r1` |
| test-v11-5 | 2 | 22.50 s | 22.50 s | `test-v11-5_v11-final_v11fo_r2` |

## Reboot — per-round measurements

| Cluster | Round | writeMaxMs | readMaxMs | runId |
|---------|-------|-----------:|----------:|-------|
| test-v11-1 | 1 | 7.00 s | 7.00 s | `test-v11-1_v11-final_v11rb_r1` |
| test-v11-1 | 2 | 6.50 s | 6.50 s | `test-v11-1_v11-final_v11rb_r2` |
| test-v11-2 | 1 | 6.50 s | 6.50 s | `test-v11-2_v11-final_v11rb_r1` |
| test-v11-2 | 2 | 6.90 s | 6.80 s | `test-v11-2_v11-final_v11rb_r2` |
| test-v11-3 | 1 | 6.30 s | 6.30 s | `test-v11-3_v11-final_v11rb_r1` |
| test-v11-3 | 2 | 6.90 s | 6.90 s | `test-v11-3_v11-final_v11rb_r2` |
| test-v11-4 | 1 | 6.60 s | 6.60 s | `test-v11-4_v11-final_v11rb_r1` |
| test-v11-4 | 2 | 6.60 s | 6.60 s | `test-v11-4_v11-final_v11rb_r2` |
| test-v11-5 | 1 | 6.70 s | 6.70 s | `test-v11-5_v11-final_v11rb_r1` |
| test-v11-5 | 2 | 6.70 s | 6.70 s | `test-v11-5_v11-final_v11rb_r2` |

## How to reproduce (full IaC)

```bash
git clone https://github.com/neosun100/aurora-bg-toolkit.git
cd aurora-bg-toolkit
# one-time CDK bootstrap (per AWS account/region):
cd infra/cdk && uv venv .venv && uv pip install -r requirements.txt && cdk bootstrap && cd ../..
# end-to-end run (~3.5h wall, ~$8 AWS):
nohup python3 infra/orchestrate-v11.py > /tmp/v11-launch.log 2>&1 &
# watch progress:
bash scripts/v11-status.sh --watch
```

## Production configuration (canonical)

```yaml
# V11-FINAL — Production-grade reference, fully IaC (CDK) + 5-cluster parallel.
#
# This is identical to v10-final at the workload/JDBC/JVM level — what changed
# is the surrounding infrastructure path:
#
#   v10-final.yaml: bash orchestrator + manual aws-cli teardown
#   v11-final.yaml: CDK (Python) orchestrator + parallel 5-cluster execution
#
# The numbers should match v10 within statistical noise. If they don't, that
# itself is a finding worth reporting.
#
# Required JVM flags when running this config (same as v10):
#   -Dnetworkaddress.cache.ttl=5
#   -Dnetworkaddress.cache.negative.ttl=2
#   --add-opens java.base/java.lang=ALL-UNNAMED
#   --add-opens java.base/java.lang.reflect=ALL-UNNAMED
#   -Xmx2g  (lower than v10 -Xmx4g because EC2 c6i.2xlarge runs 5 java processes)
#
name: v11-final
description: Production-grade CDK-native reference. v10 tuning + 5-cluster parallel via CDK + Python orchestrator.

database:
  port: 4488
  database: demo
  tableTemplate: "table_${CONFIG}_${SUFFIX}"
  user: admin

jdbc:
  # Same plugin chain as v4 / v10 (validated by v9 H6-out-of-scope assessment)
  wrapperPlugins:
    - failover2
    - efm2
    - bg
  bgHighMs: 50
  # Default bgConnectTimeoutMs (30000) and bgIncreasedMs (1000) — v9 H3 proved
  # that lowering these REGRESSES Failover. Keep defaults.
  connectTimeout: 1000
  socketTimeout: 3000
  failureDetectionTime: 6000
  failureDetectionInterval: 1000
  failureDetectionCount: 3
  wrapperLoggerLevel: INFO

hikari:
  # Production-grade pool sizing
  maximumPoolSize: 50
  minimumIdle: 50
  initializationFailTimeout: -1
  connectionTimeoutMs: 5000
  idleTimeoutMs: 30000
  maxLifetimeMs: 60000
  keepaliveTimeMs: 60000
  validationTimeoutMs: 5000
  connectionInitSql: "select 1 from dual"
  connectionTestQuery: "SELECT 1"
  exceptionOverrideClassName: software.amazon.jdbc.util.HikariCPSQLException

workload:
  # Production load: ~1280 ops/s
  threads: 64
  intervalMs: 50
  weights:
    read: 9
    insert: 2
    update: 1
  retry:
    enabled: true
    delayMs: 25
  # 10Hz STATS reporter — gives ±100ms precision (vs ±500ms for 1Hz)
  statsReporterHz: 10
```

## Final recommendation

Use **`configs/v11-final.yaml`** + `infra/orchestrate-v11.py` (CDK)
for any new measurement campaign on Aurora MySQL. v11 is the
recommended production reference path; v10 (bash + single cluster)
remains as the reference implementation.

---

*Auto-generated from `dashboard/data/v11-only.json` (2026-05-18T07:20:15Z).*

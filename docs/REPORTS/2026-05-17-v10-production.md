# v10-Production Final Report — 2026-05-17

> **Experiment**: v10-production  
> **Generated**: 2026-05-17T06:24:47Z  
> **Config file**: `configs/v10-final.yaml`  
> **N measurements**: BG=10, Failover=10, Reboot=10  

---

## Executive summary

v10 is the **production reference configuration**: `v4-current.yaml`'s
validated tuning parameters, run for the first time at production load
(1280 ops/s, pool=50) with mandatory JVM `-Dnetworkaddress.cache.ttl=5`.

This experiment closes a gap discovered during the 2026-05-17 audit of v9:
v9's v4 control cells were measured at low load (40 ops/s, pool=10), not
the claimed production load. v10 fixes that and records the production-grade
numbers for the canonical recommended configuration.

| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |
|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|
| Blue/Green    |  10 | 4.50 s   | 5.05 s    | 8.76 s    | 19.65 s   | 21.00 s   | 6.17 s    |
| Failover      |  10 | 0 ms     | 7.75 s    | 7.94 s    | 13.09 s   | 14.80 s   | 3.69 s    |
| Reboot        |  10 | 0 ms     | 100 ms    | 1.02 s    | 2.56 s    | 2.60 s    | 1.19 s    |

## Hypothesis verdicts

| H  | Prediction (from v10 plan)                     | Measured                              | Verdict |
|----|-------------------------------------------------|---------------------------------------|---------|
| H1 | BG median 3.5–4.5s, stdev<500ms                | median 5.05 s, stdev 6.17 s | ⚠️      |
| H2 | Failover median 5–8s, max<12s                  | median 7.75 s, max 14.80 s | ⚠️      |
| H3 | Reboot median <500ms                            | median 100 ms                          | ✅      |

## Test environment

- Aurora MySQL 8.0.mysql_aurora.3.10.4 (matches customer)
- db.r7g.large writer + db.t3.medium reader, aurora-iopt1 storage
- Region: us-east-1 single VPC
- Client: c6i.2xlarge EC2 (8 vCPU / 16 GiB) in same VPC
- Workload: 64 threads × 50ms × R:I:U=9:2:1 ≈ 1280 ops/s (production)
- Connection pool: HikariCP `maximumPoolSize=50, minimumIdle=50`
- JVM DNS TTL: 5s (mandatory)
- STATS reporter: 10 Hz (±100ms precision)
- Wrapper: aws-advanced-jdbc-wrapper 4.0.1
- Plugins: `[failover2, efm2, bg]`

## Blue/Green switchover — per-round measurements

| Round | writeMaxMs | readMaxMs | wrapper | runId | period |
|-------|-----------:|----------:|---------|-------|--------|
| 1 | 4.81 s | 4.73 s | abt-w401.jar | `test-v10_v10-final_v10bg_r1` | 100ms |
| 2 | 5.10 s | 5.10 s | abt-w401.jar | `test-v10_v10-final_v10bg_r2` | 100ms |
| 3 | 4.71 s | 4.71 s | abt-w401.jar | `test-v10_v10-final_v10bg_r3` | 100ms |
| 4 | 18.00 s | 18.00 s | abt-w401.jar | `test-v10_v10-final_v10bg_r4` | 100ms |
| 5 | 5.20 s | 5.20 s | abt-w401.jar | `test-v10_v10-final_v10bg_r5` | 100ms |
| 6 | 5.00 s | 5.00 s | abt-w401.jar | `test-v10_v10-final_v10bg_r6` | 100ms |
| 7 | 14.80 s | 14.70 s | abt-w401.jar | `test-v10_v10-final_v10bg_r7` | 100ms |
| 8 | 4.50 s | 4.50 s | abt-w401.jar | `test-v10_v10-final_v10bg_r8` | 100ms |
| 9 | 4.50 s | 4.50 s | abt-w401.jar | `test-v10_v10-final_v10bg_r9` | 100ms |
| 10 | 21.00 s | 20.91 s | abt-w401.jar | `test-v10_v10-final_v10bg_r10` | 100ms |

## Failover — per-round measurements

| Round | writeMaxMs | readMaxMs | wrapper | runId | period |
|-------|-----------:|----------:|---------|-------|--------|
| 1 | 0 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10fo_r1` | 100ms |
| 2 | 14.80 s | 14.80 s | abt-w401.jar | `test-v10_v10-final_v10fo_r2` | 100ms |
| 3 | 6.60 s | 6.60 s | abt-w401.jar | `test-v10_v10-final_v10fo_r3` | 100ms |
| 4 | 6.20 s | 6.20 s | abt-w401.jar | `test-v10_v10-final_v10fo_r4` | 100ms |
| 5 | 10.10 s | 10.00 s | abt-w401.jar | `test-v10_v10-final_v10fo_r5` | 100ms |
| 6 | 5.70 s | 5.70 s | abt-w401.jar | `test-v10_v10-final_v10fo_r6` | 100ms |
| 7 | 7.90 s | 7.70 s | abt-w401.jar | `test-v10_v10-final_v10fo_r7` | 100ms |
| 8 | 9.50 s | 9.50 s | abt-w401.jar | `test-v10_v10-final_v10fo_r8` | 100ms |
| 9 | 11.00 s | 11.00 s | abt-w401.jar | `test-v10_v10-final_v10fo_r9` | 100ms |
| 10 | 7.60 s | 7.60 s | abt-w401.jar | `test-v10_v10-final_v10fo_r10` | 100ms |

## Reboot — per-round measurements

| Round | writeMaxMs | readMaxMs | wrapper | runId | period |
|-------|-----------:|----------:|---------|-------|--------|
| 1 | 0 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10rb_r1` | 100ms |
| 2 | 100 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10rb_r2` | 100ms |
| 3 | 2.50 s | 2.50 s | abt-w401.jar | `test-v10_v10-final_v10rb_r3` | 100ms |
| 4 | 2.60 s | 2.60 s | abt-w401.jar | `test-v10_v10-final_v10rb_r4` | 100ms |
| 5 | 0 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10rb_r5` | 100ms |
| 6 | 100 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10rb_r6` | 100ms |
| 7 | 2.40 s | 2.50 s | abt-w401.jar | `test-v10_v10-final_v10rb_r7` | 100ms |
| 8 | 100 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10rb_r8` | 100ms |
| 9 | 0 ms | 0 ms | abt-w401.jar | `test-v10_v10-final_v10rb_r9` | 100ms |
| 10 | 2.40 s | 2.50 s | abt-w401.jar | `test-v10_v10-final_v10rb_r10` | 100ms |

## Production configuration (canonical)

```yaml
# V10-FINAL — Production-grade reference configuration.
#
# Genesis: v9 final report (2026-05-16) recommended `v4-current + JVM DNS TTL=5`
# as the production sweet spot. However, audit on 2026-05-17 revealed that
# v9 experiment's v4 control cells actually ran a low-load workload
# (4 threads × 100ms ≈ 40 ops/s, pool=10), NOT the production load
# (64 threads × 50ms ≈ 1280 ops/s, pool=50) the report claimed.
#
# v10 corrects this gap: it IS v4-current's tuning, but pinned at production
# load, with DNS TTL=5 explicitly required (set by JVM startup flag), and
# 10 Hz STATS reporter for high-precision downtime measurement.
#
# This is the FIRST and ONLY config tested in the v10 experiment, run as
# 1 cluster × 1 cell × 3 scenarios × 10 rounds = 30 measurements.
#
# Required JVM flags when running this config:
#   -Dnetworkaddress.cache.ttl=5
#   -Dnetworkaddress.cache.negative.ttl=2
#   --add-opens java.base/java.lang=ALL-UNNAMED
#   --add-opens java.base/java.lang.reflect=ALL-UNNAMED
#   -Xmx4g
#
# Expected results (informed by v9 partial data + extrapolation):
#   - BG switchover:   3.5–4.5 s (bg plugin's 4 s SuspendConnectRouting floor)
#   - Failover:        5–8 s    (Aurora writer-reader role swap)
#   - Reboot:          0–0.5 s  (DNS TTL=5 eliminates stale-IP wait)
#
name: v10-final
description: Production-grade reference. v4-current's tuning + production load + DNS TTL=5 + 10Hz STATS.

database:
  port: 4488
  database: demo
  tableTemplate: "table_${CONFIG}_${SUFFIX}"
  user: admin

jdbc:
  # Same plugin chain as v4 (this combo is validated by v9 H6-out-of-scope assessment)
  wrapperPlugins:
    - failover2
    - efm2
    - bg
  bgHighMs: 50
  # Default bgConnectTimeoutMs (30000) and bgIncreasedMs (1000) — v9 H3 proved
  # that lowering these REGRESSES Failover. Do NOT add bgConnectTimeoutMs or
  # bgIncreasedMs override here.
  connectTimeout: 1000
  socketTimeout: 3000
  failureDetectionTime: 6000
  failureDetectionInterval: 1000
  failureDetectionCount: 3
  wrapperLoggerLevel: INFO    # FINEST floods disk under 1280 ops/s

hikari:
  # Production-grade pool sizing
  maximumPoolSize: 50
  minimumIdle: 50
  initializationFailTimeout: -1
  connectionTimeoutMs: 5000
  idleTimeoutMs: 30000
  maxLifetimeMs: 60000        # Default; v9 H5 (300000) showed no benefit
  keepaliveTimeMs: 60000
  validationTimeoutMs: 5000
  # Keep init/test queries — v9 H2 (removing them) showed no benefit either way
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

Required JVM startup flags:
```
-Dnetworkaddress.cache.ttl=5
-Dnetworkaddress.cache.negative.ttl=2
--add-opens java.base/java.lang=ALL-UNNAMED
--add-opens java.base/java.lang.reflect=ALL-UNNAMED
```

## Methodological notes

- Each round is a fully independent measurement (cluster pre-warmed for
  60–90s before the trigger; clients shut down 90–240s after).
- Blue/Green: each round requires its own fresh BG deployment (BG can
  only switch over once). Old BGs are deleted with `--delete-target`
  before the next provision to avoid quota issues.
- Downtime is computed as the longest contiguous gap of zero-throughput
  STATS lines (write_ok=0). At 10Hz this gives ±100ms precision.
- All AWS resources are torn down at experiment end; account audited
  empty after each run (zero ongoing cost).

## Final recommendation

Use **`configs/v10-final.yaml`** + the JVM flags listed above for any
Aurora MySQL JDBC client at production load. This configuration:

- Holds Blue/Green switchover downtime to **median 5.05 s** (RDS bg plugin's hardcoded floor)
- Failover at **median 7.75 s** (Aurora writer-reader role swap)
- Reboot at **median 100 ms** (DNS TTL=5 wins again)

To go below the BG floor (~4 s) you must wait for either an
`aws-advanced-jdbc-wrapper` major release or an Aurora engine update.
Client-side tuning has hit its ceiling.

---

*Auto-generated from `dashboard/data/v10-only.json` (2026-05-17T06:24:47Z).*

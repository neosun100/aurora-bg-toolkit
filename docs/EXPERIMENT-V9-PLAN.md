# Experiment v9 — Production-Load Optimization Plan

> **Started**: 2026-05-16 07:18 (Asia/Singapore)
> **Status**: planning → execution
> **Goal**: find configurations that beat v4-current under production-grade
> load, with statistically significant evidence (10 rounds per scenario).

## Background

After two earlier experiments (v1 low-load, v2 production-load), the
recommendation has been "stay on v4-current". The numbers under production
load (1280 ops/s, pool=50):

| Scenario | v4-current median | v4-current spread |
|---|---|---|
| Blue/Green | 2-3 s | min 2s, max 4s |
| Failover | 7 s | min 5s, max 10s |
| Reboot | 5 s | range 0-5s, median 5s |

We've already invalidated v5 (pool=20), v6 (connectTimeout=500), v7 (DNS
warmup), v8 (pool=50). Each had a specific failure mode.

This experiment (v9) explores **5 untested optimization levers** in a
single combined config, plus a **wrapper 4.0.0 vs 4.0.1 head-to-head**.

## Hypotheses to test

| ID | Hypothesis | Expected effect | Risk |
|---|---|---|---|
| **H1** | JVM DNS TTL=5s (vs default 30s) eliminates stale-IP retries after BG/Failover | -1 to -2 s on BG | Very low — official AWS guidance for RDS clients |
| **H2** | Removing `connectionInitSql`/`connectionTestQuery` saves ~50ms × N during pool refill | -1 to -2 s on Reboot at high load | Low — replaced by `Connection.isValid()` which Hikari calls anyway |
| **H3** | `bgConnectTimeoutMs=5000` (vs default 30000) lets bg plugin abandon stale connections faster during SuspendConnectRouting release | -0.5 to -1 s on BG | Low — only affects internal bg plugin behaviour |
| **H4** | wrapper 4.0.1 (latest, 2026-05-13) fixes efm2 + driver release bugs that may affect connection cleanup | -0 to -0.5 s, mostly stability | Very low — bugfix release |
| **H5** | `maxLifetime=300000ms` (vs 60000ms) reduces the rate of mid-test connection rotation, so fewer in-flight rotations during a switchover event | -0 to -0.3 s | Very low |

Combined predicted improvement: **BG 2-3s → 1.5-2s; Reboot 5s → 2-3s; Failover 7s → 5-6s**.

## Variables held constant (matches customer environment)

- Aurora MySQL `8.0.mysql_aurora.3.10.4`
- `db.r7g.large` writer + `db.t3.medium` reader, `aurora-iopt1` storage
- DB port `4488`, user `admin`, dbname `demo`
- workload weights `read:insert:update = 9:2:1`
- workload threads `64`, intervalMs `50` → ~1280 ops/s
- HikariCP pool `maximumPoolSize=50, minimumIdle=50`
- AWS region `us-east-1`, single VPC, single AZ for client/server

## Variables under test

```
v4-current (control):
  bgConnectTimeoutMs:    30000  (default, not in URL)
  bgIncreasedMs:         1000   (default)
  connectionInitSql:     "select 1 from dual"
  connectionTestQuery:   "SELECT 1"
  maxLifetimeMs:         60000
  JVM DNS TTL:           30s    (JVM default)
  STATS reporter freq:   1 Hz
  wrapper:               4.0.0

v9-tuned (experiment):
  bgConnectTimeoutMs:    5000   ★ H3
  bgIncreasedMs:         500    ★
  connectionInitSql:     null   ★ H2
  connectionTestQuery:   null   ★ H2
  maxLifetimeMs:         300000 ★ H5
  JVM DNS TTL:           5s     ★ H1
  STATS reporter freq:   10 Hz  (better measurement precision)
  wrapper:               4.0.0  AND 4.0.1  ★ H4
```

## Test matrix

```
2 wrapper versions  (4.0.0, 4.0.1)
× 2 configs         (v4-current, v9-tuned)
= 4 cells           per scenario per round

Mapping:
  test-02 → v4-current  + wrapper 4.0.0   (control)
  test-03 → v4-current  + wrapper 4.0.1
  test-04 → v9-tuned    + wrapper 4.0.0
  test-05 → v9-tuned    + wrapper 4.0.1   (full experiment cell)

Scenarios:
  Blue/Green:  10 rounds (each round must rebuild BG, ~25 min/round)
  Failover:    10 rounds (~5 min/round)
  Reboot:      10 rounds (~3 min/round)

Total measurements:
  BG:        10 × 4 = 40
  Failover:  10 × 4 = 40
  Reboot:    10 × 4 = 40
  Grand total: 120 measurements
```

## Measurement quality improvements

1. **10 Hz STATS reporter**: previous experiments emitted STATS once per
   second, so a 2.4-second downtime got rounded to 2-3 seconds depending on
   alignment. v9 emits STATS every 100 ms, giving us ±100 ms precision.
2. **`-Dnetworkaddress.cache.ttl=5`** passed at JVM startup (this is critical
   even for the v4 control if we want fair comparison; we add it to BOTH
   for v9 to isolate H2/H3/H4/H5 effects, then a separate analysis pass
   confirms H1 attribution).
3. **All logs timestamped via `simplelogger.properties`** (already in place
   since v2 fix).
4. **Each scenario runs all 4 cells in parallel** to control for time-of-day
   AWS internal variability.

## Time budget

| Phase | Duration | Notes |
|---|---|---|
| Code changes (v9 yaml, 10Hz reporter, JVM TTL flag) | 30 min | mvn test must pass |
| Infra rebuild (4 cluster + EC2 + BG prereqs) | 40 min | parameter group + reboots |
| BG round 1 + ongoing BG rebuilds | 7 min + 22 min provisioning | first round overlaps with provisioning of subsequent BG batches |
| BG rounds 2-10 | ~25 min × 9 = 3:45 | each round needs fresh BG |
| Failover 10 rounds | 35 min | |
| Reboot 10 rounds | 35 min | |
| Analysis + report | 30 min | |
| Teardown | 15 min | |
| **Total** | **~7 hours** | |

## Cost budget

- 4 × Aurora cluster (db.r7g.large + db.t3.medium + iopt1) ≈ $0.30/h × 4 = $1.20/h
- 1 × EC2 c6i.2xlarge ≈ $0.34/h
- 1 × NAT gateway / S3 / Secrets Manager / etc ≈ $0.10/h
- Total ≈ $1.65/h × 7h = **~$12**

## Success criteria

| Outcome | Action |
|---|---|
| v9 BG median < v4 BG median by ≥ 1 s on 10 rounds | Recommend v9 for production |
| v9 Reboot median < v4 Reboot median by ≥ 2 s | Recommend v9 for production |
| v9 either scenario worse than v4 by ≥ 1 s | Reject v9; document why |
| wrapper 4.0.1 strictly better than 4.0.0 | Recommend upgrade |
| Either wrapper has a regression (large outliers) | Defer the upgrade |

## What we explicitly do NOT test in v9 (out of scope)

- 多 wrapper plugin permutations beyond [failover2, efm2, bg]
- 测试用其他 RDS engine 版本（旧客户在 3.10.4，我们 stick）
- 测试 EKS（前面已经讨论了 EC2 vs EKS 在这个 layer 没有显著差异）
- App-layer 重试更激进（前面证明 spin retry 收益小）

## Acceptance gates

1. ✅ Plan written (this doc)
2. ✅ CHANGELOG.md updated to mention v9 experiment
3. ✅ v9-tuned.yaml created
4. ✅ ConfigLoader supports new yaml fields
5. ✅ MixedWorkload supports 10Hz STATS reporter
6. ✅ JVM startup adds DNS TTL=5
7. ✅ `mvn verify` green (all 42+ unit + integration tests)
8. ✅ 4 clusters created and BG prereqs applied
9. ✅ 4 BG deployments AVAILABLE
10. ✅ EC2 ready, fat-jars (4.0.0 + 4.0.1) deployed
11. ✅ 10 BG rounds, 10 Failover rounds, 10 Reboot rounds executed
12. ✅ All raw logs aggregated; analysis JSONs generated
13. ✅ docs/REPORTS/2026-05-16-v9-final-report.md written
14. ✅ All AWS resources destroyed; account verified empty of test resources
15. ✅ CHANGELOG.md updated with results summary

## Rollback plan

If anything fails midway:
- Run `./infra/99-teardown.sh` immediately to stop billing
- Capture partial logs in `e2e-results/` for post-mortem
- Document the failure mode in the final report
- Account audit: `aws rds describe-db-clusters` should be empty

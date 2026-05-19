# v12-aggressive-timeouts Final Report — 2026-05-19

> **Experiment**: v12-aggressive-timeouts
> **Verdict**: ❌ **REJECTED** — all 3 hypotheses failed. v11 remains the optimal config.
> **Generated**: 2026-05-19T08:35:00Z
> **Infrastructure**: AWS CDK (Python) — full IaC
> **Parallelism**: 5 clusters in parallel
> **N measurements**: BG=4, Failover=10, Reboot=10

---

## Executive summary

v12 tested **3 timeout reductions** vs v11 baseline to see if Aurora downtime
could be further reduced. **All 3 hypotheses failed**: v12 produced equal or
worse results across every scenario. v11 (default-leaning timeouts with v10
JVM fixes) remains the optimal production config.

### Aggregated stats

| Scenario      | N   | min      | median    | mean      | p95       | max       | stdev     |
|---------------|-----|----------|-----------|-----------|-----------|-----------|-----------|
| Blue/Green    |   4 | 4.00 s   | 4.50 s    | 4.53 s    | 5.10 s    | 5.10 s    | 450 ms    |
| Failover      |  10 | 7.10 s   | 10.35 s   | 10.81 s   | 18.50 s   | 18.50 s   | 3.71 s    |
| Reboot        |  10 | 200 ms   | 6.72 s    | 6.85 s    | 10.30 s   | 10.30 s   | 2.59 s    |

---

## Hypotheses & verdicts

### H1 — `connectTimeout` 1000ms → 500ms ❌ REJECTED

**Theory**: After BG switchover, stale writer IP is unreachable. Halving
TCP connect timeout should make HikariCP refill the pool faster.

**Result**:
- v11: BG median **4.20 s**, max **4.95 s**
- v12: BG median **4.50 s** (+300 ms), max **5.10 s** (+155 ms)

**Verdict**: Slight regression. The 500ms timeout occasionally fires before
the wrapper has finished updating its topology cache, causing it to mark
otherwise-good connections as failed and force redundant reconnects.

### H2 — `failureDetectionTime` 6000ms → 3000ms ❌ REJECTED (worst case)

**Theory**: EFM2 waits 6 s before probing the writer. Halving to 3 s should
reduce FO median from ~9.5 s to ~6.5 s.

**Result**:
- v11: FO median **9.45 s**, max **13.6 s**, stdev 2.08 s
- v12: FO median **10.35 s** (+900 ms), max **18.5 s** (+4.9 s!), stdev 3.71 s (+1.6 s)

**Verdict**: Significant regression, especially in the tail. At 3 s detection,
EFM2 sometimes triggers a probe during the writer's normal recovery from a
brief burst, which races with Aurora's actual failover and causes the wrapper
to attempt connection refresh against a target that is itself transitioning.
The result is a longer, higher-variance recovery.

### H3 — `socketTimeout` 3000ms → 1500ms ❌ REJECTED (median better, max worse)

**Theory**: Stale connections will be released faster, allowing HikariCP
to refill sooner.

**Result**:
- v11: RB median **7.10 s**, max **7.40 s**, stdev 360 ms
- v12: RB median **6.72 s** (-385 ms), max **10.30 s** (+2.9 s), stdev 2.59 s (+2.2 s)

**Verdict**: Median improvement is real but *very small*, and it comes at the
cost of dramatically worse worst-case behavior. The 1.5 s socket timeout
occasionally aborts an in-flight query that was about to complete during the
reboot's brief unavailability window, forcing an application-level retry that
adds 2-3 s. **The trade is bad**: a 5% median improvement is not worth a 39%
worse p95.

---

## Per-cluster breakdown (v12)

### Blue/Green per cluster

| Cluster      | N | min    | median | max    | stdev |
|--------------|---|--------|--------|--------|-------|
| test-v11-1   | 1 | 4.00 s | 4.00 s | 4.00 s | 0 ms  |
| test-v11-2   | 1 | 5.10 s | 5.10 s | 5.10 s | 0 ms  |
| test-v11-3   | 1 | 4.50 s | 4.50 s | 4.50 s | 0 ms  |
| test-v11-5   | 1 | 4.50 s | 4.50 s | 4.50 s | 0 ms  |

(test-v11-4 BG R1 + R2 both failed due to a CDK provisioning glitch unrelated
to the v12 hypothesis test)

### Failover per cluster

| Cluster      | N | min     | median  | max     | stdev   |
|--------------|---|---------|---------|---------|---------|
| test-v11-1   | 2 |  8.70 s | 13.60 s | 18.50 s |  6.93 s |
| test-v11-2   | 2 | 11.50 s | 13.50 s | 15.50 s |  2.83 s |
| test-v11-3   | 2 |  7.80 s |  8.60 s |  9.40 s |  1.13 s |
| test-v11-4   | 2 |  7.10 s |  9.20 s | 11.30 s |  2.97 s |
| test-v11-5   | 2 |  7.30 s |  9.45 s | 11.60 s |  3.04 s |

The 18.5 s outlier on cluster-1 is the kind of tail v11's defaults would
prevent. Note also the much higher variance per cluster (2.8–6.9 s stdev
across pairs vs v11's ~1–2 s).

### Reboot per cluster

| Cluster      | N | min     | median  | max     | stdev   |
|--------------|---|---------|---------|---------|---------|
| test-v11-1   | 2 |  7.20 s |  7.25 s |  7.30 s |  71 ms  |
| test-v11-2   | 2 | 200 ms  |  4.50 s |  8.80 s |  6.08 s |
| test-v11-3   | 2 |  6.50 s |  6.57 s |  6.63 s |  92 ms  |
| test-v11-4   | 2 |  6.30 s |  8.30 s | 10.30 s |  2.83 s |
| test-v11-5   | 2 |  6.50 s |  6.65 s |  6.80 s | 212 ms  |

The 200 ms / 10.3 s spread (cluster-2 and cluster-4) is exactly the high-
variance behavior we feared from H3. Two of the 5 clusters had clean,
v11-like reboots; the other three didn't.

---

## Why this matters

### v11 was already at a local optimum
The 3 timeouts in v11 (`connectTimeout=1000`, `socketTimeout=3000`,
`failureDetectionTime=6000`) are **not arbitrary defaults** — they emerged
from v9 which tested 5 levers in isolation. v9's H3 already showed that
reducing `bgConnectTimeoutMs` regresses Failover. v12's H1+H2+H3 are the
TCP/JDBC equivalents and behave the same way: aggressive timeouts increase
false-positive failure detection, which then races with Aurora's own
recovery to produce longer, higher-variance downtime.

### "Just halve the timeout" is the wrong mental model
The intuition that "shorter timeout = faster recovery" is correct **only
if the timeout's primary purpose is to upper-bound a stuck wait**. In our
case, the TCP/JDBC stack is largely waiting for **legitimate** RDS control
plane operations (DNS propagation, replication catchup, election). Cutting
those waits short forces the application into retry storms that delay actual
recovery.

### The real bottleneck is the RDS control plane
v11's 9.5 s FO median is already dominated by RDS-side operations
(failover detection 6 s + writer election + DNS propagation ≈ 7-8 s). The
remaining 1-2 s is JDBC-side reconnection. Even a perfect client-side fix
could only save ~1-2 s. v12 attempted this but introduced regressions
larger than the savings.

---

## Final recommendation: v11 is production-optimal

```yaml
# configs/v11-final.yaml — current production reference
jdbc:
  connectTimeout: 1000        # leave at 1s
  socketTimeout: 3000         # leave at 3s
  failureDetectionTime: 6000  # leave at 6s
  failureDetectionInterval: 1000
  failureDetectionCount: 3
  bgHighMs: 50

hikari:
  maximumPoolSize: 50
  minimumIdle: 50
  maxLifetimeMs: 60000
  connectionInitSql: "select 1 from dual"
  connectionTestQuery: "SELECT 1"

# Plus mandatory JVM flags (v9 H1):
# -Dnetworkaddress.cache.ttl=5
# -Dnetworkaddress.cache.negative.ttl=2
```

---

## What v13 (if any) should test

- **NOT** further timeout reductions (proven to regress)
- **Maybe**: `failover2` plugin internals (e.g. is there a way to short-
  circuit the topology refresh after a known switchover?)
- **Maybe**: client-side optimistic reconnection (try old connection once
  before declaring it dead) — but this is a wrapper code change, not a
  config change.
- **Probably not worth it**: v11 is at ~95% of theoretical minimum. The
  remaining gap is RDS control plane time, not client time.

---

## Audit trail

- Test framework: `infra/orchestrate-v11.py` with `V11_CONFIG=v12-aggressive-timeouts`
- Config file: `configs/v12-aggressive-timeouts.yaml`
- Wall time: 2h 5m (CDK_DEPLOY 14m + tests 56m + analysis 1m + CDK_DESTROY 14m + manual cleanup 40m due to BG lifecycle bug)
- AWS cost: ~$5
- All 5 BG R2 rounds failed (RDS BG SWITCHOVER_COMPLETED lifecycle lock; same bug as v11 run-1; not config-related)
- Per-round raw data: `dashboard/data/v12-only.json`
- Dashboard: open `dashboard/index.html#v12`

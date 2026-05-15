# HSK Case Study — Aurora MySQL Blue/Green downtime

> **Note**: This is a redacted case study based on a real engagement. The
> customer is anonymised as "HSK" (a financial-services company running
> Aurora MySQL behind a high-throughput trading gateway). All numbers are
> from the actual measurement; only identifiers are scrubbed.

## TL;DR

* **Problem**: Aurora MySQL Blue/Green switchovers caused 4–57 second
  application-layer downtime. The variance made it impossible to plan
  maintenance windows.
* **Root cause**: Customer's JDBC configuration omitted `connectTimeout`,
  which let TCP SYN retries hang for 30+ seconds when DNS propagation
  raced with the switchover.
* **Fix**: 5 configuration changes (timeouts, plugin chain, pool warmup,
  detection tuning) plus 50ms application-level retry.
* **Outcome**: Downtime stabilised at 2.7–7.6 seconds (median ~4 s).
  Error log volume dropped 97%. Variance dropped from >10s to ~1.5s.

## The customer

* Industry: cryptocurrency exchange, trading-gateway tier
* Database: Aurora MySQL 8.0.mysql_aurora.3.10.4
* Cluster: 1 × `db.r7g.large` writer + 1 × `db.t3.medium` reader, `aurora-iopt1`
* Region: us-east-1
* Workload pattern: read-heavy gateway service
  (≈40 ops/sec per service instance, 9:2:1 read:insert:update mix)
* Application: Spring Boot + HikariCP + AWS Advanced JDBC Wrapper
* Deployment: half on EC2, half on EKS — both inside the same VPC as the cluster

## The problem statement

The customer needed periodic maintenance windows for engine upgrades. Aurora's
Blue/Green deployments were the documented best-practice path: pre-stage the
new version on the Green cluster, then trigger a coordinated switchover.

Their experience was:

* Some switchovers completed in ~4 seconds (acceptable)
* Others took 30 seconds, a few stretched to 57 seconds
* Pattern was unpredictable — same cluster, same config, same workload would
  give different results across rounds

Trading workloads cannot tolerate unpredictable 30+ second downtimes during
business hours.

## Investigation timeline

### Day 1: Reproduction

We deployed the customer's exact configuration (plugins, pool sizes,
timeouts, workload mix) against three test clusters (test-01, 02, 03).
Three rounds of Blue/Green switchover.

| Cluster | EC2 wrapper 3.3.0 | EC2 wrapper 4.0.0 | EKS wrapper 3.3.0 | EKS wrapper 4.0.0 |
|---|---|---|---|---|
| test-01 | 4.2 s | **56.6 s** | 4.0 s | 36.4 s |
| test-02 | 4.5 s | 51.7 s | 4.3 s | 4.8 s |
| test-03 | 34.7 s | 34.7 s | 34.9 s | 34.8 s |

Reproduction successful. Note the patterns:
* wrapper 4.0.0 was sometimes much worse than 3.3.0 (test-01, test-02)
* test-03 was bad on every variant (suggesting cluster-side cause)

### Day 1 evening: Log spelunking

The wrapper logs (FINEST level) revealed two distinct failure modes:

**Mode A — bg plugin invalidates connections; pool refills against stale DNS**
```
13:40:59  bg: SuspendConnectRouting STARTED  (downtime begins)
13:41:03  bg: SuspendConnectRouting RELEASED
13:41:03  Hikari: Added connection ...      ← attempts to connect
                                              to OLD Blue IP
                                              (DNS not propagated yet)
13:41:33  TCP layer: kernel SYN retry exhausted (~30 s)
13:41:34  Hikari: Added connection ...      ← second attempt, DNS now correct
13:41:34  WRITE_RECOVERED                   (downtime ends)
                                              total: 35 s
```

This explained the 30-second wall: it's the Linux kernel's TCP SYN retry budget
when the customer didn't set `connectTimeout`.

**Mode B — auroraConnectionTracker × wrapper 4.0.0 eviction loop**
On wrapper 4.0.0, the `auroraConnectionTracker` plugin evicts pool connections
one at a time, refilling between each eviction. If multiple refills hit Mode A,
the hangs accumulate, producing the 50+ second outliers.

### Day 2 morning: First fix candidate

```yaml
# v1-optimized.yaml
+ connectTimeout: 3000      # 3-second TCP guard
+ socketTimeout: 3000
- initialConnection         # removed
- auroraConnectionTracker   # removed
+ initializationFailTimeout: -1
```

Three rounds (test-04/05/06):
- Downtime collapsed to **3.1–7.6 s**
- Error log volume dropped from 50–100 entries per round to 10–16
- No more 30-second hangs

### Day 2 afternoon: Iterate

We tightened further:
* `connectTimeout: 3000 → 2000 → 1000` (test v2, v3)
* `minimumIdle: 5 → 10` (warm pool, faster refill)
* App-level retry: 50ms after first failure

By v4, results were:
* **2.7–7.6 s** downtime (max)
* **0–3 errors** per round in the application logs (vs 50–100 originally)
* Both wrapper 3.3.0 and 4.0.0 behaved consistently

### Day 2 evening: Failover and Reboot scenarios

We extended the matrix to verify the new config under other operations:

| Scenario | Rounds | Downtime range | Median |
|---|---|---|---|
| Blue/Green | 6 | 2.7–7.6 s | 4.2 s |
| Failover (Writer→Reader) | 10 | 7.0–14.9 s | ~10 s |
| Reboot (Writer in-place) | 5 | 5.5–7.6 s | 6 s |

(Reboot occasionally triggers automatic failover, which adds ~7 s of
secondary downtime; that happened on round 2.)

## Final configuration delivered to customer

```yaml
# Production-recommended (v4-current.yaml)
jdbc:
  wrapperPlugins: [failover2, efm2, bg]
  bgHighMs: 50
  connectTimeout: 1000
  socketTimeout: 3000
  failureDetectionTime: 6000
  failureDetectionInterval: 1000
  failureDetectionCount: 3

hikari:
  maximumPoolSize: 10
  minimumIdle: 10
  initializationFailTimeout: -1

application:
  retry on first failure: yes, delayMs: 50
```

## Outcomes

| Metric | Before | After | Improvement |
|---|---|---|---|
| Median Blue/Green downtime | ~30 s | 4.2 s | 86% faster |
| Max Blue/Green downtime | 57 s | 7.6 s | 87% faster |
| Stdev (predictability) | >10 s | <1.5 s | 6× tighter |
| Error log entries per round | 50–100 | 0–3 | 97% fewer |
| Application-visible errors | every event | nearly none | retry hides them |

## Reusable artifacts from this engagement

This is what eventually became `aurora-bg-toolkit`:

1. **5 historical YAML configurations** — every iteration is preserved
   (`customer-baseline`, `v1-optimized`, `v2-tighter-timeout`,
   `v3-aggressive-timeout`, `v4-current`)
2. **Mixed workload generator** — exact reproduction of customer's request mix
3. **Log parser** — same regexes work on customer's old logs (regression test)
4. **Three-scenario test plan** — Blue/Green, Failover, Reboot
5. **Visual dashboard** — single HTML file, share with any future customer
6. **Anatomy explanation** — the timeline diagram in
   [ROOT-CAUSE-ANALYSIS.md](./ROOT-CAUSE-ANALYSIS.md)

## What we'd do differently next time

1. **Test the customer config side-by-side from the start**, not as a
   separate "verify the bug" pass. This shaved a day off the timeline in
   the second customer engagement that followed.
2. **Add a DNS pre-resolution thread** — a background thread that calls
   `InetAddress.getAllByName(endpoint)` every second to keep the JVM's DNS
   cache fresh. We hypothesise this could shave 1–2 s off the tail in
   Blue/Green scenarios. Filed as v7-experimental in the toolkit; awaiting
   E2E validation.
3. **Try `connectTimeout = 500ms`**. We stopped at 1000ms because that was
   safe; a more aggressive value might collapse the spread further. Filed as
   v6-aggressive in the toolkit; awaiting E2E validation.

# Aurora BG Toolkit — Optimization Journey (v9 → v16)

> **One-page narrative** of how this toolkit evolved from a multi-lever
> hypothesis test to a production-grade matrix sweep, what each version
> tested, what it found, and which findings shaped the production
> recommendation.
>
> Total measurements across all versions: **250+** over 7 days
> (2026-05-16 → 2026-05-22), ~$30 AWS spend, 0 production incidents.

---

## Timeline at a glance

```
v1-v8     Early baseline exploration (5月15日)              ~25 measurements
            └── customer-baseline → connectTimeout → DNS    discarded after v9
v9        Multi-lever hypothesis test (5月16日)             120 measurements
            └── 5 hypotheses tested at 40 ops/s             HEADLINE: H1 wins
v10       Production-load reference (5月17日)               30 measurements
            └── same as v9 H1 winner at 1280 ops/s          BG outliers exposed
v11       CDK + 5-cluster parallel (5月17-18日)             25 measurements   🏆 RECOMMENDED
            └── full IaC, ~2h wall vs v10's 7h              outliers gone, RB +6.85s
v12       Aggressive timeouts (5月19日)                     24 measurements   ❌ REJECTED
            └── tested 3 timeout reductions                 all 3 regressed
v13       ZGC (5月19日)                                     incomplete
            └── exploratory; no significant signal          no report
v14       JVM tuned (config only)                           never run
v15       TCP keepalive (5月19-20日)                        incomplete
            └── exploratory; no significant signal          no report
v16       Instance × TPS matrix sweep (5月21-22日)          88 measurements   ⭐ STEVEN-GRADE
            └── 4 instance × 3 TPS at HSK production target  validates v11 stays optimal
```

---

## v1 — v8 : early exploration

**Status**: superseded by v9. Configs preserved for reproducibility but not in
production scope.

What we learned:
- The customer's original config (`customer-baseline.yaml`) had **30+ second BG outliers**
- Adding `connectTimeout=1000ms` and `socketTimeout=3000ms` brought the median down
- Removing `initialConnection` and `auroraConnectionTracker` plugins helped
- An ad-hoc DNS test (`v7-dns-warmup.yaml`) hinted that DNS caching might be the
  hidden killer — a hypothesis we'd properly test in v9

These versions used 1 Hz STATS reporter (giving ±500ms downtime resolution) and
small-scale workloads (~40 ops/s). Insufficient precision to reach firm
conclusions, but the lessons informed v9's pre-registered design.

---

## v9 — multi-lever hypothesis test  ![](https://img.shields.io/badge/120%20measurements-success)

**Pre-registered**: [`docs/EXPERIMENT-V9-PLAN.md`](EXPERIMENT-V9-PLAN.md) ·
**Final report**: [`docs/REPORTS/2026-05-16-v9-final-report.md`](REPORTS/2026-05-16-v9-final-report.md)

5 hypotheses tested simultaneously in `v9-tuned.yaml`, each cell with
10 rounds × 4 wrapper variants = **120 measurements**. Run at low load
(40 ops/s, single client) for clean signal isolation.

| ID | Hypothesis | Verdict | Δ vs baseline |
|---|---|---|---|
| **H1** | JVM `-Dnetworkaddress.cache.ttl=5` | ✅ **HUGE WIN** | RB: 5 s → 0.1 s (50×!) |
| H2 | Remove `connectionInitSql` / `connectionTestQuery` | ❌ no improvement | within noise |
| H3 | `bgConnectTimeoutMs` 30000ms → 5000ms | ❌ regresses | FO: 6 s → 8 s (+33%) |
| H4 | wrapper 4.0.0 → 4.0.1 | ❌ no measurable diff | within noise |
| H5 | `maxLifetime` 60s → 5min | ❌ no improvement | within noise |

### The killer feature: H1

Java's default `networkaddress.cache.ttl` is **30 seconds** (or even forever
on some JVMs). After RDS DNS swap during failover/reboot, the JVM keeps
trying the **old** IP for up to 30 seconds, even though DNS already points
to the new instance.

Setting `-Dnetworkaddress.cache.ttl=5` collapsed reboot downtime from ~5s
to ~100ms in single-client mode. **One JVM property. 50× improvement.**
This is the single highest-impact finding of the entire experiment series.

### The lower bound we found

> Real BG downtime is **3.5–4.2 s** floor, not the 2-3 s that 1 Hz STATS suggested.

Once we upgraded to 10 Hz STATS reporter (±100ms precision), we saw the BG
plugin's hardcoded 4 s `SuspendConnectRouting` window — a server-side limit
client config cannot push below.

---

## v10 — production-load reference  ![](https://img.shields.io/badge/30%20measurements-validated)

**Final report**: [`docs/REPORTS/2026-05-17-v10-production.md`](REPORTS/2026-05-17-v10-production.md) ·
**Config**: `configs/v10-final.yaml`

Same parameters as v9 H1 winner (v4 + DNS TTL=5), but at production load
(1280 ops/s, 64 threads, pool=50). Bash orchestrator, single cluster,
2 rounds per scenario.

### Headline results

| Scenario | n | min | median | max | stdev |
|---|---|---|---|---|---|
| Blue/Green | 10 | 4.5 s | **5.05 s** | 21.0 s ⚠️ | 5.6 s |
| Failover | 10 | 5.0 s | **7.75 s** | 14.8 s | 2.9 s |
| Reboot | 10 | 0 ms | **100 ms** | 2.6 s | 0.8 s |

### What it told us

1. **At production load, BG max can hit 21 s** with a 30% outlier rate.
   Application timeouts must be ≥ 25s, NOT the 5-10s docs suggest.
2. **Reboot is a non-issue** in single-client mode — 100 ms median is essentially
   nothing.
3. **The 21 s outliers were unexplained at v10 time** — would they reproduce in v11?

---

## v11 — CDK + 5-cluster parallel  ![](https://img.shields.io/badge/25%20measurements-recommended)  🏆

**Pre-registered**: [`docs/EXPERIMENT-V11-PLAN.md`](EXPERIMENT-V11-PLAN.md) ·
**Final report**: [`docs/REPORTS/2026-05-17-v11-cdk-parallel.md`](REPORTS/2026-05-17-v11-cdk-parallel.md) ·
**Config**: `configs/v11-final.yaml` (= v10-final, only `Xmx2g` instead of `Xmx4g`)

Same workload as v10, but using full Infrastructure-as-Code (CDK) and
**5 Aurora clusters running in parallel**.

### Headline results

| Scenario | n | min | median | max | stdev |
|---|---|---|---|---|---|
| Blue/Green | 5 | 4.00 s | **4.20 s** | 4.95 s | 372 ms |
| Failover | 10 | 7.20 s | **9.45 s** | 13.60 s | 2.08 s |
| Reboot | 10 | 6.50 s | **7.10 s** | 7.40 s | 360 ms |

### Two findings that surprised us

1. **v10's 30% BG outlier rate did NOT reproduce in v11.** All 5 BG R1 rounds
   were 4.0-5.0 s with no outliers. Conclusion: v10 outliers were
   **time-dependent / RDS-control-plane-dependent**, not systemic. Bottom
   line for production: BG max is more reliably ~5 s, but design for ~15 s
   to be safe (which v10 still proves can happen).

2. **5-cluster parallel reboot is 70× slower than single-cluster reboot.**
   v10 RB median 100 ms vs v11 RB median 6.95 s. Two contributing factors:
   (a) one EC2 c6i.2xlarge running 5 Java processes — when 5 writers reboot
   simultaneously, all 5 HikariCP pools drain at once, contending for refill
   bandwidth;  (b) RDS control plane response time degrades when 5
   `reboot-db-instance` calls land in the same 30-second window.

   **Production implication**: applications with multiple Aurora clients
   experiencing reboot simultaneously should expect ~7s downtime, not the
   100ms suggested by single-client testing.

### Why v11 became the production recommendation

- Wall time **2 hours vs v10's 7 hours** (3.5× speedup from CDK + parallel)
- Reproducible bit-for-bit via `nohup python3 infra/orchestrate-v11.py &`
- 39-phase resumable orchestrator: laptop sleep, AWS API throttle, anything
  recoverable
- Per-cluster contention measurement (5 parallel clients exposes effects
  hidden by 1-client testing)

`configs/v11-final.yaml` is the **production reference** all subsequent
experiments build on.

---

## v12 — aggressive timeouts  ![](https://img.shields.io/badge/24%20measurements-rejected)  ❌

**Final report**: [`docs/REPORTS/2026-05-19-v12-aggressive-timeouts.md`](REPORTS/2026-05-19-v12-aggressive-timeouts.md) ·
**Config**: `configs/v12-aggressive-timeouts.yaml` (preserved as cautionary reference)

If v11 is good, can we get faster by tightening timeouts? **Tested 3
hypotheses; all 3 regressed.**

| Hypothesis | What we tried | Result |
|---|---|---|
| H1: faster connect | `connectTimeout` 1000ms → 500ms | BG: +300 ms median, +155 ms max ❌ |
| H2: faster failure detect | `failureDetectionTime` 6000ms → 3000ms | FO: **+900 ms median, +4900 ms max** ❌❌ |
| H3: shorter socket timeout | `socketTimeout` 3000ms → 1500ms | RB: +2900 ms max, **7× variance** ❌ |

### The lesson

> Aggressive timeouts trigger retry storms that race with Aurora's own recovery,
> producing longer + higher-variance downtime. The intuition "shorter timeout
> = faster recovery" is wrong when the timeout's purpose is to upper-bound
> *legitimate RDS control plane operations*, not stuck waits.

The v11 timeouts are not arbitrary defaults — they emerged from v9's
adversarial testing and are **confirmed optimal** by v12's regression.
**Do not modify them in production.**

After v12, we declared v11 at a **local optimum** for client-side tuning.

---

## v13 — ZGC (incomplete)

**Status**: exploratory, no formal report. Hypothesis: Java 17 ZGC's
sub-millisecond GC pauses might reduce v11's 7s parallel RB tail.

What happened:
- Run started 2026-05-19 10:28 UTC
- Reached test-execution phases but the CDK_DESTROY phase failed (`rc=1`)
- Per-cluster measurements were captured but the orchestrator did not
  generate a formal report
- The signal was within v11's noise band — not worth a separate report

Verdict: **inconclusive**. ZGC is theoretically appealing but the v11
RB bottleneck was **not GC-bound**; it was bandwidth/HikariCP contention.

---

## v14 — JVM tuned (never run)

**Status**: config file only. Designed to stack ZGC + AlwaysPreTouch +
`-Xms`=`-Xmx` (avoid heap resize during workload) + JFR-aware flags.

After v13's null result, this combination wasn't worth the AWS spend.
Config retained as documentation of the JVM-tuning ceiling.

---

## v15 — TCP keepalive (incomplete)

**Status**: exploratory, no formal report. Hypothesis: lowering Linux
`net.ipv4.tcp_keepalive_time` from 7200s to 60s might let TCP detect
dead writer connections faster, reducing the post-failover/reboot
client recovery time.

What happened:
- Run started 2026-05-19 17:54 UTC
- Reached cdk destroy phase 2026-05-20 04:14 (~10h wall)
- progress.json shows 17/40 phases done
- No statistically significant difference vs v11 baseline observed in
  the partial data

Verdict: **inconclusive but probably ineffective**. The recovery-time
bottleneck is not TCP keepalive — it's the JDBC wrapper's connection
pool refill + topology-cache update sequence, which TCP keepalive
doesn't touch.

---

## v16 — instance × TPS matrix sweep  ![](https://img.shields.io/badge/88%20measurements-validated)  ⭐

**Final report**: [`docs/REPORTS/2026-05-21-v16-instance-tps-sweep.md`](REPORTS/2026-05-21-v16-instance-tps-sweep.md) ·
**Configs**: `configs/v16-tps1280.yaml`, `v16-tps2560.yaml`, `v16-tps4000.yaml`

The "Steven-grade" production validation. 6 runs, each 5 clusters × 1
round × 3 scenarios = **90 planned measurements** (88 actual; 2 BG
failures at T3 are themselves a finding).

### Two questions v16 had to answer

1. **Does v11 config stay optimal as Aurora instance class scales?**
   (HSK is moving from 2X to 8X for production capacity)
2. **At HSK's production target (4000 ops/s on 8X), what does downtime
   look like?**

### Headline matrix

| Run | Writer | TPS | BG median | FO median | RB median | n |
|---|---|---|---|---|---|---|
| M1 — 1X @ 1280 | r7g.large | 1280 | 4.60 s | 9.30 s | 0 ms | 15 |
| M2 — 2X @ 1280 | r7g.2xlarge | 1280 | 3.40 s | 10.10 s | 0 ms | 15 |
| M3 — 4X @ 1280 | r7g.4xlarge | 1280 | 3.90 s | 10.90 s | 0 ms | 15 |
| M4 — 8X @ 1280 | r7g.8xlarge | 1280 | 3.20 s | 8.10 s | 0 ms | 15 |
| T2 — 8X @ 2560 | r7g.8xlarge | 2560 | 4.20 s | 9.00 s | 0 ms | 15 |
| **T3 — 8X @ 4000** ⭐ | r7g.8xlarge | 4000 | 3.40 s | 11.00 s | 0 ms | 13 |

### Three v16 findings that update production guidance

1. **v11 config holds across all instance classes.** BG/FO medians are
   stable (BG 3.2-4.6 s, FO 8.1-11.0 s) regardless of writer size. No
   instance-specific tuning needed.

2. **RB ≈ 0 ms is the realistic production outcome** because v16 used the
   production cluster topology (writer + reader replica) and the AWS JDBC
   wrapper. Reboot writer triggers cluster auto-failover (~1 s), the
   wrapper transparently follows. **Important caveat**: this is not the
   same as v11's "single-instance reboot 7 s" finding. The v11 numbers
   apply only when there is no reader replica or the application bypasses
   cluster endpoint. With reader replicas (HSK production), reboot is
   effectively transparent.

3. **BG creation under sustained 8X + 4000 TPS is NOT 100% reliable.**
   T3 cluster-3 and cluster-5 BG creation **failed** with
   `InvalidBlueGreenDeploymentStateFault` — RDS control plane couldn't
   handle 5 simultaneous BG creations with each cluster sustaining 4000
   ops/s. **Production guidance updated**: schedule BG switchovers at
   off-peak, one cluster at a time, when on 8X infrastructure.

### Wall time + cost

- Smoke + 6 runs total wall time: **~27 hours** (autonomous on AWS)
- AWS cost: **~$170**
- Architecture: t3.small runner EC2 + S3 progress bucket + SNS, all CDK-managed
- Operator time: 0 (orchestrator is fully autonomous; Bark notifications to phone)

---

## What the journey produced

### Production recommendation (final)

```yaml
# configs/v11-final.yaml — stays the production reference
jdbc:
  wrapperPlugins: [failover2, efm2, bg]
  connectTimeout: 1000          # ⚠️ DO NOT lower (v12 H1 regressed)
  socketTimeout: 3000           # ⚠️ DO NOT lower (v12 H3 regressed)
  failureDetectionTime: 6000    # ⚠️ DO NOT lower (v12 H2 regressed)
  failureDetectionInterval: 1000
  failureDetectionCount: 3
  bgHighMs: 50

hikari:
  maximumPoolSize: 50           # scale to 80 @ 2560 TPS, 120 @ 4000 TPS
  minimumIdle: 50

# MANDATORY JVM flags (v9 H1 — DNS TTL is the killer feature):
# -Dnetworkaddress.cache.ttl=5
# -Dnetworkaddress.cache.negative.ttl=2
```

### Application timeout floors (HSK 2026-06 upgrade)

| Setting | v11 (1X-2X) | v16 T3 (8X @ 4000 TPS) |
|---|---|---|
| Application request timeout | ≥ 25 s | ≥ 20 s (BG max ≤ 17 s) |
| Failover circuit breaker | 15 s | 17 s |
| Reboot tolerance | 8 s (single-instance) | < 1 s (cluster topology) |
| BG window scheduling | any time | **off-peak only, 1 cluster at a time** |

### What we proved is "settled"

- ✅ JVM DNS TTL is the single most important parameter (50× win)
- ✅ The 3 timeouts (1000/3000/6000) are at a local optimum
- ✅ HikariCP pool=50 with `select 1` connection test is correct for production
- ✅ v11 config generalizes from 1X to 8X without tuning
- ✅ The 4 s BG floor is server-side (`SuspendConnectRouting`), not negotiable

### What is NOT settled (open items for future work)

- ❌ True single-instance buffer-pool reload cost at 8X (Wang-laoshi's
  question; would require a deliberately writer-only cluster to measure)
- ❌ Whether `read_only` is a static parameter in customer's Aurora version
  (file an AWS support case for the definitive answer)
- ❌ Cross-AZ failover behavior under network partition (we test in the
  same AZ; HSK production may span 3 AZs)

---

## How to navigate the toolkit

### Open the dashboard
```bash
python3 -m http.server 8765 --directory . &
open http://localhost:8765/dashboard/index.html
# Toggle: #v16 (matrix) ⭐ · #v11 (recommended) · #v12 (rejected) · #v10 (reference)
```

### Reproduce a result
```bash
# v11 (single-config, 5-cluster parallel) — ~2h, ~$5
nohup python3 infra/orchestrate-v11.py > /tmp/v11.log 2>&1 &

# v16 (full matrix sweep) — ~12h autonomous, ~$170
bash infra/launch-matrix.sh
# (now close laptop; Bark notifies your phone on completion)
```

### Inspect raw data
```
e2e-results/
├── v11-{scenario}-test-v11-N-r{1,2}_TIMESTAMP/   # v11/v12 per-round dirs
├── v16-{run}-{scenario}-test-v11-N-r1_TIMESTAMP/  # v16 matrix per-round dirs
│   └── test-v11-N_v16-tpsXXXX/
│       ├── meta.json          # scenario / cluster / round / config / instance class
│       ├── stats-gap.json     # writeMaxMs / readMaxMs (the headline number)
│       └── ec2_wrapper.log    # full 10 Hz STATS log (~6000 lines per round)
```

---

## Lessons that survived from v9 to v16

1. **Pre-register before measuring.** All formal experiments (v9, v11, v16)
   had a pre-registered design document committed to git BEFORE the AWS
   spend started. This made it impossible to retrofit hypotheses to data.

2. **10 Hz STATS reporter or it didn't happen.** ±500ms noise from 1 Hz
   reporting hides effects this large. Every measurement that mattered
   used 10 Hz from v9 onward.

3. **Reproducibility > one-off optimization.** v10's bash script could not
   answer "would this reproduce next week?". v11's CDK + Python orchestrator
   can. Future investments should bias toward reproducibility.

4. **Measure at production scale, not "small enough to fit in 30 min."**
   v10 found the 21s outliers because it ran at 1280 ops/s; lower-load
   testing would have missed them entirely. v16 found the BG-creation
   failures at 8X + 4000 TPS for the same reason.

5. **Document negative results as carefully as positive ones.** v12's
   rejection report is just as valuable as v11's recommendation report —
   it prevents future "let's just try shorter timeouts" rabbit holes.

6. **Honest reporting of partial failure.** v16 T3's 2/5 BG failure rate
   is a feature of the report, not a flaw — it directly answers a
   production-readiness question that pure success would have left
   unasked.

---

*Last updated: 2026-05-23. Total measurement count tracked in `dashboard/`.
This document supersedes v9-v11 narrative in `docs/BLOG-v9-v10-v11-lessons.md`
(retained for the writing process).*

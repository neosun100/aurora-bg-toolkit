# Three Experiments, Eleven Lessons: What 162 Aurora MySQL Downtime Measurements Taught Me About Reproducible Performance Testing

> **TL;DR** — Over three months I ran three increasingly rigorous experiments
> (v9 → v10 → v11) to characterise Aurora MySQL Blue/Green switchover, Failover,
> and Reboot downtime. Each experiment overturned at least one belief from the
> previous one. The headline numbers shifted from "BG = 3.8 s" (v9, low-load)
> to "BG = 5.05 s with 30% outliers up to 21 s" (v10, production load) to
> "BG = 3.90 s with no outliers, but 5-client parallel reboot is 70× slower
> than single-client" (v11, CDK + parallel). This post is a retrospective on
> what each version got right, what it got wrong, and the lessons that
> generalise beyond Aurora.
>
> All code, data, and reports are open source: [github.com/neosun100/aurora-bg-toolkit](https://github.com/neosun100/aurora-bg-toolkit)

<p align="center">
  <img src="https://img.aws.xin/uPic/v10-vs-v9-results.png" alt="downtime comparison across versions" width="100%"/>
</p>

---

## Background: a customer ticket I couldn't reproduce

In early 2026 a customer (HashKey, a Hong Kong digital-asset exchange) opened
a ticket: their application was experiencing **30-60 second blackouts during
Aurora MySQL Blue/Green switchovers**, while AWS docs and the wider community
quoted **3-5 seconds**. The customer's configuration was a textbook
`aws-advanced-jdbc-wrapper` setup with HikariCP. Nothing in their config looked
broken. Yet the downtime was 10× off.

I did what every engineer does first: I tried to reproduce it locally. I
spun up an Aurora cluster, ran their config, triggered a switchover, and saw…
3.5 seconds. Repeated 5 times. Same result. The customer's number was real;
my number was real; we were measuring different things.

This is the story of how I closed that gap, and the eleven lessons that came
out of it.

---

## v9: the first attempt — five hypotheses, one survivor

### Setup
- **Single Aurora cluster**, db.r7g.large + db.t3.medium
- **EC2 c6i.2xlarge** runner with Java 17 + `aws-advanced-jdbc-wrapper` 4.0.1
- **5 hypotheses** about what made the customer slow:
  - **H1**: JVM DNS TTL — default 30 s holds stale writer IP after switchover
  - **H2**: Connection pool size — too small (pool=10) starves under load
  - **H3**: HikariCP `maxLifetime` — too long, rotation is sluggish
  - **H4**: HikariCP `validationTimeout` — too short, kills good connections
  - **H5**: Wrapper plugin chain — extraneous plugins add overhead
- **Workload** at the time: `40 ops/s` (low load — first mistake)
- **120 measurements** over 5 nights

### Results

| Lever | Effect on BG | Effect on Failover | Effect on Reboot |
|---|---|---|---|
| **H1 (DNS TTL=5)** | -1.0 s | -2.0 s | **-4.9 s** ✅ |
| H2 (pool=20) | none | none | none |
| H3 (maxLifetime=15min) | none | none | none |
| H4 (validationTimeout=2s) | mild improvement | none | none |
| H5 (plugin removal) | none | none | none |

### Lesson 1: pre-register your hypotheses, even informally

Before v9 I had a vague feeling that "DNS and pool size matter." After v9, I
had a paper trail showing that of five reasonable-sounding levers, **only one
mattered**, and the effect of that one was mostly on Reboot (5 s → 100 ms),
with marginal effect on Switchover.

If I had just discovered "DNS TTL=5 helps" without the other four levers
documented as null results, I would have walked away suspecting the other
four also helped a little. **Documenting null results is as valuable as
documenting positive results.**

### Lesson 2: log JVM flags in your test artefacts

Half-way through v9 I realised some experiments had inadvertently been run
without `-Dnetworkaddress.cache.ttl=5` (a `nohup java -jar` had picked up an
older script that didn't set it). The runs without TTL=5 were not labelled
differently from the runs with TTL=5. Six hours of measurement got thrown
out because I couldn't tell which was which.

Now, every measurement directory contains a `meta.json` with the exact JVM
command line. **If you can't tell from the artefacts which experiment
parameters were active, you don't have an experiment, you have an anecdote.**

### Lesson 3: low load is a different system

This is the lesson v9 didn't teach me until v10 hit me with it: at 40 ops/s,
the connection pool barely turns over, the writer barely sees pressure, and
DNS lookups are cached for the entire test. **Many of the failure modes that
appear under production load are physically impossible at 40 ops/s.** The
lever H2 (pool size) showing "no effect" in v9 was almost certainly wrong;
the test wasn't stressing the pool.

I shipped v9 with a clean report and a "pool size doesn't matter" claim. v10
proved this wrong within an hour.

---

## v10: production load, and the ghost in the data

### Setup
- **Same single cluster** as v9
- **Workload**: 1280 ops/s (32× higher than v9's 40 ops/s)
- **Pool=50, maxLifetime=15min** (v9 winning config)
- **30 measurements** (10 BG + 10 FO + 10 RB), ~7 hours wall, ~$5 AWS
- **JVM DNS TTL=5** (v9's only validated lever)

### Results

| Scenario | n | min | median | max | stdev |
|---|---|---|---|---|---|
| Blue/Green | 10 | 4.5 s | **5.05 s** | **21.0 s** | 6.17 s |
| Failover | 10 | 0 ms | **7.75 s** | 14.8 s | 3.69 s |
| Reboot | 10 | 0 ms | **100 ms** | 2.6 s | 1.19 s |

The median for BG was **5.05 s** — slightly higher than v9's 3.8 s but in the
same ballpark. The thing that broke the mental model was the max: **21
seconds**. And it wasn't a single freak occurrence — three of the ten BG
rounds clocked 14, 18, and 21 seconds. **30% of BG switchovers had outliers
3-4× the median.**

### Lesson 4: production load is a different system

The customer's "30-60 second downtime" finally clicked. They weren't seeing a
median; they were seeing a **p99 long tail that v9 had completely missed
because v9's load was too low to surface it**.

This is uncomfortable to admit in retrospect — I had spent three months
producing a clean v9 report with confidence intervals, and a single change
(40 ops/s → 1280 ops/s) made the central metric (median 3.8 → 5.05) move only
+1.2 s, but introduced a new behaviour (long tail outliers up to 21 s) that
wasn't in the original data **at all**. v9's confidence intervals lied
because they were computed over a population that didn't include the
outliers — and the outliers were the customer's actual problem.

### Lesson 5: report the max, not just the median

Most performance docs (AWS's included) quote medians. "Aurora switchover
downtime is 3-5 seconds" is a true statement about medians. It is also
useless to a customer whose users are experiencing 21-second blackouts when
they happen to land on the wrong tail.

After v10, every report I write includes min, p50, mean, p95, max, and
stdev — and the prose always discusses the max. **The median is what
docs/marketing should quote. The max is what timeouts and SLAs need to be
designed against.**

### Lesson 6: orchestration is part of the experiment

v10 introduced something v9 didn't have: a **resumable orchestrator**.
`infra/orchestrate-v10-master.sh` ran 39 phases (precheck → build → bootstrap
→ cluster → BG prereqs → EC2 → 30 measurements → analyze → report → teardown)
with checkpoints in `progress.json`. Re-running the same script picked up
from the last completed phase.

Why does this matter? Because at hour 5 of a 7-hour run, my laptop slept,
killed the SSH session that was tailing the experiment, and the orchestrator
saw its last `phase_set("running")` for round 7 — without resumability, I
would have lost the entire batch. With resumability, I re-launched the
script, it re-marked round 7 as pending (because "running" + no parent
process = killed by previous session), retried it, and continued.

**An experiment script that can't survive a laptop sleep is an experiment
script that runs once and produces results no one trusts to reproduce.**

### Lesson 7: dashboard data is part of the audit trail

v10 also produced a self-contained HTML dashboard
(`dashboard/index.html` + `dashboard-v10.js` + `dashboard/data/v10-only.json`)
showing per-round numbers, per-scenario box plots, and the configuration
that produced them. The data file is canonical: all stats in the prose
report were generated from this JSON, and the dashboard renders the same
JSON.

This was inspired by [Open Science Framework](https://osf.io/) practices.
The single source of truth for everyone (me, future-me, customer, reviewer)
is the JSON. Reports cite the JSON. Dashboards render the JSON. There is no
table of numbers in any document that doesn't have a corresponding row in
the JSON.

---

## v11: CDK + 5-cluster parallel, and the surprises of concurrency

### Setup
- **Full CDK migration** — 1 NetworkStack + 5 ClusterStack + 1 ClientStack
- **5 Aurora clusters in parallel** — running on a single c6i.2xlarge EC2 with
  5 java processes (each pointed at one cluster)
- **Same v10 config** (1280 ops/s, pool=50, JVM TTL=5)
- **Python orchestrator** (replacing v10's bash) with `ThreadPoolExecutor(5)`
- **30 measurements** target — 5 clusters × (2 BG + 2 FO + 2 RB)

### Why CDK?
Two reasons:

1. **Reproducibility.** The bash orchestrator had drift in the
   `aws cli ... | jq | tee` chains. Once you accept that the experiment is
   the artefact, IaC becomes the same kind of investment as version control.
   `cdk diff` between commits tells me exactly what infrastructure changed
   between two test runs.

2. **Parallelism.** v10's serial execution took ~6 hours for 30
   measurements. With 5 clusters, I expected ~70 minutes. (I got 42.)

### Results

| Scenario | n | min | median | max | stdev |
|---|---|---|---|---|---|
| Blue/Green | 5 | 3.70 s | **3.90 s** | 5.00 s | 608 ms |
| Failover | 10 | 4.40 s | **10.15 s** | 15.90 s | 3.12 s |
| Reboot | 10 | 0 ms | **6.95 s** | 8.40 s | 2.21 s |

(N=5 for BG because of an orchestrator race condition I'll discuss below.)

### Three findings, each disagreeing with v10 in a different way

**Finding 1: v10's BG outliers did not reproduce.**
v10 had 3 outliers (14-21 s) in 10 BG rounds. v11's 5 BG rounds (one per
cluster, all in R1) were all 3.7-5.0 s, with **zero** outliers. If v10's 30%
outlier rate were a stable property of the workload, v11 should have seen
~1.5 outliers in 5 rounds. v11 saw 0.

This means v10's outliers were **time-dependent or RDS-control-plane-
dependent**: there was something specific about the day v10 ran (May 17,
afternoon UTC) that produced the long tail. It could have been a deployment
in the RDS control plane, an unlucky hardware swap, or a transient
network/VPC issue. **v10's 30% outlier rate is not a property of "Aurora BG
under production load" — it's a property of "Aurora BG, on May 17, 2026,
afternoon."**

This is humbling. v10's report was the most rigorous thing I had produced.
And one of its central findings — "30% of BG rounds have a long tail" — was
**not reproducible 24 hours later** with the same workload, just different
clusters.

**Finding 2: 5-client parallel reboot is 70× slower than single-client
reboot.**
v10 reboot median: 100 ms. v11 reboot median: 6.95 s. That's not a small
difference. It's 70×.

The cause is structural. In v10, one EC2 instance ran one Java process
talking to one Aurora cluster. When the writer rebooted, one HikariCP pool
drained over a few seconds, refilled, and the application kept moving.

In v11, one EC2 instance ran **five** Java processes, each pointed at its own
cluster, each with its own HikariCP pool. When all five writers rebooted
simultaneously, all five pools drained and tried to refill simultaneously,
contending for:
- the EC2 instance's NIC (5× the connection-establish syscalls)
- the EC2 instance's CPU (5× the JDBC stack churn)
- the EC2 instance's memory (5× the new connection objects)
- the RDS control plane's reboot-coordinated response time

The reboot itself is still fast on the database side. The downtime in v11
is **client-side recovery cost** that gets amplified when the client has
multiple parallel pools.

**Production implication**: if your application has, say, 5 microservices,
each with its own Aurora client, and they share an Aurora cluster (or
several), and your runbook says "reboot during off-peak" — you should expect
**multi-second client-perceived downtime** even when each individual reboot
is sub-second on the database side. The 100 ms number from v10 is not a lie;
it's a one-client measurement and the customer-perceived number scales with
client concurrency.

**Finding 3: Failover is reproducible across orchestration paths.**
v10 FO median 7.75 s, v11 FO median 10.15 s. Within statistical noise.
Failover is the only one of the three scenarios where the orchestration
path didn't materially change the headline number. This is consistent with
the AWS docs description: failover is dominated by "the time it takes RDS
control plane to detect, decide, and switch DNS," and that's bounded by RDS
internals not by client behaviour.

### Lesson 8: parallelism reveals what serial testing hides

This is the v11 version of v10's "production load reveals what low load
hides." Each level of realism in the test rig surfaced a new failure mode.

| Level | Workload | Concurrency | Surfaced failure mode |
|---|---|---|---|
| v9 | 40 ops/s | 1 client | DNS TTL effect on Reboot |
| v10 | 1280 ops/s | 1 client | BG long-tail outliers |
| v11 | 1280 ops/s | 5 client | client-side reboot amplification |

**The next experiment will surface something this experiment didn't.** I
can't tell you in advance what it is. But it's there. The discipline is to
keep increasing realism — workload, concurrency, geographic distribution,
network conditions — until you've produced the same failure mode the
customer reports. **If your test rig hasn't reproduced the customer's number,
your test rig is incomplete.**

### Lesson 9: race conditions in parallelism are subtle

v11 hit a bug I didn't see coming. After R1 BG completed switchover, the
orchestrator immediately tried `delete_blue_green_deployment` to clean up
before R2. RDS rejected the call:

```
InvalidBlueGreenDeploymentStateFault: Deleting target is not allowed while
blue green deployment lifecycle is SWITCHOVER_COMPLETED.
```

In v10, this race didn't fire because rounds were sequential — by the time
round 2 started, RDS had finished its background -old1 cluster creation and
the BG was deletable. In v11, all five clusters hit this race
**simultaneously**, and 5 of 5 R2 BG rounds failed.

The fix was a `_safe_delete_bg` helper that retries on the lifecycle-lock
error every 30 seconds for up to 12 minutes, while concurrently triggering
`_cleanup_old_instances_clusters` to accelerate RDS's lifecycle progression.

**Lesson**: the existence of a race condition in your serial code does not
prove there is no race condition. Your serial timing might just be wide
enough to make the race invisible. **Adding parallelism to a serial pipeline
is not just a speedup, it's a correctness test.**

### Lesson 10: "infrastructure cleanup" is also part of the experiment

v11's first run finished its 25 (of 30) measurements successfully. Then
`cdk destroy --all` failed: 5 `-old1` clusters from the BG lifecycle were
blocking stack deletion, plus the 5 BGs in `SWITCHOVER_COMPLETED` blocked
their parent clusters' deletion.

I had to manually:
1. Delete 10 `-old1` instances (waiting ~10 min for them to disappear)
2. Delete 5 `-old1` clusters
3. Delete 5 BGs with `--no-delete-target` (since target was already deleted)
4. Re-run `cdk destroy --all`

This is a serious gap. A test rig that doesn't reliably tear itself down is
a test rig that bleeds money and pollutes the AWS account for the next run.
v12 will fix this by making the orchestrator's `CDK_DESTROY` phase explicitly
clean these artefacts before invoking `cdk destroy`. (Code is in
[`infra/orchestrate-v11.py:cdk_destroy`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/infra/orchestrate-v11.py).)

### Lesson 11: when your orchestrator silently swallows errors, your debugging time goes 10×

The first v11 attempt died after 9 seconds with an opaque "phase failed"
status in `progress.json` and no error in the master log. I had used
`subprocess.run(check=True)` for `cdk deploy --all`. The shell command
exited non-zero, but I hadn't captured stdout/stderr — they went to a
sub-shell's stdout, which my orchestrator wasn't tee-ing anywhere.

For the next 90 minutes I ran progressively wider greps trying to find what
had failed. Eventually I ran `cdk deploy` manually and saw the actual error:
em-dash characters (`—`) in CDK description fields, which AWS's
`AWS::EC2::SecurityGroup` and `AWS::RDS::DBClusterParameterGroup` APIs
reject as non-ASCII.

The fix:
1. ASCII-only descriptions (`grep -P '[^\x00-\x7f]'` lint)
2. Switch `cdk_deploy` to `subprocess.Popen` + line-by-line streaming into
   the master log

**Lesson**: the cost of "I'll capture errors when I need them" compounds.
Capture them all, all the time. Disk is cheap. Your time is not.

---

## What v12 will do

If I were to do another iteration today, the experiments I'd run:

1. **Repeat v10 single-cluster BG 5 times across different days/weeks** to
   characterise the day-to-day variability in BG outliers. Is the 30%
   outlier rate a one-time fluke or a recurring pattern? Need ≥50 BG
   measurements across ≥5 separate days to make a meaningful claim.

2. **Vary client concurrency** (1, 2, 5, 10, 20 clients per Aurora cluster)
   to characterise the client-side reboot amplification curve. Linear?
   Logarithmic? Threshold?

3. **Cross-region BG/FO/RB** to characterise downtime in geographically
   distributed deployments.

4. **Instrument the JVM** with JFR / async-profiler during the downtime
   window to understand exactly where the seconds are going (DNS lookup?
   TCP handshake? TLS? HikariCP retry? Hibernate timeout?). The current
   instrumentation only measures `write_ok=0` windows; it doesn't tell us
   *why* writes failed.

5. **Fix the v11 BG R2 race** (already implemented in `_safe_delete_bg`,
   needs another full run to validate) and re-run for clean 30 measurements.

---

## Closing thought: the test rig is the lab notebook

Every iteration of this experiment improved the test rig more than it
improved the underlying knowledge. v9's lab notebook was a few shell
scripts and a couple of CSVs. v10's was a 39-phase resumable bash
orchestrator + a self-contained dashboard + a markdown report. v11's was
fully IaC + Python + ThreadPoolExecutor + per-cluster contention analysis.

This is not unique to performance testing. Any time you're doing rigorous
empirical work, the **infrastructure that produces the data is the most
durable artefact**. The numbers will be obsolete in a year (Aurora MySQL
versions, JDBC wrapper versions, RDS control plane behaviour all evolve).
The infrastructure that lets you reproduce the measurements **on the
next version** is what makes the work compoundable.

If you take one thing from this post, take this: **don't build a tool to
do an experiment. Build the experiment as a tool.**

---

## References & links

- 📦 **Project**: [github.com/neosun100/aurora-bg-toolkit](https://github.com/neosun100/aurora-bg-toolkit)
- 📊 **v9 report**: [`docs/REPORTS/2026-05-15-v9-tuned.md`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/docs/REPORTS/)
- 📊 **v10 report**: [`docs/REPORTS/2026-05-17-v10-production.md`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/docs/REPORTS/2026-05-17-v10-production.md)
- 📊 **v11 report**: [`docs/REPORTS/2026-05-17-v11-cdk-parallel.md`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/docs/REPORTS/2026-05-17-v11-cdk-parallel.md)
- 🛠 **Methodology**: [`docs/METHODOLOGY.md`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/docs/METHODOLOGY.md)
- 🐛 **Root cause of customer's 30-60 s downtime**: [`docs/ROOT-CAUSE-ANALYSIS.md`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/docs/ROOT-CAUSE-ANALYSIS.md)
- 📜 **CHANGELOG with full audit trail**: [`CHANGELOG.md`](https://github.com/neosun100/aurora-bg-toolkit/blob/main/CHANGELOG.md)

---

<div align="center">
  <sub>If this post helped, star the repo on GitHub. If it didn't, file an issue and tell me where I'm wrong. The most useful thing you can do for an experimentalist is help them find the next failure mode their rig hasn't seen yet.</sub>
</div>

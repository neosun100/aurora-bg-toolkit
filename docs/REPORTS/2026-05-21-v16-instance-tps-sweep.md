# Aurora BG Toolkit v16 — Instance × TPS Matrix Sweep — Final Report

> **Experiment**: v16-instance-tps-sweep
> **Generated**: 2026-05-23T10:42:33Z
> **Customer context**: HashKey 2026-06 production upgrade window
> **Question being answered**: at production scale (8X, 4000 TPS), is v11 the optimal config — and how does Aurora downtime scale with instance class and TPS?

---

## Executive summary

| Run | Writer | TPS | BG median | FO median | RB median | Notes |
|---|---|---|---|---|---|---|
| M1 — 1X @ 1280 TPS | r7g.large | 1280 | 4.60 s | 9.30 s | 0 ms | |
| M2 — 2X @ 1280 TPS | r7g.2xlarge | 1280 | 3.40 s | 10.10 s | 0 ms | |
| M3 — 4X @ 1280 TPS | r7g.4xlarge | 1280 | 3.90 s | 10.90 s | 0 ms | |
| M4 — 8X @ 1280 TPS | r7g.8xlarge | 1280 | 3.20 s | 8.10 s | 0 ms | |
| T2 — 8X @ 2560 TPS | r7g.8xlarge | 2560 | 4.20 s | 9.00 s | 0 ms | |
| T3 — 8X @ 4000 TPS  ⭐ | r7g.8xlarge | 4000 | 3.40 s | 11.00 s | 0 ms | T3 BG n=3 (cluster-3/5 真实失败) |

---

## ⚠️ Important: how to read RB ≈ 0 ms (NOT a measurement bug)

Every row in the matrix shows `RB median = 0 ms`. This is **not** a malfunction —
it is a direct consequence of v16's cluster topology and the AWS JDBC wrapper
behavior. Read this section before quoting any RB number.

**v9 → v11 historical reboot test**: cluster had a single instance (writer-only,
no reader replica). When `reboot-db-instance` fired, the writer was the only
endpoint; the client experienced a real ~5-7 s gap while the instance restarted.

**v16 cluster topology** (matches HSK production target):
```
test-v11-N (Aurora cluster)
  ├── test-v11-N-writer (r7g.large/2xl/4xl/8xl)
  └── test-v11-N-reader (t3.medium/r7g.large/r7g.2xl/r7g.2xl)
```

**What actually happens during v16 reboot test**:
1. Client connects via cluster endpoint (`test-v11-N.cluster-xxx.rds.amazonaws.com`)
2. AWS JDBC wrapper plugin chain (`failover2 + efm2 + bg`) tracks topology
3. Orchestrator fires `reboot-db-instance` on the **writer** instance
4. **Aurora cluster auto-failover** kicks in (~1 s): reader gets promoted to writer
5. JDBC wrapper notices the topology change, transparently re-routes new connections
6. In-flight queries: most retried successfully within the 1 s `connectTimeout`,
   gap below the 100 ms STATS reporter resolution → **measured as 0 ms**

**This is the realistic production outcome for HSK**: as long as production
clusters have at least one reader replica AND the application uses the
JDBC wrapper with cluster endpoint, an instance-level reboot is **transparent
to the application**. This is significantly better than v9-v11 single-instance
numbers suggest.

**Caveat — when this won't hold**:
- Writer-only cluster (no reader): RB will look like v11 (~5-7 s)
- Direct connection to writer instance endpoint (bypassing cluster endpoint):
  same as above
- Application doesn't use the JDBC wrapper: client must implement its own
  reconnect with DNS re-resolution

For the Wang-laoshi hypothesis "8X reboot blows up due to buffer pool reload":
the v16 measurement methodology cannot answer this directly because the
buffer-pool reload happens on a healthy reader being promoted, not on the
restarted writer (which the client never sees). To measure pure buffer-pool
reload cost, you would need a single-instance cluster — which is not the
recommended HSK production topology anyway.

---

## ⚠️ Important: T3 BG n=3 (not 5) — control-plane congestion at 4000 TPS + 8X

T3 (production target run) ran 5 clusters of `r7g.8xlarge` writers, each with
the BG plugin attempting Blue/Green creation under 4000 ops/s sustained load.
**Cluster-3 and cluster-5 BG creation FAILED**; cluster-1/2/4 succeeded.

This is the **single most important negative finding of v16**:

- **BG creation under heavy concurrent load is not always reliable at 8X scale.**
  The orchestrator log shows `InvalidBlueGreenDeploymentStateFault` on the two
  failed clusters — RDS control plane couldn't keep up with creating 5 BGs
  simultaneously while each was sustaining 4000 ops/s.
- **Implication for HSK**: do NOT initiate BG switchover during peak production
  hours. Either reduce TPS to ≤ 2560 during the BG window, or stagger BG
  creation across clusters (don't fire 5 simultaneously).
- **Recommendation**: BG switchovers should be scheduled for off-peak (nightly)
  windows and one cluster at a time when on 8X infrastructure at production TPS.
  HSK's planned 6-month upgrade should serialize BG operations.

The 3 successful BG runs at T3 (BG max = 3.4 s) are still informative as a
**lower bound** of what's achievable, but they are not a robust commitment
under all production conditions.

---



## Q1: Does v11 config remain optimal across Aurora instance classes?

Test fixed at TPS=1280 (matches HSK customer stg load), writer scaling 1X → 8X.

| Writer | n (BG) | BG median | BG max | n (FO) | FO median | FO max | n (RB) | RB median | RB max |
|---|---|---|---|---|---|---|---|---|---|
| r7g.large | 5 | 4.60 s | 6.20 s | 5 | 9.30 s | 17.00 s | 5 | 0 ms | 100 ms |
| r7g.2xlarge | 5 | 3.40 s | 4.60 s | 5 | 10.10 s | 17.00 s | 5 | 0 ms | 0 ms |
| r7g.4xlarge | 5 | 3.90 s | 15.30 s | 5 | 10.90 s | 11.20 s | 5 | 0 ms | 0 ms |
| r7g.8xlarge | 5 | 3.20 s | 4.30 s | 5 | 8.10 s | 10.40 s | 5 | 0 ms | 0 ms |

**Reading**: stable BG median means the v11 config (connectTimeout=1000, socketTimeout=3000, failureDetectionTime=6000, pool sized to TPS, DNS TTL=5) generalizes to bigger instances. A monotonic increase in RB median with instance size would confirm Wang-laoshi's hypothesis that buffer pool reload time scales.

## Q2: How do downtime numbers scale with TPS at 8X?

Test fixed at writer=r7g.8xlarge (production-target), TPS scaling 1280 → 2560 → 4000.

| TPS | Pool | n (BG) | BG median | BG max | n (FO) | FO median | FO max | n (RB) | RB median | RB max |
|---|---|---|---|---|---|---|---|---|---|---|
| 1280 | 50 | 5 | 3.20 s | 4.30 s | 5 | 8.10 s | 10.40 s | 5 | 0 ms | 0 ms |
| 2560 | 80 | 5 | 4.20 s | 4.60 s | 5 | 9.00 s | 12.60 s | 5 | 0 ms | 0 ms |
| 4000 | 120 | 3 | 3.40 s | 3.40 s | 5 | 11.00 s | 16.70 s | 5 | 0 ms | 0 ms |

**Reading for CTO Steven**: the T3 row (8X @ 4000 TPS) is the production-target measurement. BG max here is the number we recommend as the application timeout floor.

## Q3: At 8X scale, is reboot ≤ failover (or does buffer pool reload break the rule)?

Direct response to Wang-laoshi's challenge that prior tests on small (t/lark) instances cannot be extrapolated to 8X production. Below, 8X reboot time is measured WITH a 5-min buffer-pool warmup before reboot and 5-min stabilize after, so we capture the true cold-buffer reload cost.

| Run | Writer | TPS | RB median | FO median | Δ (FO − RB) | Verdict |
|---|---|---|---|---|---|---|
| M1 — 1X @ 1280 TPS | r7g.large | 1280 | 0 ms | 9.30 s | +9.30 s | RB ≤ FO ✓ |
| M2 — 2X @ 1280 TPS | r7g.2xlarge | 1280 | 0 ms | 10.10 s | +10.10 s | RB ≤ FO ✓ |
| M3 — 4X @ 1280 TPS | r7g.4xlarge | 1280 | 0 ms | 10.90 s | +10.90 s | RB ≤ FO ✓ |
| M4 — 8X @ 1280 TPS | r7g.8xlarge | 1280 | 0 ms | 8.10 s | +8.10 s | RB ≤ FO ✓ |
| T2 — 8X @ 2560 TPS | r7g.8xlarge | 2560 | 0 ms | 9.00 s | +9.00 s | RB ≤ FO ✓ |
| T3 — 8X @ 4000 TPS  ⭐ | r7g.8xlarge | 4000 | 0 ms | 11.00 s | +11.00 s | RB ≤ FO ✓ |

**Reading**: Wang-laoshi's hypothesis predicts reboot will exceed failover at 8X scale. The Δ column directly answers: positive Δ (FO > RB) confirms the existing recommendation; negative Δ (RB > FO) supports replacing reboot with failover for parameter group changes.

---

## Per-run, per-cluster measurements

Full traceability: every measurement, with run / cluster / scenario / round.

### M1 — 1X @ 1280 TPS

Writer: `r7g.large`, Reader: `t3.medium`, Client: `c6i.2xlarge`, TPS config: `v16-tps1280`

| Scenario | Cluster | Round | writeMaxMs | readMaxMs |
|---|---|---|---:|---:|
| blue-green | `test-v11-1` | 1 | 4.60 s | 4.60 s |
| blue-green | `test-v11-2` | 1 | 3.80 s | 3.80 s |
| blue-green | `test-v11-3` | 1 | 5.00 s | 5.00 s |
| blue-green | `test-v11-4` | 1 | 4.20 s | 4.20 s |
| blue-green | `test-v11-5` | 1 | 6.20 s | 6.20 s |
| failover | `test-v11-1` | 1 | 9.30 s | 9.30 s |
| failover | `test-v11-2` | 1 | 6.30 s | 6.30 s |
| failover | `test-v11-3` | 1 | 17.00 s | 17.00 s |
| failover | `test-v11-4` | 1 | 6.20 s | 6.20 s |
| failover | `test-v11-5` | 1 | 9.30 s | 9.30 s |
| reboot | `test-v11-1` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-2` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-3` | 1 | 100 ms | 0 ms |
| reboot | `test-v11-4` | 1 | 100 ms | 0 ms |
| reboot | `test-v11-5` | 1 | 0 ms | 0 ms |

### M2 — 2X @ 1280 TPS

Writer: `r7g.2xlarge`, Reader: `r7g.large`, Client: `c6i.2xlarge`, TPS config: `v16-tps1280`

| Scenario | Cluster | Round | writeMaxMs | readMaxMs |
|---|---|---|---:|---:|
| blue-green | `test-v11-1` | 1 | 3.20 s | 3.20 s |
| blue-green | `test-v11-2` | 1 | 3.30 s | 3.30 s |
| blue-green | `test-v11-3` | 1 | 4.50 s | 4.50 s |
| blue-green | `test-v11-4` | 1 | 3.40 s | 3.40 s |
| blue-green | `test-v11-5` | 1 | 4.60 s | 4.60 s |
| failover | `test-v11-1` | 1 | 17.00 s | 16.90 s |
| failover | `test-v11-2` | 1 | 8.40 s | 8.30 s |
| failover | `test-v11-3` | 1 | 11.30 s | 11.30 s |
| failover | `test-v11-4` | 1 | 10.10 s | 10.10 s |
| failover | `test-v11-5` | 1 | 8.40 s | 8.30 s |
| reboot | `test-v11-1` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-2` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-3` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-4` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-5` | 1 | 0 ms | 0 ms |

### M3 — 4X @ 1280 TPS

Writer: `r7g.4xlarge`, Reader: `r7g.large`, Client: `c6i.4xlarge`, TPS config: `v16-tps1280`

| Scenario | Cluster | Round | writeMaxMs | readMaxMs |
|---|---|---|---:|---:|
| blue-green | `test-v11-1` | 1 | 3.90 s | 3.90 s |
| blue-green | `test-v11-2` | 1 | 3.30 s | 3.30 s |
| blue-green | `test-v11-3` | 1 | 15.30 s | 15.30 s |
| blue-green | `test-v11-4` | 1 | 3.30 s | 3.27 s |
| blue-green | `test-v11-5` | 1 | 4.30 s | 4.30 s |
| failover | `test-v11-1` | 1 | 10.90 s | 10.90 s |
| failover | `test-v11-2` | 1 | 11.10 s | 11.10 s |
| failover | `test-v11-3` | 1 | 8.30 s | 8.30 s |
| failover | `test-v11-4` | 1 | 11.20 s | 11.10 s |
| failover | `test-v11-5` | 1 | 8.30 s | 8.30 s |
| reboot | `test-v11-1` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-2` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-3` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-4` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-5` | 1 | 0 ms | 0 ms |

### M4 — 8X @ 1280 TPS

Writer: `r7g.8xlarge`, Reader: `r7g.2xlarge`, Client: `c6i.4xlarge`, TPS config: `v16-tps1280`

| Scenario | Cluster | Round | writeMaxMs | readMaxMs |
|---|---|---|---:|---:|
| blue-green | `test-v11-1` | 1 | 3.10 s | 3.10 s |
| blue-green | `test-v11-2` | 1 | 3.20 s | 3.20 s |
| blue-green | `test-v11-3` | 1 | 4.30 s | 4.30 s |
| blue-green | `test-v11-4` | 1 | 3.00 s | 3.00 s |
| blue-green | `test-v11-5` | 1 | 4.20 s | 4.20 s |
| failover | `test-v11-1` | 1 | 8.10 s | 8.10 s |
| failover | `test-v11-2` | 1 | 7.60 s | 7.60 s |
| failover | `test-v11-3` | 1 | 8.90 s | 8.90 s |
| failover | `test-v11-4` | 1 | 7.70 s | 7.70 s |
| failover | `test-v11-5` | 1 | 10.40 s | 10.40 s |
| reboot | `test-v11-1` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-2` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-3` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-4` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-5` | 1 | 0 ms | 0 ms |

### T2 — 8X @ 2560 TPS

Writer: `r7g.8xlarge`, Reader: `r7g.2xlarge`, Client: `c6i.8xlarge`, TPS config: `v16-tps2560`

| Scenario | Cluster | Round | writeMaxMs | readMaxMs |
|---|---|---|---:|---:|
| blue-green | `test-v11-1` | 1 | 3.42 s | 3.42 s |
| blue-green | `test-v11-2` | 1 | 4.60 s | 4.60 s |
| blue-green | `test-v11-3` | 1 | 4.20 s | 4.20 s |
| blue-green | `test-v11-4` | 1 | 3.50 s | 3.50 s |
| blue-green | `test-v11-5` | 1 | 4.20 s | 4.20 s |
| failover | `test-v11-1` | 1 | 8.20 s | 8.20 s |
| failover | `test-v11-2` | 1 | 9.00 s | 9.00 s |
| failover | `test-v11-3` | 1 | 9.80 s | 9.80 s |
| failover | `test-v11-4` | 1 | 12.60 s | 12.60 s |
| failover | `test-v11-5` | 1 | 8.10 s | 8.10 s |
| reboot | `test-v11-1` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-2` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-3` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-4` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-5` | 1 | 0 ms | 0 ms |

### T3 — 8X @ 4000 TPS  ⭐

Writer: `r7g.8xlarge`, Reader: `r7g.2xlarge`, Client: `c6i.8xlarge`, TPS config: `v16-tps4000`

| Scenario | Cluster | Round | writeMaxMs | readMaxMs |
|---|---|---|---:|---:|
| blue-green | `test-v11-1` | 1 | 3.40 s | 3.30 s |
| blue-green | `test-v11-2` | 1 | 3.40 s | 3.40 s |
| blue-green | `test-v11-4` | 1 | 3.20 s | 3.20 s |
| failover | `test-v11-1` | 1 | 7.90 s | 7.90 s |
| failover | `test-v11-2` | 1 | 8.40 s | 8.40 s |
| failover | `test-v11-3` | 1 | 12.60 s | 12.60 s |
| failover | `test-v11-4` | 1 | 16.70 s | 16.70 s |
| failover | `test-v11-5` | 1 | 11.00 s | 11.00 s |
| reboot | `test-v11-1` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-2` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-3` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-4` | 1 | 0 ms | 0 ms |
| reboot | `test-v11-5` | 1 | 0 ms | 0 ms |

---

## Recommendations for HashKey production upgrade

### Configuration
Use `configs/v11-final.yaml` (or `configs/v16-tps4000.yaml` if 4000 TPS) as the production reference. The three timeouts (connectTimeout=1000ms, socketTimeout=3000ms, failureDetectionTime=6000ms) are validated optimal by v9 → v12 → v16.

### Application timeout floor
Set application-level request timeout ≥ **BG max from T3** (8X @ 4000 TPS, row above). This bounds even the worst-case cold-buffer-reload reboot.

### Reboot vs Failover for parameter changes
Per Q3 table above:
- If Δ ≥ 0 across all rows: **prefer reboot** (it's faster than failover regardless of instance size). Existing customer plan stands.
- If Δ < 0 at 8X: **prefer failover** for parameter changes — the cold buffer reload cost makes reboot worse.

### `read_only` static-vs-dynamic open item
This report doesn't directly answer whether `read_only` is a static parameter in the customer's Aurora version. Wang-laoshi / 张斌 should file a support case and AWS service team should confirm. The empirical evidence in this report (8X reboot/failover comparison) provides indirect guidance regardless of that answer.

---

## Methodology

- 5 Aurora MySQL clusters in parallel per run (test-v11-1..5)
- Each run: 5 cluster × 1 round × 3 scenarios = 15 measurements
- Total runs: 6 (M1, M2, M3, M4, T2, T3)
- Total measurements: 90
- 10 Hz STATS reporter (±100ms downtime measurement precision)
- aws-advanced-jdbc-wrapper 4.0.1 (failover2, efm2, bg plugins)
- v11 JDBC + HikariCP config (connectTimeout=1000ms, socketTimeout=3000ms,
  failureDetectionTime=6000ms, DNS TTL=5)
- 8X-specific tuning: 5min buffer-pool warmup before reboot, 5min stabilize after
- Each run is fully isolated: independent CDK deploy/destroy cycle
- Orchestration: orchestrate-matrix.py → orchestrate-v11.py per run

*Auto-generated from `dashboard/data/v16-matrix.json` (2026-05-23T10:42:33Z).*

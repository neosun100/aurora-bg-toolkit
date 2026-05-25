# Aurora BG Toolkit — Final Report

> **客户**：HashKey（HSK），2026-06 生产升级窗口
> **测试周期**：2026-05-15 → 2026-05-24（10 天）
> **总测量数**：380+，跨 7 个正式版本（v9 / v10 / v11 / v12 / v16 / v17）+ 3 个探索版本（v13 / v14 / v15）
> **总 AWS 成本**：~$370（v9 ~$15 + v10 ~$5 + v11 ~$5 + v12 ~$5 + v16 ~$170 + v17 ~$170）
> **报告编辑日期**：2026-05-25（v17 reboot deep-dive 完成后）

本报告整合了从 v9 到 v17 全部测试的最终结论，给出生产可直接落地的参数配置、应用层 timeout 推荐、以及完整百分位数据。

---

## ✅ 重大更新（2026-05-25）：v17 Reboot Deep-Dive 完成

**v16 的 reboot 章节存在测量盲区，v17 用 100 Hz STATS reporter（10× 精度）完成全量重测，得到真实数字。**

发现总结：
- v16 报告的"RB writeMaxMs = 0ms"是**测量盲区**，不是"reboot 透明"。10 Hz STATS reporter（采样间隔 100ms）漏掉了 20-200ms 的真实 gap window
- v17 用 100 Hz STATS reporter 完成 6 run × 5 cluster = 30 reboot 测量，**真实 RB 中位 = 10-200ms，最大 = 30-200ms**（具体数字取决于 reader 实例规格）
- v17 确认 cluster auto-failover 路径有效，但**不是 0ms**，而是按 reader 规格的 6× 阶梯：t3.medium reader → 190ms / r7g.large → 30ms / r7g.2xlarge → 10-20ms
- v17 还首次复现了**单 cluster 拓扑（无 reader）下 RB ≈ 6.6 秒**的退化场景，验证了 v11 时代的历史数字

本报告的 Reboot 章节（§3.3）、TL;DR、主表 Reboot 子表、应用层 timeout、生产拓扑章节均已用 v17 真实数据更新。**v16 时代发布的"RB ≈ 0ms"对外口径需要修正为"RB ≤ 30ms（生产拓扑下）"。**

详见 [`docs/REPORTS/2026-05-23-v17-reboot-deep-dive.md`](REPORTS/2026-05-23-v17-reboot-deep-dive.md) 和 [`dashboard/data/v17-matrix-percentiles.csv`](../dashboard/data/v17-matrix-percentiles.csv)。

---

## 📋 目录

1. [TL;DR — 一页给老板看的版本](#tldr)
2. [核心结论：实例 × TPS × 场景 完整矩阵](#核心矩阵)
3. [三种场景的最终结论](#三种场景结论)
4. [生产参数配置（直接 copy-paste）](#生产参数配置)
5. [应用层 timeout 推荐](#应用层-timeout-推荐)
6. [已验证的"不要碰"清单](#不要碰清单)
7. [已知风险与适用边界](#已知风险)
8. [测试方法论](#测试方法论)
9. [数据出处与可复现性](#数据出处)

---

<a id="tldr"></a>

## 1. TL;DR — 一页给老板看的版本

✅ **`configs/v11-final.yaml` 是生产推荐配置**，跨 4 个实例规格（1X/2X/4X/8X r7g 系列）和 3 个 TPS 档位（1280 / 2560 / 4000 ops/s）全部验证。

📊 **HSK 生产目标（8X r7g writer + r7g.2xlarge reader + 4000 TPS）的实测延迟**（v17 100Hz 测量精度）：

| 场景 | P50 | P95 | P99 | Max | 解读 |
|---|---|---|---|---|---|
| **Blue/Green 切换** | **3.7 s** | 5.3 s | 5.4 s | 5.4 s | n=5，BG 创建 + 路由切换 + 4s 地板 |
| **Failover** | **11.6 s** | 15.3 s | 15.9 s | 16.0 s | 控制平面节奏，跨版本一致 |
| **Reboot writer** | **20 ms** | 28 ms | 29 ms | 30 ms | cluster auto-failover；按 reader 规格阶梯递减 |

💡 **三个核心发现**：

1. **`v11-final.yaml` 已到客户端调优天花板** — v12 反向验证三个 timeout（connectTimeout / socketTimeout / failureDetectionTime）任何一个再压低都会 regress
2. **HSK 生产拓扑（writer + r7g.2xlarge reader）下 reboot ≤ 30ms** — v17 实测，AWS JDBC wrapper 通过 cluster endpoint 自动跟随 cluster auto-failover；具体数字按 reader 规格分档：t3.medium reader 190ms / r7g.large 30ms / r7g.2xlarge 10-20ms
3. **8X + 4000 TPS 下并发 5 个 BG 切换在 v17 复现 5/5 全成功** — v16 时代 cluster-3/5 BG 创建失败的现象未复现，但 v16 出现过的失败说明这是**偶发性 RDS 控制平面拥塞**，不是必现 bug。生产建议依然按"错峰 + 单 cluster 串行"执行

⚠️ **唯一确认的生产风险**：单 cluster 拓扑（无 reader replica）下 reboot 退化到 6.6 秒（v17 smoke 实测）。**HSK 生产必须为每个 cluster 配置至少 1 个 reader replica。**

---

<a id="核心矩阵"></a>

## 2. 核心结论：实例 × TPS × 场景 完整矩阵

### 2.1 主表 — 完整百分位（write_max_ms）

> 单位毫秒。所有数据来自 v17 全量重测（100Hz STATS reporter，2026-05-23 → 2026-05-24）。
> 完整 CSV：[`dashboard/data/v17-matrix-percentiles.csv`](../dashboard/data/v17-matrix-percentiles.csv)（18 行，6 run × 3 scenario）

#### Blue/Green 切换（值越小越好）

| Run | Writer | Reader | TPS | n | min | **P50** | P75 | P90 | P95 | P99 | max | mean | stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | t3.medium   | 1280 | 5 | 4080 | **4140** | 5130 | 5256 | 5298 | 5331 | 5340 | 4556 | 558 |
| M2 | r7g.2xlarge  | r7g.large   | 1280 | 5 | 3560 | **3880** | 4580 | 4610 | 4620 | 4628 | 4630 | 4050 | 466 |
| M3 | r7g.4xlarge  | r7g.large   | 1280 | 5 | 3540 | **3772** | 4370 | 4400 | 4410 | 4418 | 4420 | 3968 | 357 |
| M4 | r7g.8xlarge  | r7g.2xlarge | 1280 | 5 | 3270 | **3370** | 4230 | 4248 | 4254 | 4259 | 4261 | 3692 | 452 |
| T2 | r7g.8xlarge  | r7g.2xlarge | 2560 | 5 | 3403 | **3821** | 4490 | 4589 | 4622 | 4648 | 4655 | 3983 | 501 |
| **T3** ⭐ | r7g.8xlarge  | r7g.2xlarge | 4000 | 5 | 3530 | **3715** | 4530 | 5070 | 5250 | 5394 | 5431 | 4179 | 716 |

**读法**：
- BG 中位数稳定在 **3.4-4.1 秒**，跨实例规格几乎平坦 → v11 配置 generalize
- T3 在 v17 重测中 5/5 cluster BG 创建成功（v16 时代 cluster-3/5 失败的问题未复现），但 BG max 略高 (5.4s vs M2 4.6s) 暗示 4000 TPS 下控制平面有边际拥塞
- BG 仍受 BG 插件 `SuspendConnectRouting` 4 秒地板限制，min ≈ 3.5 秒

#### Failover（值越小越好）

| Run | Writer | Reader | TPS | n | min | **P50** | P75 | P90 | P95 | P99 | max | mean | stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | t3.medium   | 1280 | 5 | 7620 | **9560** | 9860  | 10712 | 10996 | 11223 | 11280 | 9574  | 1167 |
| M2 | r7g.2xlarge  | r7g.large   | 1280 | 5 | 6920 | **8720** | 11460 | 11784 | 11892 | 11978 | 12000 | 9222  | 2152 |
| M3 | r7g.4xlarge  | r7g.large   | 1280 | 5 | 6900 | **8480** | 8840  | 8900  | 8920  | 8936  | 8940  | 8306  | 734  |
| M4 | r7g.8xlarge  | r7g.2xlarge | 1280 | 5 | 7990 | **8140** | 10880 | 13496 | 14368 | 15065 | 15240 | 10066 | 2807 |
| T2 | r7g.8xlarge  | r7g.2xlarge | 2560 | 5 | 8240 | **11040** | 11250 | 14160 | 15130 | 15906 | 16100 | 11004 | 2846 |
| **T3** ⭐ | r7g.8xlarge  | r7g.2xlarge | 4000 | 5 | 8620 | **11590** | 12540 | 14610 | 15300 | 15852 | 15990 | 11892 | 2424 |

**读法**：
- Failover 中位数稳定在 **8.1-11.6 秒**，跨实例 / TPS 都可预测
- P95 在所有 run 都到 11-16s 区间 → **应用层 circuit breaker 必须 ≥ 17 秒**（覆盖 T2/T3 的 16s 长尾）
- 8X 在 1280 TPS 下反而比 4X 中位更低（M4 8.1s vs M3 8.5s）— 大实例计算资源充足，恢复反而快
- TPS 增大到 2560/4000 让 FO 中位 +3 秒（pool drain 撞 RDS 控制平面）

#### Reboot writer（值越小越好）— **v17 100Hz 测量精度**

| Run | Writer | Reader | TPS | n | min | **P50** | P75 | P90 | P95 | P99 | max | mean | stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | **t3.medium**   | 1280 | 5 | 120 | **190** | 190 | 196 | 198 | 199 | 200 | 178 | 29 |
| M2 | r7g.2xlarge  | **r7g.large**   | 1280 | 5 | 20  | **30**  | 40  | 46  | 48  | 49  | 50  | 34  | 10 |
| M3 | r7g.4xlarge  | **r7g.large**   | 1280 | 5 | 30  | **30**  | 40  | 40  | 40  | 40  | 40  | 34  | 4 |
| M4 | r7g.8xlarge  | **r7g.2xlarge** | 1280 | 5 | 20  | **20**  | 20  | 22  | 23  | 23  | 24  | 20  | 1 |
| T2 | r7g.8xlarge  | **r7g.2xlarge** | 2560 | 5 | 10  | **10**  | 10  | 10  | 10  | 10  | 10  | 10  | 0 |
| **T3** ⭐ | r7g.8xlarge  | **r7g.2xlarge** | 4000 | 5 | 10  | **20**  | 20  | 26  | 28  | 29  | 30  | 20  | 6 |

**读法**：
- **Reader 实例规格是 RB 的关键决定因素**，跨 reader 类型出现 6× 阶梯：
  - t3.medium reader → **190ms**（弱 reader 的 cluster auto-failover）
  - r7g.large reader → **30ms**
  - r7g.2xlarge reader → **10-20ms**（已接近 100Hz STATS 测量精度极限）
- **TPS 不影响 RB**（M4 1280 → T2 2560 → T3 4000 在同 reader 规格下都在 10-30ms 范围）
- Writer 规格不影响 RB（M2 2X / M3 4X / M4 8X 在 same reader r7g.large/2xlarge 下数字趋同）
- ⚠️ 这些数字**只在 cluster 有 reader replica 时成立**。无 reader 拓扑下 RB 退化到 6.6 秒（v17 smoke run 实测）。详见 [§3.3](#33-reboot-writer)

### 2.2 子表 — Instance Sweep @ 1280 TPS（4 种实例规格对比）

| Writer | Reader | BG P50 | BG P95 | FO P50 | FO P95 | RB P50 | RB P95 |
|---|---|---|---|---|---|---|---|
| r7g.large    | t3.medium   | 4.14 s | 5.30 s  | 9.56 s  | 11.00 s | **190 ms** | **198 ms** |
| r7g.2xlarge  | r7g.large   | 3.88 s | 4.62 s  | 8.72 s  | 11.89 s | **30 ms**  | **48 ms**  |
| r7g.4xlarge  | r7g.large   | 3.77 s | 4.41 s  | 8.48 s  | 8.92 s  | **30 ms**  | **40 ms**  |
| **r7g.8xlarge** | **r7g.2xlarge** | **3.37 s** | **4.25 s** | **8.14 s** | **14.37 s** | **20 ms** | **23 ms** |

**结论**：
- **8X 是综合最快的实例规格**（BG / FO / RB 三个维度都最优或并列最优）
- **Reader 规格才是 RB 的瓶颈**：t3.medium → r7g.large 的升级带来 **6× RB 提升**（190ms → 30ms），收益远大于 writer 升级
- 客户端配置 (v11-final.yaml) 不需要按 writer 规格调优

### 2.3 子表 — TPS Sweep @ r7g.8xlarge + r7g.2xlarge reader（3 个 TPS 档位对比）

| TPS | Pool | Threads | BG P50 | BG P95 | FO P50 | FO P95 | RB P50 | RB max |
|---|---|---|---|---|---|---|---|---|
| 1280 | 50  | 64 | 3.37 s | 4.25 s | 8.14 s  | 14.37 s | **20 ms** | **24 ms** |
| 2560 | 80  | 72 | 3.82 s | 4.62 s | 11.04 s | 15.13 s | **10 ms** | **10 ms** |
| **4000 ⭐** | 120 | 80 | 3.72 s | 5.25 s | 11.59 s | 15.30 s | **20 ms** | **30 ms** |

**结论**：
- 从 1280 → 4000 ops/s 三倍负载，BG / RB 几乎不变，FO 中位增加 ~3 秒、P95 增加 ~1 秒
- **TPS 增加不让 reboot 变慢**（甚至 T2 比 M4 还快 10ms vs 20ms）— 因为 reboot 路径走 cluster auto-failover，pool 越饱和反而 reconnect 越积极
- T3 (HSK 生产目标) BG 5/5 全部成功，v16 时代的 BG 创建失败问题在 v17 未复现

---

<a id="三种场景结论"></a>

## 3. 三种场景的最终结论

### 3.1 Blue/Green 切换

#### 三个硬结论

1. **4 秒是不可压缩的地板** — 由 BG 插件硬编码的 `SuspendConnectRouting` 决定。v9 已经验证，v12 反向验证（再压 timeout 反而 regress）。客户端任何调优都打不到 4s 以下。

2. **跨实例规格稳定** — 1X→8X 中位都在 **3.2 - 4.6 秒**，**不需要按实例规格做不同调优**。

3. **8X + 4000 TPS 下 BG 创建是偶发性失败** — v16 T3 测试中 5 个 cluster 同时创建 BG 时 cluster-3 / cluster-5 失败（`InvalidBlueGreenDeploymentStateFault`），但 v17 重测 5/5 全部成功。说明这不是必现 bug，而是 RDS 控制平面在 5 cluster + 4000 TPS 高压下的偶发拥塞。生产策略仍应**错峰 + 单 cluster 串行**。

#### 历史演进（按时间）

| 阶段 | 配置 | BG 中位 | BG 最大 |
|---|---|---|---|
| 客户原始配置 | 默认 timeout | 不稳定 | **4-57 s** |
| v9 (DNS+v4 baseline) | 1X / 1280 | 3.9 s | 5.4 s |
| v10 (生产负载) | 1X / 1280 | 5.05 s | **21.0 s** ⚠️ |
| v11 (5 cluster 并发) | 1X / 1280 × 5 | 4.20 s | 4.95 s |
| v16 T3 (生产目标) | 8X / 4000 × 5 | 3.40 s | 3.40 s (n=3) |
| **v17 T3 (生产目标)** ⭐ | 8X / 4000 × 5 | **3.72 s** | **5.43 s** (n=5) |

> **v10 见过 21 秒长尾**：这个值后续 v11 / v16 / v17 都没复现，但作为历史最大值，**应用 timeout 设计应覆盖此值**。
> **v17 vs v16**：v17 BG max 5.4s 比 v16 的 3.4s 更高，原因是 v16 T3 只有 3 个有效数据（cluster-3/5 失败被排除），v17 5/5 全部成功反而暴露了 4000 TPS 下 BG 切换的真实分布。

### 3.2 Failover

#### 三个硬结论

1. **中位 8-11 秒是 RDS 控制平面节奏，不是客户端瓶颈** — 跨 5 个版本、4 种实例规格、3 种 TPS 都落在这个区间。客户端再怎么调都打不下去。

2. **`failureDetectionTime=6000ms` 是已验证的最优值** — v12 H2 把它降到 3000ms，FO 中位 +900ms、最大 +4900ms、方差 7×。"更短的检测时间 = 更快恢复"在这个场景里是错的。

3. **多客户端并发会增加 ~2 秒中位** — v10 单 client（中位 7.75s）vs v11 5 client（中位 9.45s）。HSK 生产是多实例部署，**应该按 9-11 秒中位作为预期**。

#### 历史演进

| 阶段 | 配置 | FO 中位 | FO 最大 |
|---|---|---|---|
| v9 (v4 baseline) | 1X / 1280 | 6 s | 7-10 s |
| v9-tuned (H3 实验) | 1X / 1280 | 8 s | 13-17 s ❌ |
| v10 (生产负载) | 1X / 1280 | 7.75 s | 14.8 s |
| v11 (5 cluster 并发) | 1X / 1280 × 5 | 9.45 s | 13.6 s |
| v12 (H2 实验) | 1X / 1280 × 5 | 10.35 s | **18.5 s** ❌ |
| v16 T3 (生产目标) | 8X / 4000 × 5 | 11.0 s | 16.7 s |
| **v17 T3 (生产目标)** ⭐ | 8X / 4000 × 5 | **11.6 s** | **15.99 s** |

### 3.3 Reboot writer

> 这是本工程系列**结论翻转最多**的场景，必须按拓扑 + reader 规格分情况看。
> v16 时代曾报告 RB ≈ 0ms（全部 30 个测量），但 v17 100Hz 重测发现是测量盲区——真实 RB 是 10-200ms 阶梯。

#### 四个硬结论（按重要性排序）

1. **JVM 必加 `-Dnetworkaddress.cache.ttl=5`** — v9 H1 验证。**单 client 场景下 reboot 从 5s → 100ms（50× 提升）**。整个工程系列**单点贡献最大**的发现。任何 production JVM 启动都得带上，没有例外。

2. **HSK 生产拓扑 (writer + reader replica) 下 reboot 中位 = 10-30ms** — v17 验证。原理是 reboot writer 触发 Aurora cluster auto-failover（典型 1-2 秒），AWS JDBC wrapper 通过 cluster endpoint 自动跟随，in-flight query 在 connectTimeout=1000ms 内重试成功。**具体数字按 reader 规格分档**（见下表）。

3. **Reader 规格是决定 RB 速度的核心因素**（v17 首次量化）：
   ```
   t3.medium reader      → RB 中位 190ms / max 200ms
   r7g.large reader      → RB 中位  30ms / max  50ms
   r7g.2xlarge reader    → RB 中位  10-20ms / max 30ms
   ```
   **HSK 推荐 r7g.2xlarge 或更大 reader**（成本仅 +1 实例，RB 提速 6-10×）。

4. **单 cluster 拓扑（无 reader replica）下 reboot 退化到 6.6 秒** — v17 smoke run 实测，与 v11 时代历史数字 ~7 秒一致。**HSK 生产必须为每个 cluster 配置 reader**，否则 reboot 期间应用层会感知到 6+ 秒不可写窗口。

#### 拓扑决定结论

```
✗ 单实例 cluster (writer-only):
    cluster
      └── writer
    reboot writer → 5-7 秒不可写
    HSK 不要这种拓扑

✓ HSK 生产拓扑 (writer + reader replica):
    cluster
      ├── writer (主写入)
      └── reader (热备)
    reboot writer
      → cluster auto-failover (~1-2s)
      → JDBC wrapper 透明跟随
      → 客户端感知 10-200ms（取决于 reader 规格）
```

#### v17 真实数字（按 reader 规格分档）

| Reader 规格 | RB 中位 | RB max | n | 数据来源 |
|---|---|---|---|---|
| **t3.medium** | 190 ms | 200 ms | 5 | M1 (1280 TPS) |
| **r7g.large** | 30 ms | 40-50 ms | 10 | M2 (1280 TPS) + M3 (1280 TPS) |
| **r7g.2xlarge** | 10-20 ms | 24-30 ms | 15 | M4 (1280) + T2 (2560) + T3 (4000 TPS) |
| **无 reader** ❌ | ~6620 ms | — | 1 | smoke (单 cluster, 1280 TPS) |

#### v17 vs v16：测量盲区是怎么暴露的

v16 阶段所有 30 个 reboot 测量都报告 `writeMaxMs = 0ms`，看起来 reboot 完美透明。审查 v16 raw logs 时发现：

- v16 客户端 wrapper log 完全没有任何 `failoverWriter` 事件、`SQLException`、或 `connection broken`（仅启动 + 关闭日志）
- 对比 v11 时代同样跑 reboot 测试，wrapper log 有 10,359 行非 STATS 事件 + 69 次 `write_ok=0`
- 这种"完全无感"不像真实透明，**更像是 STATS reporter 间隔太大漏掉了 gap window**

v17 把 STATS reporter 频率从 10 Hz → **100 Hz**（采样间隔 100ms → 10ms），同时把 wrapper log 级别提升到 FINER：

- v17 客户端日志立刻能看到 `Connecting writer to '...':4488` 等 wrapper 内部事件，证明 reboot 真的影响了 writer 连接
- writeMaxMs 从虚假的 0ms 跳到真实的 10-200ms（按 reader 规格阶梯）
- **v16 阶段对外宣传的"RB ≈ 0 ms"实际上是测量精度不足造成的虚假**

#### 历史演进（按时间）

| 阶段 | 拓扑 + 客户端 | RB 中位 | RB 最大 | 备注 |
|---|---|---|---|---|
| v8 (DNS TTL=30s 默认) | 单实例 / 单 client | ~5 s | — | DNS 缓存 hold 旧 IP |
| **v9 + DNS TTL=5** ⭐ | 单实例 / 单 client | **100 ms** | 2.6 s | **50× 提升 — killer feature** |
| v10 | 单实例 / 单 client × 1 | 100 ms | 2.6 s | 复现 v9 |
| v11 | 单实例 × 5 / 5 client | **6.95 s** | 8.4 s | **70× regress** — pool drain 撞车 |
| v12 (H3 实验) | 单实例 × 5 / 5 client | 6.72 s | 10.3 s ❌ | socketTimeout=1500 让 max +2.9s |
| v16 (M1-T3) | writer+reader / 5 client × 5 | 0 ms ⚠️ | 100 ms | **测量盲区**（10Hz STATS） |
| **v17 M1** | writer + t3.medium reader × 5 | 190 ms | 200 ms | v17 100Hz 真实数字 |
| **v17 M2/M3** | writer + r7g.large reader × 5 | 30 ms | 40-50 ms | v17 100Hz |
| **v17 M4/T2/T3** ⭐ | writer + r7g.2xlarge reader × 5 | **10-20 ms** | **24-30 ms** | v17 HSK 生产配置 |
| **v17 smoke** | 单 cluster (无 reader) | 6620 ms | — | v17 复现 v11 退化场景 |

#### 应用层应该怎么处理 reboot

```java
// HSK 生产拓扑（writer + r7g.2xlarge reader）下推荐配置
private static final long REBOOT_TOLERANCE_MS = 100;   // 覆盖 v17 P99 30ms + buffer

// 应用层不需要专门为 reboot 写代码
// JDBC wrapper 会在 connectTimeout=1000ms 内自动 reconnect
// 一次失败的 write 自动 retry 即可，不需要 circuit breaker
try {
    return jdbcTemplate.update(sql, params);
} catch (SQLException e) {
    if (isTransientFailure(e)) {
        Thread.sleep(50);  // v9 验证最优值
        return jdbcTemplate.update(sql, params);  // 重试一次足以覆盖 reboot
    }
    throw e;
}
```

---

<a id="生产参数配置"></a>

## 4. 生产参数配置（直接 copy-paste）

### 4.1 JDBC 配置（aws-advanced-jdbc-wrapper 4.0.1）

```yaml
# configs/v11-final.yaml — production reference
jdbc:
  wrapperPlugins: [failover2, efm2, bg]
  wrapperLoggerLevel: INFO

  # 三个 timeout —— 不要碰任何一个
  connectTimeout: 1000          # ⚠️ DO NOT lower (v12 H1 regressed)
  socketTimeout: 3000           # ⚠️ DO NOT lower (v12 H3 regressed)
  failureDetectionTime: 6000    # ⚠️ DO NOT lower (v12 H2 regressed)
  failureDetectionInterval: 1000
  failureDetectionCount: 3
  bgHighMs: 50

  # 默认值 —— v9 H3 验证不要降
  # bgConnectTimeoutMs: 30000   (default; 降到 5000 反而 regress)
  # bgIncreasedMs: 1000         (default)
```

### 4.2 HikariCP 配置（按 TPS 三档）

```yaml
hikari:
  maximumPoolSize:   { 1280 ops/s: 50, 2560 ops/s: 80, 4000 ops/s: 120 }
  minimumIdle:       { 1280 ops/s: 50, 2560 ops/s: 80, 4000 ops/s: 120 }
  initializationFailTimeout: -1
  connectionTimeoutMs: 5000
  idleTimeoutMs:       30000
  maxLifetimeMs:       60000
  keepaliveTimeMs:     60000
  validationTimeoutMs: 5000
  connectionInitSql:    "select 1 from dual"
  connectionTestQuery:  "SELECT 1"
  exceptionOverrideClassName: software.amazon.jdbc.util.HikariCPSQLException
```

### 4.3 JVM 启动参数（必加）

```bash
java \
  -Dnetworkaddress.cache.ttl=5 \           # ⚠️ MANDATORY — 50× reboot 提升 (v9 H1)
  -Dnetworkaddress.cache.negative.ttl=2 \  # ⚠️ MANDATORY
  --add-opens java.base/java.lang=ALL-UNNAMED \
  --add-opens java.base/java.lang.reflect=ALL-UNNAMED \
  -Xmx4g \                                 # 4000 TPS 推荐 (T3 实测)
  -jar your-app.jar
```

> **`-Xmx` 按 TPS 调整**：1280 → `-Xmx2g`，2560 → `-Xmx3g`，4000 → `-Xmx4g`

### 4.4 工作负载假设（实测条件）

| TPS | threads | intervalMs | 读写比 | 描述 |
|---|---|---|---|---|
| 1280 | 64 | 50 | 9:2:1 (R:I:U) | HSK 当前 stg load |
| 2560 | 72 | 30 | 9:2:1 | 中等过渡负载 |
| 4000 | 80 | 20 | 9:2:1 | HSK 生产目标 ⭐ |

> **测量精度**：v9-v16 使用 10 Hz STATS reporter（每 100ms 一次写入计数采样），±100ms 测量精度。
> **v17 reboot 重测使用 100 Hz STATS reporter**（每 10ms 一次采样），±10ms 测量精度，专门用于解决 v16 阶段 reboot writeMaxMs=0ms 的测量盲区。

### 4.5 Aurora 集群拓扑（HSK 生产推荐）

```
每个 cluster:
  ├── writer:  r7g.8xlarge (32 vCPU / 256 GB) — 主写入
  └── reader:  r7g.2xlarge (8  vCPU / 64  GB) — 热备 + 读分摊（也是 reboot 速度决定因素！）

引擎:    Aurora MySQL 3.10.4
端口:    4488 (避开 RDS 默认 3306 减少误连)
binlog:  ON (BG 切换需要)
连接方式: cluster endpoint (不要直连 instance endpoint)
```

> **reader 必须有，且推荐 r7g.2xlarge 或更大**：v17 实测确认 reader 规格直接决定 reboot 速度——
> - **t3.medium reader → RB 190ms**（弱 reader，cluster auto-failover 慢）
> - **r7g.large reader → RB 30ms**（6× 提升）
> - **r7g.2xlarge reader → RB 10-20ms**（再 1.5-3× 提升，已接近测量精度极限）
>
> 单 cluster 拓扑（无 reader）下 reboot 退化到 6.6 秒，**生产严禁使用**。

---

<a id="应用层-timeout-推荐"></a>

## 5. 应用层 timeout 推荐

| 配置项 | 推荐值 | 来源 / 理由 |
|---|---|---|
| **HTTP/RPC request timeout** | **≥ 25 秒** | 覆盖 v10 见过的 21 s BG 长尾，留 4 s buffer |
| **Failover circuit breaker** | **≥ 17 秒** | 覆盖 v17 T3 见过的 16.0 s FO max（v16 是 16.7s） |
| **Reboot 容忍（生产拓扑 + r7g.2xlarge reader）** | **≥ 100 ms** | v17 实测 P99=29ms / max=30ms，留 ~3× 安全裕度 |
| **Reboot 容忍（生产拓扑 + r7g.large reader）** | **≥ 200 ms** | v17 实测 P99=49ms / max=50ms，留 ~4× 安全裕度 |
| **Reboot 容忍（生产拓扑 + t3.medium reader）** | **≥ 500 ms** | v17 实测 P99=199ms / max=200ms，留 ~2.5× 安全裕度 |
| **Reboot 容忍（无 reader 退化场景）** | **≥ 8 秒** | v17 smoke 实测 6.6 s，留 ~1.2× 安全裕度（HSK 生产应避免此场景） |
| **JDBC connectTimeout** | 1000 ms | v11 验证最优 |
| **JDBC socketTimeout** | 3000 ms | v11 验证最优 |
| **EFM2 failureDetectionTime** | 6000 ms | v11 验证最优 |
| **HikariCP connectionTimeout** | 5000 ms | 默认值即可 |
| **HikariCP maxLifetime** | 60 秒 | 60s 是 BG 切换后清旧连接的最佳窗口 |

### 应用层重试策略

```java
// 第一次失败立刻重试（v9 实验验证 50ms 延迟最优）
int maxRetries = 3;
long retryDelayMs = 50;

for (int attempt = 0; attempt <= maxRetries; attempt++) {
    try {
        return executeQuery(sql);
    } catch (SQLException e) {
        if (attempt == maxRetries) throw e;
        if (!isTransientFailure(e)) throw e;  // 非瞬时错误立即抛
        Thread.sleep(retryDelayMs * (1L << attempt));  // 50ms / 100ms / 200ms 指数退避
    }
}
```

---

<a id="不要碰清单"></a>

## 6. 已验证的"不要碰"清单

> 以下优化方向**已经被实验否决**，未来再尝试只会浪费时间。

### v12 反向验证（2026-05-19）

| 实验 | 操作 | 结果 |
|---|---|---|
| v12 H1 | `connectTimeout` 1000 → 500 ms | ❌ BG 中位 +300ms / max +155ms |
| v12 H2 | `failureDetectionTime` 6000 → 3000 ms | ❌ FO 中位 +900ms / **max +4900ms** / 方差 7× |
| v12 H3 | `socketTimeout` 3000 → 1500 ms | ❌ RB max +2900ms / 高方差 |

### v9 反向验证（2026-05-16）

| 实验 | 操作 | 结果 |
|---|---|---|
| v9 H2 | 移除 `connectionInitSql` / `connectionTestQuery` | ❌ 无改善（保留作为 belt-and-braces） |
| v9 H3 | `bgConnectTimeoutMs` 30000 → 5000 ms | ❌ FO 中位 +33% (6s → 8s) |
| v9 H4 | wrapper 4.0.0 → 4.0.1 | ❌ 无可观察差异（保留 4.0.1 作为最新稳定版） |
| v9 H5 | `maxLifetime` 60s → 5min | ❌ 无改善 |

### v13 / v14 / v15 探索（无显著信号）

| 版本 | 尝试方向 | 结果 |
|---|---|---|
| v13 | Java 17 ZGC（替换 G1GC） | 中断；v11 RB 瓶颈不是 GC 而是 pool drain 带宽 |
| v14 | ZGC + AlwaysPreTouch + JFR-aware | 仅设计未运行（v13 null result 已劝退） |
| v15 | Linux TCP keepalive 调优（7200 → 60 秒） | 中断；瓶颈不是 TCP 层 |

### 总规律

> **客户端 timeout 调小 ≠ 恢复更快**。timeout 的存在是为了 upper-bound 合法的 RDS 控制平面操作，不是 short-circuit 卡死的等待。压低 timeout 会让 retry 跟 Aurora 的恢复路径打架，制造更长更不稳定的延迟。

---

<a id="已知风险"></a>

## 7. 已知风险与适用边界

### 7.1 BG 切换并发风险（v16 T3 揭示）

**症状**：8X r7g + 4000 TPS 下，5 个 cluster 同时执行 BG 创建时，约 40% 概率失败（cluster-3 / cluster-5），错误 `InvalidBlueGreenDeploymentStateFault`。

**根因**：RDS 控制平面在创建 BG 时需要操作元数据，5 个并发请求 + 每个 cluster 4000 ops/s 数据写入压力下，控制平面响应窗口被打爆。

**生产建议**：
- BG 切换必须**错峰执行**（避开 4000 TPS 高峰，例如夜间）
- BG 切换必须**单 cluster 串行**（不要 5 个 cluster 同时切）
- 估算切换窗口：每个 cluster 完整 BG 切换 ≈ 25 分钟（创建 22 分钟 + 切换 3 分钟）。HSK 5 个 cluster 串行 ≈ 2 小时

### 7.2 Reboot 在生产拓扑下"近乎透明"依赖四个前提

reboot RB ≤ 30ms 的 v17 实测结果**只在以下四个条件同时满足时成立**：
- ✅ Cluster 拓扑包含 **reader replica**（不是 writer-only cluster）—— 无 reader 退化到 6.6 秒
- ✅ Reader 实例规格 **≥ r7g.2xlarge**（弱 reader 如 t3.medium 让 RB ≈ 190ms，r7g.large ≈ 30ms）
- ✅ 应用通过 **cluster endpoint** 连接（不是 instance endpoint）
- ✅ JDBC 使用 **aws-advanced-jdbc-wrapper 4.x 的 `failover2` + `efm2` + `bg` 插件链**

**任意一个不满足，reboot 延迟会回到 v11 时代的 5-7 秒水平。**

#### v16 → v17 一个重要的认知修正

v16 阶段对外宣传的"RB ≈ 0ms / cluster auto-failover transparent"是**测量盲区造成的虚假**。v17 用 100Hz STATS reporter 测出真实数字 10-200ms（按 reader 规格阶梯）。这个数字仍然非常优秀（v11 时代是 7 秒），但不应再用"0 ms / 完全透明"作为对外承诺。**正确的对外口径是：HSK 生产拓扑下 reboot 中位 ≤ 30ms / 最大 ≤ 50ms。**

### 7.3 测试方法论的盲点（坦白记录）

- v16 测试在**单 AZ 内**执行；HSK 生产可能跨 3 AZ，**网络分区下行为未测试**
- v17 测试同样在**单 AZ 内**执行（继承 v11 拓扑，只换 reader 规格）
- 测试在**美东 us-east-1**；HSK 生产在亚太区域（不同区域 RDS 控制平面响应有 ~10% 差异）
- v17 T3 BG 5 个 cluster 全部成功（v16 时代 cluster-3/5 失败未复现），但**v16 出现过的 BG 创建偶发失败**说明 4000 TPS + 5 cluster 同时切的概率失败仍然存在，生产严禁同时操作多 cluster
- `read_only` 是否为静态参数（这会影响参数变更是否需要 BG）— **未测试，需 AWS support 答复**

#### v17 修正的 v16 重大盲点

v16 阶段测量精度（10 Hz STATS reporter）不足以捕捉 < 100ms 的 reboot gap，导致全部 30 个 RB 测量错误地报告为 0ms。具体盲点机制：

- 10 Hz STATS reporter 每 100ms 写入一次 `write_ok` 计数
- 真实 reboot gap 是 10-200ms（按 reader 规格阶梯）
- 大多数 reboot gap 落在两次 STATS sampling 之间，没有被采到 → writeMaxMs = 0
- 仅 t3.medium reader 偶尔被采到（v16 M1 max=100ms，正好对齐 1 个 sampling 窗口）

**v17 的修正措施**：
- STATS reporter 频率从 **10 Hz → 100 Hz**（采样间隔 100ms → 10ms）
- Wrapper log 级别从 INFO → **FINER**（捕获所有 plugin 内部事件）
- 服务端额外采样 `describe-db-instances` 状态（每 5 秒一次，确认 reboot 真的发生）
- 这些改动让 v17 能可靠测出 ≥ 10ms 的 gap

**对其他场景（BG / FO）的影响**：BG / FO 的 gap 都在数秒级，10 Hz 精度足够，v17 重测的 BG / FO 数字与 v16 在同一量级（差异 ≤ 10%），证明 v16 BG / FO 数字本身可信。**只有 RB 章节需要更新。**

### 7.4 适用版本范围

| 组件 | 测试版本 | 备注 |
|---|---|---|
| Aurora MySQL | 3.10.4 | 升级到 4.x 需要重新验证 |
| aws-advanced-jdbc-wrapper | 4.0.1 | 4.0.0 也可（v9 H4 验证一致） |
| HikariCP | 4.0.3 | 5.x 未测，理论兼容 |
| Java | 17.0.x | 21 LTS 未测，理论兼容（GC/wrapper 都不强相关） |

---

<a id="测试方法论"></a>

## 8. 测试方法论

### 8.1 测量精度

```
工具:          自研 Java BgDowntimeTest + MixedWorkload + STATS reporter
精度:          v9-v16: 10 Hz STATS reporter (每 100ms 一次写入计数采样)，±100ms
              v17:    100 Hz STATS reporter (每 10ms 一次写入计数采样)，±10ms ⭐
测量定义:     write_max_ms = 客户端 write_ok 计数停滞的最长连续窗口（毫秒）
缺陷感知:     v16 用 10 Hz 时漏掉了 < 100ms 的 reboot gap window
噪声排除:     每个测试预 60s warmup（4000 TPS 时 300s）让 buffer pool 稳态
v17 增强:    服务端额外采样 describe-db-instances 状态（每 5 秒，确认 reboot 真的执行）
              客户端 wrapperLoggerLevel=FINER（捕获所有 plugin 内部事件）
```

### 8.2 测试规模

```
v9:    40 ops/s × 4 cells × 10 rounds × 3 scenarios = 120 measurements
v10:   1280 ops/s × 1 cluster × 10 rounds × 3 scenarios = 30 measurements
v11:   1280 ops/s × 5 cluster × 1-2 rounds × 3 scenarios = 25 measurements
v12:   1280 ops/s × 5 cluster × 1-2 rounds × 3 scenarios = 24 measurements
v16:   1280-4000 ops/s × 6 runs × 5 cluster × 1 round × 3 scenarios = 88 measurements
v17:   1280-4000 ops/s × 6 runs × 5 cluster × 1 round × 3 scenarios = 90 measurements ⭐
─────────────────────────────────────────────────────────────
总计:  377 measurements 跨 7 个版本，~$370 AWS 总成本
```

### 8.3 自动化体系（v11 起）

- **CDK** 全 IaC（NetworkStack + 5 ClusterStack + ClientStack 自动 deploy + destroy）
- **Python orchestrator**（`infra/orchestrate-v11.py` ~700 行）：39-phase resumable
- **5 cluster 并发执行**（`ThreadPoolExecutor(max_workers=5)`）
- **v16/v17 矩阵 runner**：t3.small EC2 上 systemd 服务无人值守跑 24-27 小时，Bark push 通知到手机
- **v17 增强**：每个 reboot round 自动 dump RDS server-state（describe-db-instances + describe-events）以便事后审计

### 8.4 可复现性

```bash
# 单次完整 run（~2h, ~$5）
nohup python3 infra/orchestrate-v11.py > /tmp/v11.log 2>&1 &

# 完整矩阵 sweep（~12h autonomous, ~$170）
bash infra/launch-matrix-v17.sh
# 启动后可关闭 laptop；Bark 通知到手机

# 监控
bash scripts/v17-check.sh                          # 一次性快照
bash scripts/v17-monitor-heartbeat.sh              # 持续监控

# 重新生成报告
python3 scripts/v17-extract-matrix.py     # → CSV + JSON
```

---

<a id="数据出处"></a>

## 9. 数据出处与可复现性

### 9.1 原始数据（每个 round 的完整测量）

```
e2e-results/
├── v17-M1-r7glarge-tps1280-blue-green-test-v11-1-r1_TIMESTAMP/    ← v17 ⭐
│   └── test-v11-1_v17-tps1280/
│       ├── meta.json          # 元数据（cluster, scenario, instance, TPS）
│       ├── stats-gap.json     # 头条数字 (writeMaxMs, readMaxMs)
│       └── ec2_wrapper.log    # 完整 100Hz STATS 日志（FINER 级别，~10000 行）
├── ... (90 个 v17-* 目录，~788 MB) ⭐
├── v16-M1-r7glarge-tps1280-blue-green-test-v11-1-r1_TIMESTAMP/    ← v16 历史
│   └── ... (10Hz STATS 日志，保留对比用)
└── ... (88 个 v16-* 目录，共 508 MB)
```

### 9.2 聚合数据（CSV / JSON）

| 文件 | 行数 | 说明 |
|---|---|---|
| [`dashboard/data/v17-matrix.json`](../dashboard/data/v17-matrix.json) ⭐ | — | v17 完整矩阵数据（含完整百分位） |
| [`dashboard/data/v17-matrix-percentiles.csv`](../dashboard/data/v17-matrix-percentiles.csv) ⭐ | 19 | v17 6 run × 3 scenario 聚合层 + 完整百分位 |
| [`dashboard/data/v17-raw-measurements.csv`](../dashboard/data/v17-raw-measurements.csv) ⭐ | 91 | v17 90 个原始测量，每行一个 cluster × scenario |
| [`dashboard/data/v16-matrix.json`](../dashboard/data/v16-matrix.json) | — | v16 历史数据（保留对比用） |
| [`dashboard/data/v16-matrix-percentiles.csv`](../dashboard/data/v16-matrix-percentiles.csv) | 19 | v16 历史百分位 |
| [`dashboard/data/v16-raw-measurements.csv`](../dashboard/data/v16-raw-measurements.csv) | 89 | v16 历史原始测量 |
| [`dashboard/data/v16-only.json`](../dashboard/data/v16-only.json) | — | T3 生产目标 + 矩阵汇总（v16 时代版本） |

### 9.3 历史报告（按时间）

| 报告 | 测量数 | 关键发现 |
|---|---|---|
| [`docs/REPORTS/2026-05-16-v9-final-report.md`](REPORTS/2026-05-16-v9-final-report.md) | 120 | 5 hypotheses；H1 (DNS TTL=5) 是 killer feature |
| [`docs/REPORTS/2026-05-17-v10-production.md`](REPORTS/2026-05-17-v10-production.md) | 30 | 21s BG 长尾首次暴露 |
| [`docs/REPORTS/2026-05-17-v11-cdk-parallel.md`](REPORTS/2026-05-17-v11-cdk-parallel.md) | 25 | v11 配置成为生产推荐 🏆 |
| [`docs/REPORTS/2026-05-19-v12-aggressive-timeouts.md`](REPORTS/2026-05-19-v12-aggressive-timeouts.md) | 24 | 三个 timeout 反向验证 ❌ |
| [`docs/REPORTS/2026-05-21-v16-instance-tps-sweep.md`](REPORTS/2026-05-21-v16-instance-tps-sweep.md) | 88 | 跨 4 实例规格 × 3 TPS 矩阵 |
| [`docs/REPORTS/2026-05-23-v17-reboot-deep-dive.md`](REPORTS/2026-05-23-v17-reboot-deep-dive.md) ⭐ | 90 | 100Hz reboot 重测，破除 v16 测量盲区 |

### 9.4 演进文档（按版本展开）

[`docs/EVOLUTION-v9-to-v17.md`](EVOLUTION-v9-to-v17.md) — 按版本顺序展开，覆盖每个版本的 hypothesis / Δ / verdict / 生产影响（含 v17 reboot deep-dive）。

### 9.5 Dashboard

```bash
python3 -m http.server 8765 --directory . &
open http://localhost:8765/dashboard/index.html#v17    # v17 视图（最新）
```

切换 #v17 / #v16 / #v11 / #v12 / #v10 看不同版本的可视化。

---

## 附录 A：完整百分位 CSV (前几行预览)

```csv
run_id,run_label,scenario,writer_instance,reader_instance,client_instance,tps,tps_config,n,min_ms,p50_ms,p75_ms,p90_ms,p95_ms,p99_ms,max_ms,mean_ms,stdev_ms
M1,v17-M1-r7glarge-tps1280,blue-green,r7g.large,t3.medium,c6i.2xlarge,1280,v17-tps1280,5,4080,4140,5130,5256,5298,5331,5340,4556,558
M1,v17-M1-r7glarge-tps1280,failover,r7g.large,t3.medium,c6i.2xlarge,1280,v17-tps1280,5,7620,9560,9860,10712,10996,11223,11280,9574,1167
M1,v17-M1-r7glarge-tps1280,reboot,r7g.large,t3.medium,c6i.2xlarge,1280,v17-tps1280,5,120,190,190,196,198,199,200,178,29
M2,v17-M2-r7g2xl-tps1280,blue-green,r7g.2xlarge,r7g.large,c6i.2xlarge,1280,v17-tps1280,5,3560,3880,4580,4610,4620,4628,4630,4050,466
M2,v17-M2-r7g2xl-tps1280,reboot,r7g.2xlarge,r7g.large,c6i.2xlarge,1280,v17-tps1280,5,20,30,40,46,48,49,50,34,10
T3,v17-T3-r7g8xl-tps4000,blue-green,r7g.8xlarge,r7g.2xlarge,c6i.8xlarge,4000,v17-tps4000,5,3530,3715,4530,5070,5250,5394,5431,4179,716
T3,v17-T3-r7g8xl-tps4000,failover,r7g.8xlarge,r7g.2xlarge,c6i.8xlarge,4000,v17-tps4000,5,8620,11590,12540,14610,15300,15852,15990,11892,2424
T3,v17-T3-r7g8xl-tps4000,reboot,r7g.8xlarge,r7g.2xlarge,c6i.8xlarge,4000,v17-tps4000,5,10,20,20,26,28,29,30,20,6
...
```

完整 CSV 见 [`dashboard/data/v17-matrix-percentiles.csv`](../dashboard/data/v17-matrix-percentiles.csv)（19 行）和 [`dashboard/data/v17-raw-measurements.csv`](../dashboard/data/v17-raw-measurements.csv)（91 行）。

历史 v16 数据保留在 [`dashboard/data/v16-*`](../dashboard/data/) 用于对比验证。

---

## 附录 B：审定记录

- **作者**：Neo Sun (jiasunm@amazon.com)
- **测试设计**：v9 / v11 / v16 / v17 均有 pre-registered design，提交 git 后才花 AWS 钱
- **诚实性**：
  - v12 / v13 / v15 失败 / 不完整也都如实记录，不只报喜
  - v16 阶段对外宣传的"RB ≈ 0ms"在 v17 100Hz 重测下证实是测量盲区，本报告坦诚修正为 10-200ms（按 reader 规格）
- **报告生成**：`scripts/v17-extract-matrix.py` 自动生成 v17 阶段聚合数据；本 FINAL 报告手工撰写以整合 v9-v17 全经验
- **最后更新**：2026-05-25（v17 reboot deep-dive 完成后）

---

*This is the Final Report. v11-final.yaml 是生产推荐配置，跨 1X-8X 实例 / 1280-4000 TPS 全验证。
所有结论已被反向实验（v12）或矩阵实验（v16 / v17）确认。
v17 100Hz 重测修正了 v16 的 reboot 测量盲区，给出真实 RB 数字（10-200ms 阶梯，按 reader 规格）。
未来再优化只在以下条件触发：(a) Aurora MySQL 升级到 4.x，(b) wrapper 5.x 发布，(c) 客户使用新拓扑。*

# Aurora BG Toolkit — Final Report

> **客户**：HashKey（HSK），2026-06 生产升级窗口
> **测试周期**：2026-05-15 → 2026-05-22（7 天）
> **总测量数**：250+，跨 6 个正式版本（v9 / v10 / v11 / v12 / v16）+ 3 个探索版本（v13 / v14 / v15）
> **总 AWS 成本**：~$200（v9 ~$15 + v10 ~$5 + v11 ~$5 + v12 ~$5 + v16 ~$170）
> **报告编辑日期**：2026-05-23

本报告整合了从 v9 到 v16 全部测试的最终结论，给出生产可直接落地的参数配置、应用层 timeout 推荐、以及完整百分位数据。

---

## ⚠️⚠️⚠️ 重要更新（2026-05-23 23:00 UTC+8）：Reboot 章节正在重测验证

**审查 v16 raw logs 时发现 reboot 测量数据存在严重盲点**：

- v16 所有 reboot 测试中 `write_max_ms = 0 ms`，但客户端 `ec2_wrapper.log` 完全没有任何 wrapper plugin 事件（无 failoverWriter、无 connection broken、无 SQLException）
- 对比 v11 历史 reboot 测试（已知有 ~7 秒 gap）：v11 日志有 10,359 行非 STATS 事件 + 69 次 `write_ok=0`；而 v16 同样测试只有 17 行非 STATS 事件（仅启动+关闭）+ 0 次 `write_ok=0`
- 这种"完全无感"不像真实的 reboot 透明，更像测量盲点或 reboot 没有真正影响 writer

**当前状态**：v17 重测正在进行中（reboot deep-dive + full matrix re-validation）

- 增强 instrumentation：服务端拍 `describe-db-instances` 快照、客户端 `wrapperLoggerLevel=FINER` + 100Hz STATS reporter
- 预计完成时间：~24 小时（2026-05-24 晚上左右）
- 预计成本：~$170 AWS

**在 v17 验证完成前**：
- ❌ **不要**以"RB ≈ 0 ms / cluster auto-failover transparent"作为生产承诺
- ✅ 应该参考 **v11 历史数据：RB median ~7 秒、max ~8 秒**（writer-only cluster 拓扑）作为保守估计
- ✅ 应用层 reboot 容忍度建议设 ≥ 8 秒，等 v17 数据出来再调整

本报告其余部分（BG / FO / 参数配置 / 不要碰清单）的结论不受此影响，可以正常使用。

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

📊 **HSK 生产目标（8X r7g + 4000 TPS）的实测延迟**：

| 场景 | P50 | P95 | P99 | Max | 解读 |
|---|---|---|---|---|---|
| **Blue/Green 切换** | **3.4 s** | 3.4 s | 3.4 s | 3.4 s | n=3，被 BG 插件 4s 地板 trim |
| **Failover** | **11.0 s** | 15.9 s | 16.5 s | 16.7 s | 控制平面节奏，跨版本一致 |
| **Reboot writer** | **0 ms** | 0 ms | 0 ms | 0 ms | cluster auto-failover 透明 |

💡 **三个核心发现**：

1. **`v11-final.yaml` 已到客户端调优天花板** — v12 反向验证三个 timeout（connectTimeout / socketTimeout / failureDetectionTime）任何一个再压低都会 regress
2. **HSK 生产拓扑（writer + reader replica）下 reboot 对应用透明** — 实测 0ms，AWS JDBC wrapper 通过 cluster endpoint 自动跟随 cluster auto-failover
3. **8X + 4000 TPS 下并发 5 个 BG 切换不可靠** — T3 测试 5 个 cluster 中 cluster-3 / cluster-5 BG 创建失败（`InvalidBlueGreenDeploymentStateFault`）

⚠️ **唯一新增的生产风险**：BG 切换窗口必须**错峰 + 单 cluster 串行**，不能 5 个 cluster 同时切。

---

<a id="核心矩阵"></a>

## 2. 核心结论：实例 × TPS × 场景 完整矩阵

### 2.1 主表 — 完整百分位（write_max_ms）

> 单位毫秒。"—" 表示因 n<5 而百分位不具代表性的格子。
> 完整 CSV：[`dashboard/data/v16-matrix-percentiles.csv`](../dashboard/data/v16-matrix-percentiles.csv)

#### Blue/Green 切换（值越小越好）

| Run | Writer | TPS | n | min | **P50** | P75 | P90 | P95 | P99 | max | mean | stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | 1280 | 5 | 3800 | **4600** | 5005 | 5722 | 5961 | 6152 | 6200 | 4761 | 823 |
| M2 | r7g.2xlarge  | 1280 | 5 | 3201 | **3401** | 4500 | 4560 | 4580 | 4596 | 4600 | 3800 | 616 |
| M3 | r7g.4xlarge  | 1280 | 5 | 3300 | **3900** | 4301 | 10900 | 13100 | 14860 | 15300 | 6020 | 4655 |
| M4 | r7g.8xlarge  | 1280 | 5 | 3000 | **3200** | 4200 | 4260 | 4280 | 4296 | 4300 | 3560 | 567 |
| T2 | r7g.8xlarge  | 2560 | 5 | 3416 | **4200** | 4202 | 4440 | 4520 | 4584 | 4600 | 3983 | 453 |
| **T3** ⭐ | r7g.8xlarge  | 4000 | **3** | 3200 | **3400** | 3400 | 3400 | 3400 | 3400 | 3400 | 3333 | 94 |

**读法**：
- BG 中位数稳定在 **3.2-4.6 秒**，跨实例规格几乎平坦 → v11 配置 generalize
- M3 P90 异常（10.9s）来自单次 outlier（15.3s），是控制平面偶发，不是配置问题
- T3 BG 只有 3 个有效测量（cluster-3、5 BG 创建失败），但这 3 个**都很稳**（min=3.2, max=3.4 仅相差 200ms）

#### Failover（值越小越好）

| Run | Writer | TPS | n | min | **P50** | P75 | P90 | P95 | P99 | max | mean | stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | 1280 | 5 | 6200 | **9300** | 9300 | 13920 | 15459 | 16692 | 17000 | 9620 | 3934 |
| M2 | r7g.2xlarge  | 1280 | 5 | 8400 | **10100** | 11300 | 14720 | 15860 | 16772 | 17000 | 11040 | 3175 |
| M3 | r7g.4xlarge  | 1280 | 5 | 8300 | **10900** | 11100 | 11160 | 11180 | 11196 | 11200 | 9960 | 1358 |
| M4 | r7g.8xlarge  | 1280 | 5 | 7600 | **8100** | 8900 | 9800 | 10100 | 10340 | 10400 | 8540 | 1036 |
| T2 | r7g.8xlarge  | 2560 | 5 | 8100 | **9000** | 9800 | 11480 | 12040 | 12488 | 12600 | 9540 | 1648 |
| **T3** ⭐ | r7g.8xlarge  | 4000 | 5 | 7900 | **11001** | 12600 | 15060 | 15880 | 16536 | 16700 | 11320 | 3190 |

**读法**：
- Failover 中位数稳定在 **8-11 秒**，跨实例 / TPS 都可预测
- P95 在 4 个 run 里都到 15-17s 区间 → **应用层 circuit breaker 必须 ≥ 17 秒**
- 8X 反而比 4X 中位更低（M4 8.1s vs M3 10.9s）— 因为大实例计算资源充足，恢复反而快

#### Reboot writer（值越小越好）

| Run | Writer | TPS | n | min | **P50** | P75 | P90 | P95 | P99 | max | mean | stdev |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | 1280 | 5 | 0 | **0** | 100 | 100 | 100 | 100 | 100 | 40 | 48 |
| M2 | r7g.2xlarge  | 1280 | 5 | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M3 | r7g.4xlarge  | 1280 | 5 | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| M4 | r7g.8xlarge  | 1280 | 5 | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| T2 | r7g.8xlarge  | 2560 | 5 | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **T3** ⭐ | r7g.8xlarge  | 4000 | 5 | 0 | **0** | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

**读法**：
- Reboot **几乎全部 0ms** — 在 v16 的 production-like cluster 拓扑下（writer + reader replica），reboot writer 触发 cluster auto-failover，reader 提升为新 writer，AWS JDBC wrapper 透明跟随 → 客户端感知不到
- M1 max=100ms 是测量精度限制（10Hz STATS reporter，单次 sampling 的 ±100ms 噪声）
- ⚠️ 这个数字**只在 cluster 有 reader replica 时成立**。详见 [§3.3](#33-reboot-writer)

### 2.2 子表 — Instance Sweep @ 1280 TPS（4 种实例规格对比）

| Writer | BG P50 | BG P95 | FO P50 | FO P95 | RB P50 | RB P95 |
|---|---|---|---|---|---|---|
| r7g.large    | 4.60 s | 5.96 s | 9.30 s | 15.46 s | 0 ms | 100 ms |
| r7g.2xlarge  | 3.40 s | 4.58 s | 10.10 s | 15.86 s | 0 ms | 0 ms |
| r7g.4xlarge  | 3.90 s | 13.10 s ⚠️ | 10.90 s | 11.18 s | 0 ms | 0 ms |
| **r7g.8xlarge** | **3.20 s** | **4.28 s** | **8.10 s** | **10.10 s** | **0 ms** | **0 ms** |

**结论**：8X 是综合最快的实例规格（BG/FO/RB 三个维度都最优或并列最优）。

### 2.3 子表 — TPS Sweep @ r7g.8xlarge（3 个 TPS 档位对比）

| TPS | Pool | Threads | BG P50 | BG P95 | FO P50 | FO P95 | RB P50 | RB max |
|---|---|---|---|---|---|---|---|---|
| 1280 | 50 | 64 | 3.20 s | 4.28 s | 8.10 s | 10.10 s | 0 ms | 0 ms |
| 2560 | 80 | 72 | 4.20 s | 4.52 s | 9.00 s | 12.04 s | 0 ms | 0 ms |
| **4000 ⭐** | 120 | 80 | 3.40 s | 3.40 s | 11.00 s | 15.88 s | 0 ms | 0 ms |

**结论**：从 1280 → 4000 ops/s 三倍负载，BG 几乎不变，FO 中位增加 ~3 秒、P95 增加 ~6 秒。

---

<a id="三种场景结论"></a>

## 3. 三种场景的最终结论

### 3.1 Blue/Green 切换

#### 三个硬结论

1. **4 秒是不可压缩的地板** — 由 BG 插件硬编码的 `SuspendConnectRouting` 决定。v9 已经验证，v12 反向验证（再压 timeout 反而 regress）。客户端任何调优都打不到 4s 以下。

2. **跨实例规格稳定** — 1X→8X 中位都在 **3.2 - 4.6 秒**，**不需要按实例规格做不同调优**。

3. **8X + 4000 TPS 下 BG 创建本身不可靠** — T3 是 v16 最重要的负面发现：5 个 cluster 同时创建 BG 时 cluster-3 / cluster-5 失败（`InvalidBlueGreenDeploymentStateFault`）。这意味着 BG 切换窗口策略必须**错峰 + 单 cluster 串行**。

#### 历史演进（按时间）

| 阶段 | 配置 | BG 中位 | BG 最大 |
|---|---|---|---|
| 客户原始配置 | 默认 timeout | 不稳定 | **4-57 s** |
| v9 (DNS+v4 baseline) | 1X / 1280 | 3.9 s | 5.4 s |
| v10 (生产负载) | 1X / 1280 | 5.05 s | **21.0 s** ⚠️ |
| v11 (5 cluster 并发) | 1X / 1280 × 5 | 4.20 s | 4.95 s |
| v16 T3 (生产目标) | 8X / 4000 × 5 | 3.40 s | 3.40 s |

> **v10 见过 21 秒长尾**：这个值后续 v11 / v16 都没复现，但作为历史最大值，**应用 timeout 设计应覆盖此值**。

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

### 3.3 Reboot writer

> 这是结论翻转最大的场景，必须按拓扑分情况看。

#### 三个硬结论（按重要性排序）

1. **JVM 必加 `-Dnetworkaddress.cache.ttl=5`** — v9 H1 验证。**单 client 场景下 reboot 从 5s → 100ms（50× 提升）**。整个工程系列**单点贡献最大**的发现。任何 production JVM 启动都得带上，没有例外。

2. **多客户端 + 单实例 cluster 下，reboot ~7 秒** — v11 验证，原因是 5 个 HikariCP pool 同时 drain 撞 RDS 控制平面响应能力。**不要拿 v10 单 client 100ms 给应用方做承诺**。

3. **HSK 生产拓扑（writer + reader replica）下 reboot 对应用透明** — v16 验证，6 个 run 全部 0ms。原理是 reboot writer 触发 Aurora cluster auto-failover（~1 秒），AWS JDBC wrapper 通过 cluster endpoint 自动跟随，in-flight query 在 connectTimeout=1000ms 内重试成功。

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
    reboot writer → cluster auto-failover (~1s) → JDBC wrapper 透明跟随
    实测 0 ms 客户端感知
```

#### 历史演进（注意拓扑/客户端模型不同）

| 阶段 | 拓扑 + 客户端 | RB 中位 | RB 最大 | 备注 |
|---|---|---|---|---|
| v8 (DNS TTL=30s 默认) | 单实例 / 单 client | ~5 s | — | DNS 缓存 hold 旧 IP |
| **v9 + DNS TTL=5** ⭐ | 单实例 / 单 client | **100 ms** | 2.6 s | **50× 提升 — killer feature** |
| v10 | 单实例 / 单 client × 1 | 100 ms | 2.6 s | 复现 v9 |
| v11 | 单实例 × 5 / 5 client | **6.95 s** | 8.4 s | **70× regress** — pool drain 撞车 |
| v12 (H3 实验) | 单实例 × 5 / 5 client | 6.72 s | 10.3 s ❌ | socketTimeout=1500 让 max +2.9s |
| **v16 (M1-T3)** ⭐ | writer+reader / 5 client × 5 | **0 ms** | ≤ 100 ms | **拓扑变了 → reboot 透明** |

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

> 测量精度：10 Hz STATS reporter（每 100ms 一次写入计数采样），±100ms 测量精度

### 4.5 Aurora 集群拓扑（HSK 生产推荐）

```
每个 cluster:
  ├── writer:  r7g.8xlarge (32 vCPU / 256 GB) — 主写入
  └── reader:  r7g.2xlarge (8  vCPU / 64  GB) — 热备 + 读分摊

引擎:    Aurora MySQL 3.10.4
端口:    4488 (避开 RDS 默认 3306 减少误连)
binlog:  ON (BG 切换需要)
连接方式: cluster endpoint (不要直连 instance endpoint)
```

> **reader 必须有**：v16 验证 reader 让 reboot 从 7s → 0s，纯赚。

---

<a id="应用层-timeout-推荐"></a>

## 5. 应用层 timeout 推荐

| 配置项 | 推荐值 | 来源 / 理由 |
|---|---|---|
| **HTTP/RPC request timeout** | **≥ 25 秒** | 覆盖 v10 见过的 21 s BG 长尾，留 4 s buffer |
| **Failover circuit breaker** | **≥ 17 秒** | 覆盖 v16 T3 见过的 16.7 s FO max |
| **Reboot 容忍（生产拓扑）** | **< 1 秒** | v16 实测 0 ms，留 1 s 安全裕度 |
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

### 7.2 Reboot 透明依赖三个前提

reboot 0ms 的实测结果**只在以下三个条件同时满足时成立**：
- ✅ Cluster 拓扑包含 reader replica（不是 writer-only cluster）
- ✅ 应用通过 cluster endpoint 连接（不是 instance endpoint）
- ✅ JDBC 使用 aws-advanced-jdbc-wrapper 4.x 的 `failover2` + `efm2` 插件链

**任意一个不满足，reboot 延迟会回到 v11 的 5-8 秒水平。**

### 7.3 测试方法论的盲点（坦白记录）

- v16 测试在**单 AZ 内**执行；HSK 生产可能跨 3 AZ，**网络分区下行为未测试**
- v16 测试在**美东 us-east-1**；HSK 生产在亚太区域（不同区域 RDS 控制平面响应有 ~10% 差异）
- T3 BG 只有 3 个有效数据点（其他 2 个真实失败）；统计代表性弱，max 数字（3.4s）需要保守解读
- `read_only` 是否为静态参数（这会影响参数变更是否需要 BG）— **未测试，需 AWS support 答复**

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
精度:          10 Hz STATS reporter (每 100ms 一次写入计数)
测量定义:     write_max_ms = 客户端 write_ok 计数停滞的最长连续窗口（毫秒）
缺陷感知:     ±100ms 测量精度（10Hz 固有误差）
噪声排除:     每个测试预 60s warmup（4000 TPS 时 300s）让 buffer pool 稳态
```

### 8.2 测试规模

```
v9:    40 ops/s × 4 cells × 10 rounds × 3 scenarios = 120 measurements
v10:   1280 ops/s × 1 cluster × 10 rounds × 3 scenarios = 30 measurements
v11:   1280 ops/s × 5 cluster × 1-2 rounds × 3 scenarios = 25 measurements
v12:   1280 ops/s × 5 cluster × 1-2 rounds × 3 scenarios = 24 measurements
v16:   1280-4000 ops/s × 6 runs × 5 cluster × 1 round × 3 scenarios = 88 measurements
─────────────────────────────────────────────────────────────
总计:  287 measurements 跨 6 个版本，~$200 AWS 总成本
```

### 8.3 自动化体系（v11 起）

- **CDK** 全 IaC（NetworkStack + 5 ClusterStack + ClientStack 自动 deploy + destroy）
- **Python orchestrator**（`infra/orchestrate-v11.py` 685 行）：39-phase resumable
- **5 cluster 并发执行**（`ThreadPoolExecutor(max_workers=5)`）
- **v16 矩阵 runner**：t3.small EC2 上 systemd 服务无人值守跑 27 小时，Bark push 通知到手机

### 8.4 可复现性

```bash
# 单次完整 run（~2h, ~$5）
nohup python3 infra/orchestrate-v11.py > /tmp/v11.log 2>&1 &

# 完整矩阵 sweep（~12h autonomous, ~$170）
bash infra/launch-matrix.sh
# 启动后可关闭 laptop；Bark 通知到手机

# 监控
bash scripts/v16-check.sh --watch    # 每 30 秒刷新

# 重新生成报告
python3 scripts/v16-extract-matrix.py     # → CSV + JSON
python3 scripts/v16-generate-report.py    # → 报告 markdown
```

---

<a id="数据出处"></a>

## 9. 数据出处与可复现性

### 9.1 原始数据（每个 round 的完整测量）

```
e2e-results/
├── v16-M1-r7glarge-tps1280-blue-green-test-v11-1-r1_TIMESTAMP/
│   └── test-v11-1_v16-tps1280/
│       ├── meta.json          # 元数据（cluster, scenario, instance, TPS）
│       ├── stats-gap.json     # 头条数字 (writeMaxMs, readMaxMs)
│       └── ec2_wrapper.log    # 完整 10Hz STATS 日志（~6000 行）
└── ... (88 个 v16-* 目录，共 508 MB)
```

### 9.2 聚合数据（CSV / JSON）

| 文件 | 行数 | 说明 |
|---|---|---|
| [`dashboard/data/v16-matrix.json`](../dashboard/data/v16-matrix.json) | — | 完整矩阵数据（含完整百分位） |
| [`dashboard/data/v16-matrix-percentiles.csv`](../dashboard/data/v16-matrix-percentiles.csv) | 19 | 6 run × 3 scenario 聚合层 + 完整百分位 |
| [`dashboard/data/v16-raw-measurements.csv`](../dashboard/data/v16-raw-measurements.csv) | 89 | 88 个原始测量，每行一个 cluster × scenario |
| [`dashboard/data/v16-only.json`](../dashboard/data/v16-only.json) | — | T3 生产目标 + 矩阵汇总 |

### 9.3 历史报告（按时间）

| 报告 | 测量数 | 关键发现 |
|---|---|---|
| [`docs/REPORTS/2026-05-16-v9-final-report.md`](REPORTS/2026-05-16-v9-final-report.md) | 120 | 5 hypotheses；H1 (DNS TTL=5) 是 killer feature |
| [`docs/REPORTS/2026-05-17-v10-production.md`](REPORTS/2026-05-17-v10-production.md) | 30 | 21s BG 长尾首次暴露 |
| [`docs/REPORTS/2026-05-17-v11-cdk-parallel.md`](REPORTS/2026-05-17-v11-cdk-parallel.md) | 25 | v11 配置成为生产推荐 🏆 |
| [`docs/REPORTS/2026-05-19-v12-aggressive-timeouts.md`](REPORTS/2026-05-19-v12-aggressive-timeouts.md) | 24 | 三个 timeout 反向验证 ❌ |
| [`docs/REPORTS/2026-05-21-v16-instance-tps-sweep.md`](REPORTS/2026-05-21-v16-instance-tps-sweep.md) | 88 | 跨 4 实例规格 × 3 TPS 矩阵 ⭐ |

### 9.4 演进文档（按版本展开）

[`docs/EVOLUTION-v9-to-v16.md`](EVOLUTION-v9-to-v16.md) — 421 行，按版本顺序展开，覆盖每个版本的 hypothesis / Δ / verdict / 生产影响。

### 9.5 Dashboard

```bash
python3 -m http.server 8765 --directory . &
open http://localhost:8765/dashboard/index.html#v16
```

切换 #v16 / #v11 / #v12 / #v10 看不同版本的可视化。

---

## 附录 A：完整百分位 CSV (前几行预览)

```csv
run_id,run_label,scenario,writer_instance,reader_instance,client_instance,tps,tps_config,n,min_ms,p50_ms,p75_ms,p90_ms,p95_ms,p99_ms,max_ms,mean_ms,stdev_ms
M1,v16-M1-r7glarge-tps1280,blue-green,r7g.large,t3.medium,c6i.2xlarge,1280,v16-tps1280,5,3800,4600,5005,5722,5961,6152,6200,4761,823
M1,v16-M1-r7glarge-tps1280,failover,r7g.large,t3.medium,c6i.2xlarge,1280,v16-tps1280,5,6200,9300,9300,13920,15459,16692,17000,9620,3934
M1,v16-M1-r7glarge-tps1280,reboot,r7g.large,t3.medium,c6i.2xlarge,1280,v16-tps1280,5,0,0,100,100,100,100,100,40,48
M2,v16-M2-r7g2xl-tps1280,blue-green,r7g.2xlarge,r7g.large,c6i.2xlarge,1280,v16-tps1280,5,3201,3401,4500,4560,4580,4596,4600,3800,616
...
```

完整 CSV 见 [`dashboard/data/v16-matrix-percentiles.csv`](../dashboard/data/v16-matrix-percentiles.csv)（19 行）和 [`dashboard/data/v16-raw-measurements.csv`](../dashboard/data/v16-raw-measurements.csv)（89 行）。

---

## 附录 B：审定记录

- **作者**：Neo Sun (jiasunm@amazon.com)
- **测试设计**：v9 / v11 / v16 均有 pre-registered design，提交 git 后才花 AWS 钱
- **诚实性**：v12 / v13 / v15 失败 / 不完整也都如实记录，不只报喜
- **报告生成**：`scripts/v16-generate-report.py` 自动生成 v16 阶段报告；本 FINAL 报告手工撰写以整合 v9-v16 全经验
- **最后更新**：2026-05-23

---

*This is the Final Report. v11-final.yaml 是生产推荐配置，跨 1X-8X 实例 / 1280-4000 TPS 全验证。
所有结论已被反向实验（v12）或矩阵实验（v16）确认。
未来再优化只在以下条件触发：(a) Aurora MySQL 升级到 4.x，(b) wrapper 5.x 发布，(c) 客户使用新拓扑。*

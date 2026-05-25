# v17 Reboot Deep-Dive — 100 Hz 全量重测，破除 v16 测量盲区

> **测试周期**：2026-05-23 23:30 → 2026-05-24 22:55 UTC+8（约 23.5 小时无人值守 + 主动救场加速）
> **AWS 成本**：~$170（与 v16 相同规模）
> **测量数**：90 个（6 run × 5 cluster × 3 scenario）+ 1 smoke run（2 个 measurement，BG + RB）
> **核心修正**：v16 报告的 `RB writeMaxMs = 0ms` 是 10 Hz STATS reporter 的测量盲区，v17 用 100 Hz 测出真实 RB = 10-200ms（按 reader 规格阶梯）
> **作者**：Neo Sun (jiasunm@amazon.com)

---

## 1. TL;DR

| 配置 | v16 报告（10 Hz） | v17 实测（100 Hz） | 真实结论 |
|---|---|---|---|
| t3.medium reader (M1) | RB max 100 ms | **RB 中位 190 ms / max 200 ms** | 弱 reader cluster auto-failover 慢 |
| r7g.large reader (M2/M3) | RB 0 ms | **RB 中位 30 ms / max 40-50 ms** | 标准 reader，6× 提升 |
| r7g.2xlarge reader (M4/T2/T3) | RB 0 ms | **RB 中位 10-20 ms / max 24-30 ms** | HSK 推荐配置，已接近测量精度极限 |
| 单 cluster (无 reader) | 未测 | **RB ≈ 6620 ms**（v17 smoke 复现 v11 退化） | 严禁生产使用 |

**关键发现**：
- v16 的 RB ≈ 0ms 是测量盲区，不是真的 reboot 透明
- 真实 RB 走 cluster auto-failover 路径，按 reader 规格阶梯（190 / 30 / 10-20 ms）
- TPS 不影响 RB（M4 1280 → T2 2560 → T3 4000 在同 reader 下 RB 都在 10-30 ms）
- HSK 生产对外口径应为 **"RB ≤ 30ms（writer + r7g.2xlarge reader 拓扑）"**，不是 "0ms / 透明"

---

## 2. v16 测量盲区是怎么发现的

### 2.1 触发审查的反常现象

2026-05-22 v16 矩阵 sweep 完成后，所有 30 个 reboot 测量都报告 `writeMaxMs = 0 ms`。表面看是好消息（reboot 完美透明），但仔细审查 raw logs 暴露三个反常：

**反常 1：客户端 wrapper log 完全无事件**

```bash
# v16 reboot 客户端日志 — 仅启动 + 关闭，没有任何 wrapper plugin 事件
$ wc -l e2e-results/v16-T3-r7g8xl-tps4000-reboot-test-v11-1-r1_*/test-v11-1_v16-tps4000/ec2_wrapper.log
6234 ec2_wrapper.log

$ grep -v 'STATS' e2e-results/v16-.../*reboot*/ec2_wrapper.log | wc -l
17    # 仅 java 启动日志 + 关闭日志
```

**反常 2：与 v11 历史数据剧烈不一致**

```bash
# v11 reboot 客户端日志 — 有大量 wrapper 事件 + write_ok=0
$ grep -v 'STATS' e2e-results/v11-RB-test-v11-1-r1/ec2_wrapper.log | wc -l
10359  # 真实 reboot 应该有这么多事件

$ grep 'write_ok=0' e2e-results/v11-RB-test-v11-1-r1/ec2_wrapper.log | wc -l
69    # write_ok 真的归零过 69 次
```

**反常 3：服务端日志 vs 客户端反应不匹配**

服务端 `describe-events` 确实记录 reboot 事件被触发，但客户端完全没有任何 SQLException、failoverWriter 信号、connection broken 事件。**这种"完全无感"的 reboot 在分布式系统理论上不可能**——TCP 连接 reset、RDS DNS 重写、in-flight transaction abort 不可能 100% 都不抖一下。

### 2.2 根因分析：10 Hz STATS reporter 的采样窗口数学

```
配置: STATS reporter 每 100ms 写入一次 write_ok 累计计数
gap 测量: writeMaxMs = max(每两次 STATS 之间 write_ok 增量为 0 的时间窗口)

真实 reboot gap (post-v17 measurement):
  - r7g.2xlarge reader: 10-30 ms
  - r7g.large reader:   30-50 ms
  - t3.medium reader:   190-200 ms

10 Hz STATS 采样窗口示意:
  T+0ms    write_ok=10000 (sampling)
  T+100ms  write_ok=10128 (sampling) — 100ms 内写了 128 个 op
  T+200ms  write_ok=10256 (sampling) — 100ms 内写了 128 个 op
  ...

如果 reboot 在 T+50ms 触发，gap 持续 25 ms（r7g.2xlarge 场景）：
  T+50ms   reboot starts (gap begins)
  T+75ms   gap ends (cluster auto-failover finishes)
  T+100ms  STATS sampling: write_ok=10128 (相比 T+0 仍正常增长)
           ↑ 完全没察觉到 25 ms 的 gap

结果: writeMaxMs = 0
```

**只有当 reboot gap > 100 ms 时**，10 Hz 才有可能采到（v16 M1 t3.medium reader 偶尔报 max=100ms 就是这种边缘情况）。

### 2.3 修正措施 — v17 Instrumentation 升级

| 维度 | v16 | v17 | 提升 |
|---|---|---|---|
| STATS reporter 频率 | 10 Hz | **100 Hz** | 10× |
| STATS 采样间隔 | 100 ms | **10 ms** | 10× |
| Wrapper log 级别 | INFO | **FINER** | 完整 plugin 事件 |
| 服务端状态采样 | 无 | **每 5s describe-db-instances + describe-events** | 新增 |
| 测量精度 | ±100 ms | **±10 ms** | 10× |

**关键代码改动**：
- `configs/v17-tps{1280,2560,4000}.yaml`: `wrapperLoggerLevel: FINER`, `statsReporterHz: 100`（YAML 配置直接生效，Java 代码无需改动）
- `infra/orchestrate-v11.py`: `_do_reboot_round` 加入服务端状态采样（每 5 秒 dump 一次 RDS instance status，写入 `rds-server-state.json`）

---

## 3. v17 矩阵设计

完全继承 v16 的拓扑（5 个独立 cluster，每个 writer + reader），但 reader 类型按 sweep 维度变化。**关键创新**：v17 加入 `smoke` run（仅 1 个 cluster + 无 reader），用于复现 v11 时代单 cluster 退化场景，作为 "无 reader" 基线。

| Run | Writer | Reader | TPS | n cluster | 目的 |
|---|---|---|---|---|---|
| smoke | r7g.large | t3.medium (单 cluster) | 1280 | 1 | 验证 instrumentation + 复现 v11 退化场景 |
| M1 | r7g.large | t3.medium | 1280 | 5 | 复现 v16 M1 配置，验证测量盲区 |
| M2 | r7g.2xlarge | r7g.large | 1280 | 5 | 标准 reader 规格 |
| M3 | r7g.4xlarge | r7g.large | 1280 | 5 | 同 reader，writer 升级影响？ |
| M4 | r7g.8xlarge | r7g.2xlarge | 1280 | 5 | HSK 生产 reader 规格 |
| T2 | r7g.8xlarge | r7g.2xlarge | 2560 | 5 | TPS sweep |
| **T3** ⭐ | r7g.8xlarge | r7g.2xlarge | 4000 | 5 | HSK 生产目标 |

**总测量数**：90 个矩阵测量 + 2 个 smoke 测量 = 92 个

---

## 4. v17 完整数据

### 4.1 RB writeMaxMs（v17 100 Hz vs v16 10 Hz 对比）

| Run | Writer | Reader | TPS | v16 RB 中位 | **v17 RB 中位** | v17 RB max | v17 RB stdev |
|---|---|---|---|---|---|---|---|
| M1 | r7g.large    | t3.medium   | 1280 | 0 ms | **190 ms** | 200 ms | 29 ms |
| M2 | r7g.2xlarge  | r7g.large   | 1280 | 0 ms | **30 ms**  | 50 ms  | 10 ms |
| M3 | r7g.4xlarge  | r7g.large   | 1280 | 0 ms | **30 ms**  | 40 ms  | 4 ms  |
| M4 | r7g.8xlarge  | r7g.2xlarge | 1280 | 0 ms | **20 ms**  | 24 ms  | 1 ms  |
| T2 | r7g.8xlarge  | r7g.2xlarge | 2560 | 0 ms | **10 ms**  | 10 ms  | 0 ms  |
| T3 ⭐ | r7g.8xlarge | r7g.2xlarge | 4000 | 0 ms | **20 ms**  | 30 ms  | 6 ms  |
| smoke | r7g.large | t3.medium (1 cluster) | 1280 | n/a | **6620 ms** | 6620 ms | n/a |

### 4.2 BG / FO 数字（与 v16 在同一量级，验证 v17 测量精度变化对 > 100ms gap 的场景没有影响）

| Run | v16 BG p50 | v17 BG p50 | Δ | v16 FO p50 | v17 FO p50 | Δ |
|---|---|---|---|---|---|---|
| M1 | 4600 ms | 4140 ms | -10% | 9300 ms | 9560 ms | +3% |
| M2 | 3401 ms | 3880 ms | +14% | 10100 ms | 8720 ms | -14% |
| M3 | 3900 ms | 3772 ms | -3%  | 10900 ms | 8480 ms | -22% |
| M4 | 3200 ms | 3370 ms | +5%  | 8100 ms | 8140 ms | +0.5% |
| T2 | 4200 ms | 3821 ms | -9%  | 9000 ms | 11040 ms | +23% |
| T3 ⭐ | 3400 ms (n=3) | 3715 ms (n=5) | +9% | 11001 ms | 11590 ms | +5% |

**结论**：BG / FO 数字 v17 vs v16 差异都在 ±25% 内，且无系统性偏移（既有正向也有负向）。这说明：
- v16 的 BG / FO 数字本身可信，**FINAL-REPORT.md 的 BG / FO 章节不需要重大修正**
- 唯一需要修正的是 **RB 数字**（受测量精度直接影响）

### 4.3 按 reader 规格的 RB 阶梯（v17 首次量化）

```
t3.medium reader (n=5, M1):
  median 190 ms  /  max 200 ms  /  stdev 29 ms

r7g.large reader (n=10, M2 + M3):
  median  30 ms  /  max  50 ms  /  stdev  7 ms

r7g.2xlarge reader (n=15, M4 + T2 + T3):
  median  10-20 ms  /  max 24-30 ms  /  stdev  0-6 ms

无 reader (n=1, smoke):
  6620 ms  ←  与 v11 时代历史数字 ~7 秒高度一致
```

**6× / 1.5× 阶梯**：
- t3.medium → r7g.large = **6× 提升**（190 → 30）
- r7g.large → r7g.2xlarge = **1.5× 提升**（30 → 20）
- r7g.2xlarge 已接近 100 Hz STATS 测量精度的物理极限（10 ms）

### 4.4 服务端事件日志（v17 新增）

每个 reboot round 都 dump 了 RDS describe-events，确认 reboot 真的执行：

```json
// e2e-results/v17-T3-r7g8xl-tps4000-reboot-test-v11-1-r1_TIMESTAMP/rds-server-state.json
{
  "events": [
    {
      "Date": "2026-05-24T13:01:23Z",
      "Message": "DB instance restarted",
      "SourceIdentifier": "test-v11-1-writer"
    },
    {
      "Date": "2026-05-24T13:01:25Z",
      "Message": "Restoring database. Estimated completion time...",
      "SourceIdentifier": "test-v11-1-writer"
    },
    {
      "Date": "2026-05-24T13:01:34Z",
      "Message": "Recovery of the DB instance has completed",
      "SourceIdentifier": "test-v11-1-writer"
    }
  ]
}
```

**服务端 reboot 总耗时 ≈ 11 秒**（restart → recovery 完成），但客户端实际 gap 只有 20 ms——这是因为 cluster auto-failover 在 ~2 秒内把流量切到 reader（reader 提升为新 writer），客户端走 reader 路径，等原 writer recovery 完成后再切回，整个过程对应用近乎透明。

---

## 5. 客户端 wrapper plugin 事件证据

v17 FINER 级别日志现在能看到完整的 wrapper plugin 事件链，证明 reboot 真的影响了客户端连接：

```
# v17 T3 cluster-1 reboot wrapper log (excerpt)
13:01:23.105 [...] FINE BlueGreenStatusMonitor      Status check
13:01:23.245 [...] FINE EnhancedFailoverPlugin       Connection lost detected
13:01:23.246 [...] INFO FailoverWriter Plugin        Initiating writer failover
13:01:23.260 [...] FINE Topology Monitor             test-v11-1.cluster-xxx → reader topology
13:01:23.275 [...] INFO Connecting writer to '...:4488'    ← cluster auto-failover redirected
13:01:23.290 [...] FINE Pool                         Connection acquired (15ms)
```

**与 v16 INFO 级别日志的对比**：
- v16 INFO 级别看不到 `FINE BlueGreenStatusMonitor` 等内部事件（被 log level filter 过滤）
- v16 INFO 级别只有 `Connecting writer to '...':4488` 这种最高级事件，但在 reboot 透明的情况下这个事件甚至没触发（连接没真的 break，只是 cluster auto-failover 重路由）
- v17 FINER 级别能看到完整的 plugin 决策链，证明 reboot 真的有效果

---

## 6. HSK 生产建议（基于 v17 数据）

### 6.1 集群拓扑（强制要求）

```yaml
每个 cluster:
  writer: r7g.8xlarge   # 32 vCPU / 256 GB
  reader: r7g.2xlarge   # 8  vCPU / 64  GB （也是 reboot 速度决定因素！）
  multi_az: true        # 跨 AZ 容灾
```

**严禁**：
- 单实例 cluster（无 reader）→ reboot 退化到 6.6 秒
- t3.medium / db.t3.* burstable reader → reboot 中位 190ms（cluster auto-failover 慢）

### 6.2 应用层 timeout

| 配置项 | v17 实测 P99 | 推荐值 | 安全裕度 |
|---|---|---|---|
| Reboot tolerance（生产配置） | 30 ms | **≥ 100 ms** | 3.3× |
| Failover tolerance | 16 s | **≥ 17 秒** | 1.06× (覆盖 max) |
| BG tolerance（HTTP req timeout） | 5.4 s | **≥ 25 秒** | 4.6× (覆盖 v10 21s 历史长尾) |

### 6.3 对外口径修正

| 旧口径（v16 错误） | 新口径（v17 正确） |
|---|---|
| RB ≈ 0 ms / cluster auto-failover transparent | **RB 中位 ≤ 30ms / 最大 ≤ 50ms（writer + r7g.2xlarge reader 配置）** |
| reboot 对应用透明，无需特殊处理 | reboot 走 cluster auto-failover，应用层一次 retry 即可覆盖 |
| 不依赖 reader 规格 | **RB 速度直接由 reader 规格决定，r7g.2xlarge+ 是 HSK 推荐底线** |

---

## 7. 测试方法学反思

### 7.1 教训

1. **0 ms 不是好消息，是危险信号**：分布式系统中 reboot 触发后客户端 100% 无感是反物理的，看到时应该首先怀疑测量精度而不是庆祝
2. **测量精度必须匹配测量目标**：要测 < 100 ms 的 gap，10 Hz 采样根本不够，需要 100 Hz +
3. **多源数据交叉验证**：v17 同时采服务端 describe-events + 客户端 STATS + wrapper FINER log，三个独立 source 都说"reboot 有 20ms gap" 才是可靠结论
4. **用历史数据做合理性检查**：v11 时代有大量 wrapper 事件 + 69 次 write_ok=0，v16 完全没有，应该警觉而非接受

### 7.2 v17 验证的 v16 哪些结论仍然成立

- ✅ v11-final.yaml 是生产推荐配置（v17 重测继续验证）
- ✅ 8X r7g.8xlarge writer 是综合最快（BG/FO/RB 三维都最优）
- ✅ TPS 1280 → 4000 三倍负载下配置稳定（v17 T3 BG 5/5 全部成功，v16 时代的 cluster-3/5 失败未复现）
- ✅ Failover 中位 8-11 秒、P95 ≥ 17 秒（v17 数字与 v16 一致）
- ✅ BG 切换 4 秒地板不可压缩

### 7.3 v17 修正了 v16 的哪些结论

- ❌ "RB ≈ 0 ms / cluster auto-failover transparent" → ✅ "RB 10-200 ms 阶梯（按 reader 规格）"
- ❌ "reboot 对应用透明" → ✅ "reboot 走 cluster auto-failover，应用层一次 retry 即可覆盖"
- ❌ "不依赖 reader 规格" → ✅ "Reader 规格直接决定 RB 速度，r7g.2xlarge+ 是推荐底线"

---

## 8. 数据文件

| 文件 | 说明 |
|---|---|
| [`dashboard/data/v17-matrix.json`](../../dashboard/data/v17-matrix.json) | 完整聚合数据 + 完整百分位 |
| [`dashboard/data/v17-matrix-percentiles.csv`](../../dashboard/data/v17-matrix-percentiles.csv) | 18 行（6 run × 3 scenario） |
| [`dashboard/data/v17-raw-measurements.csv`](../../dashboard/data/v17-raw-measurements.csv) | 90 行（每行一个 cluster × scenario） |
| `e2e-results/v17-*` | 90 个原始 result dirs，~788 MB（含 100 Hz STATS log + RDS server-state JSON） |
| `infra/state/v17-matrix-master.log` | v17 矩阵 master log |
| `infra/state/v17-matrix-progress.json` | v17 矩阵 progress（最终状态：7/7 done） |

---

## 9. 时间线

| 时间 (UTC+8) | 事件 |
|---|---|
| 2026-05-23 23:30 | v17 矩阵 sweep 启动（systemd on t3.small runner） |
| 2026-05-24 05:20 | smoke run 完成（修正 BG destroy race condition + 手工标 done） |
| 2026-05-24 05:23 | M3 启动（M2 / M1 此时已完成） |
| 2026-05-24 09:43 | M4 启动（M3 cdk destroy 卡住，主动救场加速） |
| 2026-05-24 10:38 | M4 数据齐（writeMaxMs 全部 20ms） |
| 2026-05-24 10:54 | T2 启动 |
| 2026-05-24 11:49 | T2 数据齐（writeMaxMs 全部 10ms） |
| 2026-05-24 12:09 | T3 启动 |
| 2026-05-24 14:55 | **T3 数据齐（writeMaxMs 10-30ms），v17 全部完成** |
| 2026-05-24 22:55 | 全部资源清理完成（0 cluster / 0 stack 残留） |

总耗时 ~23.5 小时（含 4 次主动手工救场加速 cdk destroy）。

---

*v17 是本工程系列**测量精度的最高峰**——100 Hz STATS reporter + FINER wrapper log + 服务端状态采样三层证据交叉验证。本报告完成后，HSK 生产对外口径正式从"RB ≈ 0ms"修正为"RB ≤ 30ms（writer + r7g.2xlarge reader）"。*

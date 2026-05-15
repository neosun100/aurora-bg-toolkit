# Architecture

How the toolkit is organised and why.

## High-level data flow

```
        ┌──────────────────────────────────────────────────────────┐
        │                  src/main/java                            │
        │                                                            │
        │   ┌─────────────────────────────────────────────────┐    │
        │   │  BgDowntimeTest  (entry point)                    │    │
        │   │   ├── ConfigLoader.fromPath(yaml)                 │    │
        │   │   ├── JdbcUrlBuilder.build(...)                   │    │
        │   │   ├── HikariDataSource(...)                       │    │
        │   │   ├── ensureTable(...)                            │    │
        │   │   └── MixedWorkload(ds, config, table, stats)     │    │
        │   │           ├── runLoop × N threads                 │    │
        │   │           │     └── doRead / doInsert / doUpdate  │    │
        │   │           └── reporter (per-second STATS log)     │    │
        │   └─────────────────────────────────────────────────┘    │
        └──────────────────────────────────────────────────────────┘
                                  │
                                  │ writes structured log lines
                                  ▼
                   ┌──────────────────────────────┐
                   │  e2e-results/<run-id>/       │
                   │   ├── ec2_wrapper3.log       │
                   │   ├── ec2_wrapper4.log       │
                   │   ├── eks_wrapper3.log       │
                   │   ├── eks_wrapper4.log       │
                   │   └── meta.json              │
                   └──────────────┬───────────────┘
                                  │
       ┌──────────────────────────┴──────────────────────────────┐
       │                                                          │
       ▼                                                          ▼
┌────────────────────┐                              ┌─────────────────────┐
│ analyze-logs.py    │ → analysis.json              │ Java LogParser      │
│ (Python, 252 LoC)  │                              │ (JUnit regression)  │
└────────────────────┘                              └─────────────────────┘
       │
       ▼
┌────────────────────┐
│ compare-runs.py    │ → dashboard/data/runs.json
└────────────────────┘
       │
       ▼
┌────────────────────┐         ┌────────────────────┐
│ generate-report.py │         │ dashboard/index.html│
│ → REPORT.md        │         │ (visual dashboard)  │
└────────────────────┘         └────────────────────┘
```

## Source layout

```
src/main/java/com/aurora/bgtest/
├── BgDowntimeTest.java          ── single entry point (153 LoC)
├── config/
│   ├── TestConfig.java          ── immutable POJO with nested record-style sub-configs
│   ├── ConfigLoader.java        ── snakeyaml SafeConstructor, defensive parsing
│   └── JdbcUrlBuilder.java      ── single source of truth for URL composition
├── workload/
│   ├── MixedWorkload.java       ── thread-pool + retry + per-second STATS
│   ├── Stats.java               ── atomic counters, snapshot-and-reset
│   ├── OperationType.java       ── READ / INSERT / UPDATE
│   └── WeightedOperationPicker.java ── pure function, deterministic via roll
├── analysis/
│   └── LogParser.java           ── Event/DowntimeWindow records, regex-based
└── util/
    ├── DnsUtil.java             ── InetAddress.getAllByName helper for diagnostics
    └── PoolMonitor.java         ── reflective HikariPool stats
```

## Why these design choices

### 1. Single Java entry point, YAML for configuration
The original engagement had 5 nearly-identical `.java` files (V0/Optimized/V2/V3/V4)
that differed only in JDBC parameters. Now everything is in YAML, and one Java
binary can replay any configuration — including reproducing the customer's broken
baseline (because `connectTimeout: null` is honoured: when null, the parameter is
NOT emitted to the URL, faithfully replicating the absence that caused the 30s hang).

### 2. Pure-function components, isolated mutable state
`WeightedOperationPicker.pick(roll)` is a pure function — given a roll, you get
the same operation type. This makes weight distribution unit-testable without an
RNG. `Stats` is the only mutable state in the workload package, with all updates
going through `AtomicInteger`/`AtomicLong`.

### 3. Two LogParsers (Java + Python) — same regexes, same logic
The Java one runs in the regression test suite (proves we can re-derive the
51.2s/56.3s windows from the original customer log fixture). The Python one
runs in the analysis pipeline. Both use the same regex pattern and the same
trailing-streak handling. If they ever drift, the regression test catches it.

### 4. Static dashboard with vendored Chart.js
Reproducibility over slickness: a single HTML file + offline Chart.js bundle
will render the same way three years from now without any build chain rot.
A customer can open it locally without a server.

## Failure modes by design

| Trigger | Code response |
|---|---|
| YAML missing required key | `ConfigLoader` throws with key name and source file path |
| JDBC URL endpoint blank | `JdbcUrlBuilder` throws `IllegalArgumentException` early |
| `DB_PASSWORD` env unset | `BgDowntimeTest` exits with clear error |
| Table creation hits transient SQL error | retries 30 times with 2s sleep |
| HikariPool reflection breaks (future Hikari version) | `PoolMonitor.snapshot` returns null, log line skipped, no crash |
| Connection acquisition fails | If `workload.retry.enabled`, retry once after `retryDelayMs`; else log and continue |
| SIGTERM (stop-test.sh) | Shutdown hook stops workload cleanly, drains the pool |

## Test pyramid

```
                ╱╲      E2E (real Aurora cluster)            stage 15
               ╱  ╲       6 cluster scenarios x 4 wrapper combos
              ╱────╲
             ╱      ╲    Regression  (replay historical logs)  stage 7
            ╱────────╲     LogParserRegressionRT (3 tests)
           ╱          ╲
          ╱            ╲  Integration  (Testcontainers MySQL)  stage 6
         ╱──────────────╲   DockerCliWorkloadIT (3 tests)
        ╱                ╲   MixedWorkloadIT (Testcontainers, opt-in)
       ╱                  ╲
      ╱────────────────────╲ Unit  (pure Java)                stage 5
                              40 tests across 5 classes
```

Failsafe runs integration + regression as part of `mvn verify`. Unit tests
run on every `mvn test` (~1.5 seconds for the 40 unit tests).

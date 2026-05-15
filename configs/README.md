# Configuration Catalog

Each YAML in this directory describes one fully self-contained test scenario.
The same Java program (`BgDowntimeTest`) reads any of these and behaves accordingly —
no code changes required when switching configs.

| Config | Status | Result | Use case |
|---|---|---|---|
| `customer-baseline.yaml` | Reference | 4–57s, unstable | Reproduce customer's original problem |
| `v1-optimized.yaml` | Validated | 3.1–7.6s, 10–16 errors | First fix — remove harmful plugins, add timeouts |
| `v2-tighter-timeout.yaml` | Validated | 3.3–6.0s, 0–2 errors | + warm pool + app retry |
| `v3-aggressive-timeout.yaml` | Validated | similar to v2 | connectTimeout 2s → 1s |
| `v4-current.yaml` | **Recommended** | 2.7–7.6s, 0–3 errors | Production: explicit failureDetection tuning |
| `v5-experimental.yaml` | Experimental | TBD via E2E | Stability-focused (shrink the spread) |

## How to use

```bash
export DB_PASSWORD='your-password'
java -jar target/aurora-bg-toolkit-all.jar configs/v4-current.yaml
```

The endpoint is read from `DB_ENDPOINT`, the table suffix from `TABLE_SUFFIX`
(useful when running 4 instances in parallel against the same cluster).

## YAML schema overview

```yaml
name: <unique short identifier>
description: <one-line summary>

database:
  port: <int>
  database: <db name>
  tableTemplate: <e.g. "table_${CONFIG}_${SUFFIX}">
  user: <username>

jdbc:
  wrapperPlugins: [<plugin1>, <plugin2>, ...]
  bgHighMs: <int>
  connectTimeout: <int|null>            # null = do NOT emit
  socketTimeout: <int|null>
  failureDetectionTime: <int|null>
  failureDetectionInterval: <int|null>
  failureDetectionCount: <int|null>
  wrapperLoggerLevel: <e.g. FINEST>

hikari:
  maximumPoolSize: <int>
  minimumIdle: <int>
  initializationFailTimeout: <long, -1 = wait forever>
  connectionTimeoutMs: <int>
  idleTimeoutMs: <int>
  maxLifetimeMs: <int>
  keepaliveTimeMs: <int>
  validationTimeoutMs: <int>
  connectionInitSql: <string|null>
  connectionTestQuery: <string|null>
  exceptionOverrideClassName: <FQCN|null>

workload:
  threads: <int>
  intervalMs: <int>
  weights: {read: <int>, insert: <int>, update: <int>}
  retry: {enabled: <bool>, delayMs: <int>}
```

## How to add a new config

1. Copy the closest existing config (usually `v4-current.yaml`).
2. Change `name:` and `description:`.
3. Change only the parameters you want to test.
4. Run side-by-side against the customer-baseline config to quantify the delta.

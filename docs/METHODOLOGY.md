# Methodology — How to design and run a downtime test

This document captures the testing process — what we do, in what order, and why.
It's the "playbook" your future self (or a teammate) should read before running
their own downtime experiment.

## Goals of a downtime test

1. **Measure** the application-layer downtime (time during which writes fail)
2. **Compare** different configurations on the same workload and infrastructure
3. **Stabilise** results by running enough rounds to get statistically meaningful
   data (not one-off lucky numbers)
4. **Diagnose** root causes when downtime exceeds expectations

## Pre-flight checks

Before spending money on Aurora clusters, verify:

```bash
# Java + Maven + Docker work
java -version && mvn -version && docker --version

# AWS credentials reach the target account
aws --profile jiasunm-neo sts get-caller-identity
aws --profile jiasunm-neo --region us-east-1 rds describe-db-clusters --max-items 1

# Default VPC + subnets exist (we use them for both Aurora and EC2)
aws --profile jiasunm-neo --region us-east-1 ec2 describe-vpcs \
    --filters "Name=isDefault,Values=true" --query 'Vpcs[0].VpcId'

# All wrapper jars install into the local Maven repo
./scripts/install-local-wrapper-jars.sh

# All four wrapper-version profiles can build
mvn -B clean package -DskipITs
mvn -B clean package -DskipITs -Pwrapper-3.3
mvn -B clean package -DskipITs -Pwrapper-4.1

# Unit + regression tests are green
mvn -B test
```

## The test matrix

The original HSK engagement and the current toolkit run this matrix:

| Scenario | Configurations | Rounds | Why |
|---|---|---|---|
| Blue/Green Switchover | customer-baseline / v4 / v5 / v6 / v7 | 10 | Most-tested customer concern; reproduces original problem |
| Failover (writer→reader) | v4 / v5 / v6 / v7 | 10 | Faster than BG creation; tests Aurora's automatic failover |
| Reboot (writer in-place) | v4 / v5 / v6 / v7 | 10 | Cheapest scenario; baseline for "how fast does the writer come back" |

Each round of every scenario tests **4 client variants** in parallel:
* EC2 + wrapper 3.3.0
* EC2 + wrapper 4.0.0
* EKS + wrapper 3.3.0
* EKS + wrapper 4.0.0

So a single Blue/Green round produces 4 log files, and 10 rounds × 5 configs ≈ 200
data points just for Blue/Green.

## The workload

Faithfully reproduces the customer's production workload:

| Parameter | Value |
|---|---|
| Threads | 4 |
| Per-thread interval | 100 ms |
| Operation mix | read : insert : update = 9 : 2 : 1 |
| Aggregate throughput | ~ 40 ops/sec |
| Connection pool max | 10 (configurable per YAML) |
| Connection pool min idle | 5 (baseline) or 10 (v4) or 20 (v5) |

The mix is deliberately read-heavy because the customer's gateway service is
read-heavy. **Don't change this** for HSK comparisons; do change it if you're
testing a different customer with a different workload shape.

## Step-by-step: running one Blue/Green round

```bash
# 0. Set the password from Secrets Manager (NEVER hard-code)
export DB_PASSWORD="$(aws --profile jiasunm-neo --region us-east-1 \
    secretsmanager get-secret-value \
    --secret-id <your-aurora-master-secret-arn> \
    --query SecretString --output text | jq -r .password)"

# 1. Start clients (this returns immediately, processes run in background)
./scripts/run-test.sh \
    --endpoint test-04.cluster-xxx.us-east-1.rds.amazonaws.com \
    --config v4-current \
    --wrappers 3.3.0,4.0.0

# 2. Wait 60 seconds so we have a clean baseline of STATS lines
sleep 60

# 3. Trigger Blue/Green switchover (you must have created the BG deployment first)
aws --profile jiasunm-neo --region us-east-1 rds switchover-blue-green-deployment \
    --blue-green-deployment-identifier <bg-id> \
    --switchover-timeout 600

# 4. Wait 5 minutes for the workload to stabilise after switchover
sleep 300

# 5. Stop and analyse
./scripts/stop-test.sh
# This automatically runs analyze-logs.py on the run dir.

# 6. Aggregate all completed runs into the dashboard
python3 scripts/compare-runs.py e2e-results/ -o dashboard/data/runs.json
open dashboard/index.html
```

## Avoiding common methodological mistakes

### 1. Don't measure when warm-up is incomplete
The Hikari pool needs ~5-10 seconds to fully populate. Wait at least 60 seconds
of stable STATS lines before triggering the scenario, otherwise you'll attribute
warm-up latency to switchover.

### 2. Don't reuse the same Aurora cluster across configs without cleanup
A previous test's connections may still be in TCP TIME_WAIT, polluting the next
round's measurements. Either:
* Use a different cluster per config (recommended; 10 rounds = 10 clusters), or
* Wait at least 4 minutes between rounds on the same cluster

### 3. Run from EC2 in the same VPC, not from your laptop
Internet round-trip jitter (100–200 ms with high variance) will dominate your
4–7 second downtime measurements. **Always** run the client from an EC2 in the
same VPC and AZ as the writer.

### 4. Triple-check that customer-baseline is faithful
`customer-baseline.yaml` has `connectTimeout: null` deliberately. If you
"helpfully" add a value, you've stopped reproducing the customer's problem.
Run `mvn test` — `JdbcUrlBuilderTest.customerBaselineUrlOmitsConnectTimeout`
will catch this drift.

### 5. Don't average across scenarios
Blue/Green / Failover / Reboot have very different physics. Aggregate them
separately. The dashboard does this automatically (scenario field in meta.json
is preserved through to the final UI).

## What good results look like

| Config | Scenario | Expected median | Expected max | Expected stdev |
|---|---|---|---|---|
| customer-baseline | Blue/Green | 30–40 s | 50–57 s | ≥10 s (very unstable) |
| v4-current | Blue/Green | ~4 s | ≤7.6 s | <1.5 s |
| v5+ optimised | Blue/Green | <4 s | <6 s | <1 s |
| v4-current | Failover | ~10 s | ≤15 s | ~2 s |
| v4-current | Reboot | ~6 s | ≤8 s | <1 s |

If you see numbers significantly outside these ranges, **investigate the
environment**, don't blame the toolkit. Likely culprits: client running on a
different network, Aurora cluster size mismatch, MySQL-version mismatch.

## When to update the methodology

Re-run the regression test (`mvn -B verify`) whenever:
* You modify `LogParser` — must still re-derive 51.2s/56.3s from the fixture
* You modify a shipped YAML — `ShippedConfigsParseTest` enforces the schema
* You modify `JdbcUrlBuilder` — `JdbcUrlBuilderTest` enforces the URL invariants

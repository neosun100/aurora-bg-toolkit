# Experiment v11 — CDK-native, 5-Cluster Parallel

> **Started**: 2026-05-17 17:30 SGT  
> **Status**: planning → execution  
> **Goal**: re-run v10 (production-load reference) but with **CDK as the IaC**
> and **5 clusters in parallel** to compress wall time from 7-8h to 3-4h
> while validating the same numbers.

## Why v11 exists

The v10 audit (CHANGELOG `[post-experiment-audit]`) fixed v9's silent
low-load issue and produced the first clean production-load numbers. But
v10 had two limitations:

1. **Not really IaC** — orchestration was bash + aws-cli. Reproducible by
   the author, but not "clone the repo and run `cdk deploy`".
2. **Slow** — single cluster, single thread; BG provisioning (~22min/round)
   serializes 10 BG rounds into 4+ hours.

v11 fixes both:

- 3 CDK stacks (NetworkStack + ClusterStack×5 + ClientStack) — full IaC
- Python orchestrator (`infra/orchestrate-v11.py`) — replaces bash master
- 5 clusters in parallel, each does 6 measurements (2 BG + 2 FO + 2 RB)
- Wall time: ~3-4h instead of ~7-8h
- AWS cost: ~$8 instead of ~$5 (small price for half the time)

## Hypotheses to confirm

| ID | Hypothesis | Predicted outcome |
|---|---|---|
| H1 | v11 BG numbers match v10 within ±15% | median 5.0 ± 0.75s, max ≤ 25s |
| H2 | v11 Failover matches v10 within ±15% | median 7.7 ± 1.2s, max ≤ 18s |
| H3 | v11 Reboot matches v10 | median ≤ 200ms |
| H4 | 5-cluster parallel introduces no measurable interference | per-cluster stats indistinguishable from each other |
| H5 | CDK deploy + destroy is reliable across 7 stacks | 0 manual cleanup needed; teardown empty in <15min |

If H1-H3 fail by more than ±15%, that would suggest 5-cluster contention
on RDS control plane — a finding worth investigating but not invalidating
the methodology (the absolute numbers, not the comparison, are the value).

## Test matrix

```
1 NetworkStack (1 VPC + 1 SG + 1 subnet group + 1 keypair + 1 master secret)
× 5 ClusterStack (test-v11-1..5, each with writer r7g.large + reader t3.medium, port 4488)
× 1 ClientStack (1 EC2 c6i.2xlarge, drives all 5 clusters in parallel)

Each cluster does:
  2 BG rounds + 2 Failover rounds + 2 Reboot rounds = 6 measurements

5 × 6 = 30 measurements (matches v10).
```

## Phases (with CDK)

```
PHASE                ESTIMATED   MECHANISM       NOTES
─────────────────────────────────────────────────────────────────────
PRECHECK             ~5 s        python          AWS auth, CDK CLI, java, mvn
BUILD                ~30 s       mvn package -Pwrapper-4.1
CDK_BOOTSTRAP        ~1 min      cdk bootstrap   (one-time per account/region)
CDK_DEPLOY           ~12 min     cdk deploy --all  CFN deploys 7 stacks; cluster creation parallel
COLLECT_OUTPUTS      ~5 s        boto3           cluster endpoints, EC2 IP, secret ARN
EC2_PROVISION        ~30 s       SSH + scp       upload jar + configs to EC2
TEST_C{1..5}         ~3.5h each  ThreadPoolExec  5 clusters × 6 rounds, run in parallel
ANALYZE              ~30 s       extract → JSON
REPORT               ~10 s       template fill
CDK_DESTROY          ~12 min     cdk destroy --all  delete-target on BG, then stacks
─────────────────────────────────────────────────────────────────────
TOTAL                ~4-4.5h     wall (parallel TEST_C{1..5})
```

## Resumability

- Same `progress.json` checkpoint pattern as v10
- Keyed by `(cluster, scenario, round)` instead of just `(scenario, round)`
- `infra/state/v11-progress.json`
- master orchestrator can resume from any phase including mid-test
- 5 cluster threads each maintain their own progress in shared json (file-locked)

## Risk register

| Risk | Probability | Mitigation |
|------|-------------|------------|
| 5 clusters × 2 BG = 10 BG creates hits region quota | Low | per-cluster max 1 active BG; 10 < default quota 100 |
| EC2 OOM (5 java processes × Xmx2g = 10GB on 16GB box) | Medium | Xmx2g per process + monitor; reduce to Xmx1.5g if needed |
| CDK deploy partial failure (e.g. one ClusterStack fails) | Low | per-stack retry; manual cleanup via `cdk destroy AbtV11ClusterStack-N` |
| BG provisioning serial dep on cluster-pg in-sync | Medium | ParameterGroup attached at create-time, no separate reboot needed |
| Master password shared across 5 clusters → blast radius | Low | this is a test environment; secret destroyed at teardown |
| 5 java processes contention on EC2 NIC | Low | c6i.2xlarge has up to 12.5 Gbps NIC; 5 × ~50 Mbps workload << limit |

## CDK design decisions (recorded for the record)

1. **Default VPC** instead of new VPC — saves 5 min on every deploy/destroy cycle.
2. **CfnDBClusterParameterGroup** instead of `rds.ParameterGroup` — the L2
   construct doesn't expose `cluster_parameter_group_name` for cross-stack export.
3. **Manual master password via SecretsManager.fromSecretValue** — `Credentials.from_generated_secret` triggers `MasterUserSecret` which v10 audit proved is incompatible with BG Deployments. We create the Secret separately and read its `password` field at deploy time so the cluster has a fixed password (not a managed link to the secret).
4. **5 separate ClusterStack instances** instead of one stack with 5 clusters — CDK deploys independent stacks in parallel (CFN concurrency); same wall time, much cleaner per-stack rollback semantics.
5. **No cdk.context.json committed** — VPC lookup is account-specific; running on a different account regenerates it.

## Acceptance gates

1. `cdk synth` clean (✓ achieved 2026-05-17 17:30)
2. `cdk deploy --all` succeeds in ≤15 min
3. EC2 reaches Running + SSH-ready
4. All 5 clusters reach Available + writer/reader ready
5. Master secret retrievable via boto3
6. orchestrator successfully drives 30 measurements via 5 parallel threads
7. dashboard/data/v11-only.json contains 30 entries
8. docs/REPORTS/2026-05-17-v11-cdk-parallel.md auto-generated
9. `cdk destroy --all` leaves account empty (audit step)
10. README + CHANGELOG updated; v11 marked as recommended path

## What if v11 fails?

- **Partial failure** (e.g. 1 cluster fails to provision): orchestrator tags
  that cluster's measurements as "skipped", writes report with N=24 instead
  of 30. Clearly stated in the report.
- **CDK deploy fails**: `cdk destroy --all --force` and we fall back to v10
  bash path for any urgent re-runs.
- **Data quality issue** (e.g. 5 cluster contention shows as 30s outliers
  everywhere): document as a finding; v11 becomes the "do NOT use 5-cluster
  parallel for production-grade measurements" reference.

The v10 bash path remains intact under `infra/orchestrate-v10-master.sh`
as the reference implementation.

---

*Plan locked: 2026-05-17 17:35 SGT.*

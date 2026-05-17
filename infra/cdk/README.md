# CDK Skeleton — Future IaC Path

> Status: **skeleton / blueprint only**. The v10 experiment uses the
> battle-tested bash scripts (`infra/00..30-*.sh`). This directory is the
> starting point for the next iteration that will fully replace bash with
> CDK.

## What's here

```
infra/cdk/
├── README.md             # this file
├── app.py                # CDK app entrypoint
├── cdk.json              # CDK config (skeleton)
├── requirements.txt      # CDK Python deps
└── stacks/
    ├── __init__.py
    ├── network_stack.py  # VPC + subnet group + security group + EC2 key
    ├── cluster_stack.py  # Aurora cluster + writer + reader + parameter group
    ├── client_stack.py   # EC2 c6i.2xlarge runner with IAM role
    └── outputs.py        # Cross-stack outputs & tags
```

## Why this isn't done yet

Migrating the v10 orchestrator to CDK requires deciding **what's IaC and
what isn't**. v9 final report explained the constraint at length:

- **Persistent infrastructure** (VPC, subnet group, security group,
  parameter group, EC2 runner): a perfect fit for CDK. State is stable,
  drift is manageable, `cdk destroy` is tidy.
- **Test mutations** (BG creation/deletion, switchover, failover, reboot,
  cluster `-old*` cleanup): a poor fit. Each round mutates state. CDK's
  desired-state model fights this; you'd be running `cdk deploy --hotswap`
  with imperative `local-exec` constructs to do the actual mutation, which
  defeats the purpose.

The right design: **CDK for persistent infra; a thin Python orchestrator
for the test mutations**. The orchestrator imports outputs from CDK
(VPC ID, cluster ARN, EC2 IP) and uses boto3 directly for BG / failover /
reboot operations.

## Migration plan (for v11)

| Step | What | Effort |
|------|------|--------|
| 1 | Implement `network_stack.py` (replaces `00-bootstrap.sh`) | 1-2 h |
| 2 | Implement `cluster_stack.py` (replaces `10-create-cluster.sh` + `05-enable-bg-prereqs.sh`) | 2-3 h |
| 3 | Implement `client_stack.py` (replaces `20-create-ec2.sh`) | 1 h |
| 4 | Replace `infra/orchestrate-v10-master.sh` bash with `infra/orchestrate.py` (Python boto3) | 4-6 h |
| 5 | Add `Makefile` with targets: `make deploy`, `make run`, `make destroy` | 30 min |
| 6 | Add `.github/workflows/v11-experiment.yml` for CI-driven runs | 1 h |
| **Total** | | **~10-15 h** |

## Why we don't do it now

Two reasons:

1. **Time-to-result**: v10 experiment must produce data within 24h.
   CDK rewrite blocks data delivery for another half-day.
2. **De-risking**: bash scripts are validated by 200+ measurements across
   v1-v9. CDK rewrite introduces new failure modes that will only show up
   under production-grade load. Better to validate v10 first, then
   confidently migrate.

## When we DO do it

After v10 produces clean data, we treat the bash scripts as "reference
implementation" and port them to CDK constructs piece by piece.
The migration succeeds when:

- A new contributor can clone the repo and run `make deploy && make run`
  with no shell knowledge.
- All AWS resources are tagged with `Project=aurora-bg-toolkit, Cost=v11`
  for centralized cost allocation.
- `make destroy` leaves zero residue (current bash teardown leaves 3
  control-plane objects per audit).

## Quick CDK starter (when you're ready)

```bash
cd infra/cdk
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk bootstrap aws://ACCOUNT/us-east-1   # one-time
cdk synth                                # generate templates
cdk deploy '*'                           # deploy all stacks
# ... run experiments via separate orchestrator ...
cdk destroy '*' --force                  # tear down
```

The skeleton files below provide the right module structure but are
intentionally minimal — they document the resources we want to create
without committing implementation details that might not match v11's
needs.

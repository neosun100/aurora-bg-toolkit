#!/usr/bin/env python3
"""
Aurora BG Toolkit — CDK app entrypoint.

By default deploys the v11 measurement stacks (1 NetworkStack + 5 ClusterStack
+ 1 ClientStack). v16 adds an optional MatrixRunnerStack via env var.

Usage:
    cd infra/cdk
    source .venv/bin/activate         # uv venv .venv  (one-time)
    cdk synth                         # validate (default = ABT stacks only)
    cdk deploy --all                  # deploy 7 ABT stacks
    cdk destroy --all --force         # tear down

v16 matrix runner (one-time, deployed by launch-matrix.sh):
    INCLUDE_MATRIX_RUNNER=1 \\
    ABT_NOTIFICATION_EMAIL=you@example.com \\
    cdk deploy AbtV16MatrixRunnerStack --require-approval never

The orchestrator imports stack outputs via boto3 to drive measurements;
the matrix orchestrator (v16) drives full cdk deploy/destroy cycles per run.
"""
from __future__ import annotations

import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.cluster_stack import ClusterStack
from stacks.client_stack import ClientStack
from stacks.matrix_runner_stack import MatrixRunnerStack
from stacks.matrix_runner_stack_v17 import MatrixRunnerStack as MatrixRunnerStackV17


CLUSTER_COUNT = 5  # 5 parallel Aurora clusters

app = cdk.App()
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# 1) Shared network + security + master secret
network = NetworkStack(app, "AbtV11NetworkStack", env=env)

# 2) 5 Aurora clusters in parallel (CDK deploys them concurrently because
#    they only depend on NetworkStack, not on each other)
clusters = []
for i in range(1, CLUSTER_COUNT + 1):
    c = ClusterStack(
        app, f"AbtV11ClusterStack-{i}",
        env=env,
        cluster_index=i,
        vpc_id=cdk.Fn.import_value("AbtV11VpcId"),
        subnet_group_name=cdk.Fn.import_value("AbtV11SubnetGroupName"),
        sg_id=cdk.Fn.import_value("AbtV11SgId"),
        parameter_group_name=cdk.Fn.import_value("AbtV11ParameterGroupName"),
        master_secret_arn=cdk.Fn.import_value("AbtV11MasterSecretArn"),
    )
    c.add_dependency(network)
    clusters.append(c)

# 3) Single EC2 runner driving all 5 clusters in parallel via 5 java processes
client = ClientStack(
    app, "AbtV11ClientStack",
    env=env,
    vpc_id=cdk.Fn.import_value("AbtV11VpcId"),
    sg_id=cdk.Fn.import_value("AbtV11SgId"),
    key_name=cdk.Fn.import_value("AbtV11KeyName"),
    master_secret_arn=cdk.Fn.import_value("AbtV11MasterSecretArn"),
)
client.add_dependency(network)
# ClientStack does NOT depend on ClusterStacks: it can come up in parallel
# (the orchestrator only uses the EC2 after all clusters are AVAILABLE)

# Common tags
for stack in (network, *clusters, client):
    cdk.Tags.of(stack).add("project", "aurora-bg-toolkit")
    cdk.Tags.of(stack).add("experiment", "v11-cdk")

# 4) (v16) Optional MatrixRunnerStack — long-lived runner EC2 + S3 + SNS.
# Only deployed when INCLUDE_MATRIX_RUNNER=1 is set, so historical
# `cdk deploy --all` for v11/v12 measurements isn't affected.
if os.environ.get("INCLUDE_MATRIX_RUNNER", "0") == "1":
    runner = MatrixRunnerStack(
        app, "AbtV16MatrixRunnerStack",
        env=env,
        vpc_id=cdk.Fn.import_value("AbtV11VpcId"),
        sg_id=cdk.Fn.import_value("AbtV11SgId"),
        key_name=cdk.Fn.import_value("AbtV11KeyName"),
        notification_email=os.environ.get("ABT_NOTIFICATION_EMAIL", "").strip() or None,
    )
    runner.add_dependency(network)
    cdk.Tags.of(runner).add("project", "aurora-bg-toolkit")
    cdk.Tags.of(runner).add("experiment", "v16-matrix")

# 5) (v17) Optional MatrixRunnerStack v17 — reboot deep-dive re-validation.
# Same shape as v16 but writes to a different S3 bucket and SNS topic so the
# v16 raw artifacts are preserved as historical record.
if os.environ.get("INCLUDE_MATRIX_RUNNER_V17", "0") == "1":
    runner_v17 = MatrixRunnerStackV17(
        app, "AbtV17MatrixRunnerStack",
        env=env,
        vpc_id=cdk.Fn.import_value("AbtV11VpcId"),
        sg_id=cdk.Fn.import_value("AbtV11SgId"),
        key_name=cdk.Fn.import_value("AbtV11KeyName"),
        notification_email=os.environ.get("ABT_NOTIFICATION_EMAIL", "").strip() or None,
    )
    runner_v17.add_dependency(network)
    cdk.Tags.of(runner_v17).add("project", "aurora-bg-toolkit")
    cdk.Tags.of(runner_v17).add("experiment", "v17-reboot-deepdive")

app.synth()

#!/usr/bin/env python3
"""
Aurora BG Toolkit — CDK app entrypoint (v11).

Deploys a parallel-test environment for the v11 production-load experiment:
  - 1 NetworkStack    : VPC (default), SG, subnet group, key pair, parameter group, master secret
  - 5 ClusterStack    : 5 Aurora MySQL clusters (test-v11-1..5), each with writer + reader
  - 1 ClientStack     : 1 EC2 c6i.2xlarge runner that drives all 5 clusters in parallel

Usage:
    cd infra/cdk
    source .venv/bin/activate         # uv venv .venv  (one-time)
    cdk synth                         # validate
    cdk deploy --all --require-approval never   # deploy 7 stacks (NetworkStack first, others parallel)
    cdk destroy --all --force         # tear down

The orchestrator (`infra/orchestrate-v11.py`) imports these stack outputs via
boto3 to drive the actual measurement workload.
"""
from __future__ import annotations

import os
import aws_cdk as cdk

from stacks.network_stack import NetworkStack
from stacks.cluster_stack import ClusterStack
from stacks.client_stack import ClientStack


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

app.synth()

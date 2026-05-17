#!/usr/bin/env python3
"""
Aurora BG Toolkit — CDK app entrypoint (skeleton, v11 target).

Defines three stacks:
  * NetworkStack   — VPC, subnet group, security group, EC2 key pair
  * ClusterStack   — Aurora MySQL cluster + writer + reader + cluster param group
  * ClientStack    — c6i.2xlarge EC2 runner with IAM role + user-data

Usage (when CDK migration completes — not yet wired up for v10):
    cd infra/cdk
    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cdk bootstrap
    cdk deploy '*'

For v10, this is documentation only. v10 uses infra/orchestrate-v10-master.sh.
"""
from __future__ import annotations

import os

try:
    import aws_cdk as cdk
except ImportError:
    print("aws_cdk not installed. This is a skeleton — install only if you're migrating to CDK.")
    print("  pip install -r infra/cdk/requirements.txt")
    raise SystemExit(0)

from stacks.network_stack import NetworkStack
from stacks.cluster_stack import ClusterStack
from stacks.client_stack import ClientStack


app = cdk.App()
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

network = NetworkStack(app, "AbtNetworkStack", env=env)
cluster = ClusterStack(
    app, "AbtClusterStack",
    env=env,
    vpc=network.vpc,
    db_subnet_group=network.db_subnet_group,
    db_security_group=network.db_security_group,
)
client = ClientStack(
    app, "AbtClientStack",
    env=env,
    vpc=network.vpc,
    security_group=network.db_security_group,
    cluster_endpoint=cluster.endpoint,
    secret_arn=cluster.secret_arn,
    key_name=network.key_name,
)

# Common tags
for stack in (network, cluster, client):
    cdk.Tags.of(stack).add("project", "aurora-bg-toolkit")
    cdk.Tags.of(stack).add("experiment", "v11-cdk")

app.synth()

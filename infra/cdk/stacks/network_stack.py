"""
NetworkStack — shared infra for the v11 5-cluster experiment.

Creates ONCE, used by all 5 ClusterStacks + ClientStack:
  - References the default VPC (no new VPC creation; faster + cheaper)
  - 1 Security Group (self-referencing on Aurora port 4488 + SSH 22)
  - 1 DBSubnetGroup spanning all default subnets
  - 1 Cluster Parameter Group with binlog ON (BG prerequisite, applied at create-time
    so we don't need a separate "BG_PREREQS reboot writer" phase)
  - 1 EC2 KeyPair (private key auto-stored in Secrets Manager by AWS)
  - 1 SecretsManager Secret with the Aurora master password (admin/<random32>),
    NOT linked to any cluster (so BG Deployments still work — see v10 audit)

All outputs are exported via CfnOutput so the other stacks (and the orchestrator)
can pick them up via Fn.importValue or DescribeStacks.
"""
from __future__ import annotations

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as sm,
)


class NetworkStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # 1) Lookup default VPC (avoids creating a new one)
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # 2) Security Group: Aurora port 4488 from-self + SSH 22 (from anywhere)
        sg = ec2.SecurityGroup(
            self, "AbtV11Sg",
            vpc=vpc,
            description="Aurora BG Toolkit v11 - Aurora 4488 (self) + SSH 22 (any)",
            allow_all_outbound=True,
            security_group_name="abt-v11-sg",
        )
        sg.add_ingress_rule(
            peer=sg,
            connection=ec2.Port.tcp(4488),
            description="Aurora 4488 (self-referencing)",
        )
        sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(22),
            description="SSH from anywhere (test environment)",
        )

        # 3) DB subnet group — covers all default subnets
        subnet_group = rds.SubnetGroup(
            self, "AbtV11SubnetGroup",
            description="Aurora BG Toolkit v11 shared subnets",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            subnet_group_name="abt-v11-subnets",
        )

        # 4) Cluster Parameter Group with binlog ON (BG prerequisite)
        # Use the low-level Cfn construct because the high-level ParameterGroup
        # doesn't expose `cluster_parameter_group_name` cleanly for cross-stack export.
        param_group = rds.CfnDBClusterParameterGroup(
            self, "AbtV11ParamGroup",
            db_cluster_parameter_group_name="abt-v11-mysql8-bg",
            family="aurora-mysql8.0",
            description="Aurora BG Toolkit v11 - BG prerequisites enabled",
            parameters={
                "aurora_enhanced_binlog": "1",
                "binlog_backup": "0",
                "binlog_replication_globaldb": "0",
                "binlog_format": "ROW",
                "binlog_row_image": "FULL",
                "binlog_row_metadata": "FULL",
            },
        )

        # 5) EC2 Key Pair — CDK creates it server-side; private key goes to
        #    Secrets Manager (AWS native KeyPair behaviour).
        key_pair = ec2.KeyPair(
            self, "AbtV11KeyPair",
            key_pair_name="abt-v11-key",
            type=ec2.KeyPairType.ED25519,
        )

        # 6) Master password (NOT a managed secret — see v10 audit:
        #    `--manage-master-user-password` is incompatible with BG Deployments).
        #    We store a generated password and the cluster reads it at create-time
        #    via SecretValue.from_secrets_manager(...).
        master_secret = sm.Secret(
            self, "AbtV11MasterSecret",
            secret_name="abt-v11-master-secret",
            description="Aurora BG Toolkit v11 master password (admin user)",
            generate_secret_string=sm.SecretStringGenerator(
                secret_string_template='{"username":"admin"}',
                generate_string_key="password",
                exclude_characters=' /@"\'\\$%`',  # avoid shell + URL escapes
                password_length=32,
            ),
        )

        # ──────────────── Outputs (exported for cross-stack ref) ────────────────
        cdk.CfnOutput(self, "VpcId",
                      value=vpc.vpc_id,
                      export_name="AbtV11VpcId")
        cdk.CfnOutput(self, "SgId",
                      value=sg.security_group_id,
                      export_name="AbtV11SgId")
        cdk.CfnOutput(self, "SubnetGroupName",
                      value=subnet_group.subnet_group_name,
                      export_name="AbtV11SubnetGroupName")
        cdk.CfnOutput(self, "ParameterGroupName",
                      value=param_group.ref,
                      export_name="AbtV11ParameterGroupName")
        cdk.CfnOutput(self, "KeyName",
                      value=key_pair.key_pair_name,
                      export_name="AbtV11KeyName")
        cdk.CfnOutput(self, "KeyPairId",
                      value=key_pair.key_pair_id,
                      export_name="AbtV11KeyPairId")
        cdk.CfnOutput(self, "MasterSecretArn",
                      value=master_secret.secret_arn,
                      export_name="AbtV11MasterSecretArn")

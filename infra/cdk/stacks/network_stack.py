"""
NetworkStack — replaces infra/00-bootstrap.sh.

Creates:
  * Default VPC reference (existing) OR purpose-built VPC (your choice for v11)
  * DB Subnet Group spanning all default-for-az subnets
  * Security Group allowing 4488 within itself + 22 from anywhere
  * EC2 key pair (private key uploaded to AWS Secrets Manager for retrieval)

This is a SKELETON. Not deployed for v10. Implement when ready to migrate.
"""
from __future__ import annotations

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
)


class NetworkStack(cdk.Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # Use default VPC for parity with bash scripts
        self.vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # Security group for both DB cluster and EC2 runner
        self.db_security_group = ec2.SecurityGroup(
            self, "AbtSg",
            vpc=self.vpc,
            description="Aurora BG Toolkit test traffic",
            allow_all_outbound=True,
        )
        self.db_security_group.add_ingress_rule(
            peer=self.db_security_group,
            connection=ec2.Port.tcp(4488),
            description="Aurora port (self-referencing)",
        )
        self.db_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(22),
            description="SSH from anywhere (production should restrict!)",
        )

        # DB subnet group — covers all default subnets
        self.db_subnet_group = rds.SubnetGroup(
            self, "AbtSubnetGroup",
            description="Aurora BG Toolkit shared subnets",
            vpc=self.vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # EC2 key pair (CDK creates and stores private key in Secrets Manager)
        self.key_name = "abt-test-key-v11"
        # Note: KeyPair construct is in cdk.aws_ec2 but the API is unstable.
        # Real implementation: use `ec2.CfnKeyPair` and read the resulting
        # private key from CloudFormation outputs OR pre-create via console.

        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id)
        cdk.CfnOutput(self, "SecurityGroupId", value=self.db_security_group.security_group_id)
        cdk.CfnOutput(self, "DbSubnetGroupName", value=self.db_subnet_group.subnet_group_name)

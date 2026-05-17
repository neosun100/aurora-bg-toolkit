"""
ClientStack — replaces infra/20-create-ec2.sh.

Creates:
  * c6i.2xlarge EC2 with Amazon Linux 2023 + Java 17 pre-installed
  * IAM role with secretsmanager:GetSecretValue + rds:Describe* + rds switchover/failover/reboot
  * Instance profile attached
  * SSH-accessible public IP
  * User-data installs java-17-amazon-corretto and creates work dirs

This is a SKELETON. Not deployed for v10. Implement when ready to migrate.
"""
from __future__ import annotations

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
)


class ClientStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc: ec2.IVpc,
        security_group: ec2.ISecurityGroup,
        cluster_endpoint: str,
        secret_arn: str,
        key_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # IAM role
        role = iam.Role(
            self, "AbtClientRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Aurora BG Toolkit test client",
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
            resources=[secret_arn],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=[
                "rds:DescribeDBClusters",
                "rds:DescribeDBInstances",
                "rds:DescribeBlueGreenDeployments",
                "rds:SwitchoverBlueGreenDeployment",
                "rds:CreateBlueGreenDeployment",
                "rds:DeleteBlueGreenDeployment",
                "rds:FailoverDBCluster",
                "rds:RebootDBInstance",
            ],
            resources=["*"],
        ))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -e",
            "yum update -y",
            "yum install -y java-17-amazon-corretto-headless jq",
            "mkdir -p /home/ec2-user/aurora-bg-toolkit/{configs,e2e-results}",
            "chown -R ec2-user:ec2-user /home/ec2-user/aurora-bg-toolkit",
            "echo 'cdk user-data complete' >> /var/log/abt-bootstrap.log",
        )

        # AMI: Amazon Linux 2023, x86_64
        ami = ec2.MachineImage.latest_amazon_linux2023()

        instance = ec2.Instance(
            self, "AbtClient",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.C6I, ec2.InstanceSize.XLARGE2),
            machine_image=ami,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=security_group,
            role=role,
            key_name=key_name,
            user_data=user_data,
            associate_public_ip_address=True,
        )

        cdk.CfnOutput(self, "InstanceId", value=instance.instance_id)
        cdk.CfnOutput(self, "PublicIp", value=instance.instance_public_ip)
        cdk.CfnOutput(self, "ClusterEndpointForJob", value=cluster_endpoint)
        cdk.CfnOutput(self, "SecretArnForJob", value=secret_arn)

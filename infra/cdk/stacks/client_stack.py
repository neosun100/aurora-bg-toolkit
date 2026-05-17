"""
ClientStack — single c6i.2xlarge EC2 runner that drives all 5 clusters in parallel.

Creates:
  - IAM Role with rds:* (Describe/Switchover/Failover/Reboot/Create/Delete BG)
    + secretsmanager:GetSecretValue (for the v11 master secret)
  - Instance profile attached
  - c6i.2xlarge EC2 with Amazon Linux 2023
  - User-data installs java-17, jq, mysql client, and creates work dirs
  - Public IP assigned
  - SSH key from NetworkStack (abt-v11-key)

The orchestrator (`infra/orchestrate-v11.py`) SSHs to this EC2 and starts
5 java processes in parallel, each driving one cluster.
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
        vpc_id: str,
        sg_id: str,
        key_name: str,
        master_secret_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # Resolve imports
        vpc = ec2.Vpc.from_lookup(self, "ImportedVpc", is_default=True)
        sg = ec2.SecurityGroup.from_security_group_id(self, "ImportedSg", sg_id)

        # IAM role for the EC2
        role = iam.Role(
            self, "AbtV11ClientRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Aurora BG Toolkit v11 test client",
            role_name="abt-v11-client-role",
        )
        role.add_to_policy(iam.PolicyStatement(
            actions=[
                "secretsmanager:GetSecretValue",
                "secretsmanager:DescribeSecret",
            ],
            resources=[master_secret_arn],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=[
                "rds:DescribeDBClusters",
                "rds:DescribeDBInstances",
                "rds:DescribeBlueGreenDeployments",
                "rds:CreateBlueGreenDeployment",
                "rds:DeleteBlueGreenDeployment",
                "rds:SwitchoverBlueGreenDeployment",
                "rds:FailoverDBCluster",
                "rds:RebootDBInstance",
                "rds:ListTagsForResource",
                "rds:AddTagsToResource",
                # for cleanup of -old1 instances/clusters left by BG --delete-target
                "rds:DeleteDBInstance",
                "rds:DeleteDBCluster",
            ],
            resources=["*"],
        ))

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -e",
            "yum update -y",
            "yum install -y java-17-amazon-corretto-headless jq mariadb105",
            # workdir
            "mkdir -p /home/ec2-user/aurora-bg-toolkit/{configs,e2e-results}",
            "chown -R ec2-user:ec2-user /home/ec2-user/aurora-bg-toolkit",
            "echo 'cdk user-data complete: '$(date -u)" " >> /var/log/abt-v11-bootstrap.log",
        )

        # AMI: Amazon Linux 2023, x86_64
        ami = ec2.MachineImage.latest_amazon_linux2023()

        instance = ec2.Instance(
            self, "AbtV11Client",
            instance_name="abt-v11-client",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.C6I, ec2.InstanceSize.XLARGE2
            ),
            machine_image=ami,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_group=sg,
            role=role,
            key_name=key_name,
            user_data=user_data,
            associate_public_ip_address=True,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=30,  # 30 GiB; java logs from 5 parallel processes can be large
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                    ),
                ),
            ],
        )

        # ──────────────── Outputs ────────────────
        cdk.CfnOutput(self, "InstanceId",
                      value=instance.instance_id,
                      export_name="AbtV11ClientInstanceId")
        cdk.CfnOutput(self, "PublicIp",
                      value=instance.instance_public_ip,
                      export_name="AbtV11ClientPublicIp")
        cdk.CfnOutput(self, "RoleArn",
                      value=role.role_arn,
                      export_name="AbtV11ClientRoleArn")

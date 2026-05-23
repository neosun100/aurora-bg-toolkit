"""
MatrixRunnerStack — long-lived EC2 + S3 + SNS for unattended matrix sweep.

The point of this stack is to **decouple the matrix orchestrator from the
user's laptop**. When deployed:
  - The matrix runner EC2 stays up 24/7 (until user manually destroys)
  - It runs `orchestrate-matrix.py` as a systemd service
  - That orchestrator drives `cdk deploy/destroy` of the AbtV11* stacks
    for each run in the matrix
  - All progress.json + master.log files sync to S3 every 30 seconds
  - SNS topic emits emails on key events (start, run-complete, failure, all-done)

The user's laptop only needs to:
  1. Run `bash infra/launch-matrix.sh` once (deploys this stack + uploads code)
  2. Walk away. Optionally check progress later via S3/email.
  3. Come back when SNS says "matrix complete" and pull the report.

Resources:
  - 1 EC2 t3.small (Amazon Linux 2023): 24/7, ~$0.02/h
  - 1 IAM role with: cdk* + ec2:* + rds:* + s3:* + sns:* + cloudformation:*
  - 1 S3 bucket: abt-v16-state-{account}: holds progress.json, master.log,
    dashboard.html, per-run data JSONs, final report
  - 1 SNS topic: abt-v16-events: subscriber emails configured via env var
  - Reuses NetworkStack VPC + SG + key pair so it can SSH into the
    AbtV11Client EC2 with the same key

Cost: ~$0.50 for a 24h matrix sweep window.

NOTE: This stack uses the SAME IAM permissions and bucket model as production
test infrastructure, so be careful not to deploy it in unrelated accounts.
"""
from __future__ import annotations

import os

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_iam as iam,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subs,
)


class MatrixRunnerStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc_id: str,
        sg_id: str,
        key_name: str,
        notification_email: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # ── 1. State bucket (per-account, deterministic name) ──
        # Private bucket — account-level S3 Block Public Access prevents
        # public bucket policies. Users access dashboard.html and
        # progress.json via `aws s3 cp` (which they have IAM access for).
        # The runner EC2 keeps the bucket up-to-date via boto3.
        state_bucket = s3.Bucket(
            self, "AbtV16StateBucket",
            bucket_name=f"abt-v16-state-{self.account}",
            removal_policy=cdk.RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=False,
            lifecycle_rules=[
                s3.LifecycleRule(
                    expiration=cdk.Duration.days(30),
                    abort_incomplete_multipart_upload_after=cdk.Duration.days(1),
                ),
            ],
        )

        # ── 2. SNS topic ──
        topic = sns.Topic(
            self, "AbtV16EventsTopic",
            topic_name="abt-v16-events",
            display_name="Aurora BG Toolkit v16 Matrix Sweep",
        )
        if notification_email:
            topic.add_subscription(sns_subs.EmailSubscription(notification_email))

        # ── 3. IAM role for the runner EC2 ──
        role = iam.Role(
            self, "AbtV16RunnerRole",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            description="Aurora BG Toolkit v16 matrix runner - full automation",
            role_name="abt-v16-runner-role",
        )
        # CDK + CloudFormation: deploy/destroy stacks
        role.add_to_policy(iam.PolicyStatement(
            actions=[
                "cloudformation:*",
                "ec2:*",
                "rds:*",
                "iam:GetRole", "iam:PassRole", "iam:CreateRole",
                "iam:AttachRolePolicy", "iam:PutRolePolicy",
                "iam:DetachRolePolicy", "iam:DeleteRolePolicy",
                "iam:DeleteRole", "iam:GetRolePolicy",
                "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile",
                "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile",
                "secretsmanager:*",
                "ssm:GetParameter", "ssm:GetParameters",
                "logs:*",
                "kms:Decrypt", "kms:DescribeKey",
            ],
            resources=["*"],
        ))
        # CDK bootstrap-stage SSM
        role.add_to_policy(iam.PolicyStatement(
            actions=["sts:AssumeRole"],
            resources=[f"arn:aws:iam::{self.account}:role/cdk-*"],
        ))
        # Cost watch (auto-pause if blowing budget)
        role.add_to_policy(iam.PolicyStatement(
            actions=["ce:GetCostAndUsage", "budgets:ViewBudget"],
            resources=["*"],
        ))
        # State bucket + topic
        state_bucket.grant_read_write(role)
        topic.grant_publish(role)

        # ── 4. EC2 user-data: minimal, just installs deps. The launch
        # script (infra/launch-matrix.sh) scp's the toolkit code afterwards.
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -e",
            "exec > /var/log/abt-v16-runner-userdata.log 2>&1",
            "yum update -y",
            # Core tooling
            "yum install -y java-17-amazon-corretto git jq tar gzip mariadb105 python3-pip",
            # Maven (yum has it but old; use the AL2023 default)
            "yum install -y maven",
            # AWS CLI v2 (already on AL2023, but ensure latest)
            "yum install -y awscli",
            # Node.js (CDK CLI needs it)
            "curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -",
            "yum install -y nodejs",
            # CDK CLI globally
            "npm install -g aws-cdk@2",
            # Python deps for orchestrator
            "pip3 install boto3 pyyaml",
            # uv for CDK Python venv (keeps consistent with local dev)
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "ln -sf /root/.local/bin/uv /usr/local/bin/uv",
            # Workdir
            "mkdir -p /opt/abt && chown ec2-user:ec2-user /opt/abt",
            "echo 'runner user-data complete: '$(date -u) >> /var/log/abt-v16-runner-userdata.log",
        )

        # Resolve imports
        vpc = ec2.Vpc.from_lookup(self, "ImportedVpc", is_default=True)
        sg = ec2.SecurityGroup.from_security_group_id(self, "ImportedSg", sg_id)

        # ── 5. Runner EC2 (t3.small is plenty — orchestrator is mostly
        # idle waiting on RDS and SSH) ──
        instance = ec2.Instance(
            self, "AbtV16Runner",
            instance_name="abt-v16-runner",
            instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.SMALL),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
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
                        volume_size=30,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                        delete_on_termination=True,
                    ),
                ),
            ],
        )

        # ── 6. Outputs ──
        cdk.CfnOutput(self, "RunnerInstanceId",
                      value=instance.instance_id,
                      export_name="AbtV16RunnerInstanceId")
        cdk.CfnOutput(self, "RunnerPublicIp",
                      value=instance.instance_public_ip,
                      export_name="AbtV16RunnerPublicIp")
        cdk.CfnOutput(self, "StateBucketName",
                      value=state_bucket.bucket_name,
                      export_name="AbtV16StateBucketName")
        cdk.CfnOutput(self, "TopicArn",
                      value=topic.topic_arn,
                      export_name="AbtV16TopicArn")
        cdk.CfnOutput(self, "DashboardUrl",
                      value=f"s3://{state_bucket.bucket_name}/dashboard.html",
                      description="S3 URI for dashboard.html (use `aws s3 cp` to download + `open`)")

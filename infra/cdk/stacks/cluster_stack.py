"""
ClusterStack — one Aurora MySQL cluster (parameterized by index 1..5).

v16 update: writer + reader instance class are now parameterized via env vars
so the matrix sweep can stand up r7g.large / 2xlarge / 4xlarge / 8xlarge
clusters without code changes.

Each instance creates:
  - 1 cluster: test-v11-{idx}
  - 1 writer (instance class from env, default r7g.large)
  - 1 reader (instance class from env, default t3.medium)
  - aurora-iopt1 storage, port 4488, engine 8.0.mysql_aurora.3.10.4

The cluster is created with a manual master password (read at deploy time
from the shared SecretsManager secret produced by NetworkStack). This avoids
the v10 audit finding that `ManageMasterUserPassword` is incompatible with
Blue/Green Deployments.

Other shared resources (subnet group, security group, parameter group) are
imported by name from NetworkStack outputs.
"""
from __future__ import annotations

import os

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as sm,
)


# Parse "r7g.large" → (InstanceClass.R7G, InstanceSize.LARGE)
_INSTANCE_CLASS_MAP = {
    "t3": ec2.InstanceClass.T3,
    "t4g": ec2.InstanceClass.T4G,
    "r5": ec2.InstanceClass.R5,
    "r6g": ec2.InstanceClass.R6G,
    "r6i": ec2.InstanceClass.R6I,
    "r7g": ec2.InstanceClass.R7G,
    "r7i": ec2.InstanceClass.R7I,
    "r8g": ec2.InstanceClass.R8G,
    "m5": ec2.InstanceClass.M5,
    "m6g": ec2.InstanceClass.M6G,
    "m6i": ec2.InstanceClass.M6I,
    "m7g": ec2.InstanceClass.M7G,
    "c6i": ec2.InstanceClass.C6I,
    "c7i": ec2.InstanceClass.C7I,
}

_INSTANCE_SIZE_MAP = {
    "nano": ec2.InstanceSize.NANO,
    "micro": ec2.InstanceSize.MICRO,
    "small": ec2.InstanceSize.SMALL,
    "medium": ec2.InstanceSize.MEDIUM,
    "large": ec2.InstanceSize.LARGE,
    "xlarge": ec2.InstanceSize.XLARGE,
    "xlarge2": ec2.InstanceSize.XLARGE2,
    "2xlarge": ec2.InstanceSize.XLARGE2,
    "xlarge4": ec2.InstanceSize.XLARGE4,
    "4xlarge": ec2.InstanceSize.XLARGE4,
    "xlarge8": ec2.InstanceSize.XLARGE8,
    "8xlarge": ec2.InstanceSize.XLARGE8,
    "xlarge12": ec2.InstanceSize.XLARGE12,
    "12xlarge": ec2.InstanceSize.XLARGE12,
    "xlarge16": ec2.InstanceSize.XLARGE16,
    "16xlarge": ec2.InstanceSize.XLARGE16,
}


def _parse_instance_type(spec: str, default: ec2.InstanceType) -> ec2.InstanceType:
    """Parse 'r7g.2xlarge' → ec2.InstanceType.of(R7G, XLARGE2). Falls back to default."""
    if not spec or "." not in spec:
        return default
    cls_str, size_str = spec.split(".", 1)
    cls = _INSTANCE_CLASS_MAP.get(cls_str.lower())
    size = _INSTANCE_SIZE_MAP.get(size_str.lower())
    if cls is None or size is None:
        return default
    return ec2.InstanceType.of(cls, size)


class ClusterStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        cluster_index: int,
        vpc_id: str,
        subnet_group_name: str,
        sg_id: str,
        parameter_group_name: str,
        master_secret_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        cluster_id = f"test-v11-{cluster_index}"

        # ── Read instance classes from env vars (v16 matrix sweep) ──
        # Default values match v11 historical experiment (writer r7g.large + reader t3.medium).
        writer_instance_type = _parse_instance_type(
            os.environ.get("V11_WRITER_INSTANCE", "r7g.large"),
            default=ec2.InstanceType.of(ec2.InstanceClass.R7G, ec2.InstanceSize.LARGE),
        )
        reader_instance_type = _parse_instance_type(
            os.environ.get("V11_READER_INSTANCE", "t3.medium"),
            default=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
        )

        # Resolve cross-stack imports back into L2 constructs
        vpc = ec2.Vpc.from_lookup(self, "ImportedVpc", is_default=True)
        sg = ec2.SecurityGroup.from_security_group_id(self, "ImportedSg", sg_id)
        master_secret = sm.Secret.from_secret_complete_arn(
            self, "ImportedSecret", master_secret_arn
        )

        # The cluster — manual password from the shared secret (NOT managed)
        cluster = rds.DatabaseCluster(
            self, f"V11Cluster{cluster_index}",
            cluster_identifier=cluster_id,
            engine=rds.DatabaseClusterEngine.aurora_mysql(
                version=rds.AuroraMysqlEngineVersion.of(
                    "8.0.mysql_aurora.3.10.4", "8.0"
                ),
            ),
            credentials=rds.Credentials.from_password(
                "admin",
                master_secret.secret_value_from_json("password"),
            ),
            default_database_name="demo",
            port=4488,
            parameter_group=rds.ParameterGroup.from_parameter_group_name(
                self, "ImportedParamGroup", parameter_group_name
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[sg],
            storage_type=rds.DBClusterStorageType.AURORA_IOPT1,
            backup=rds.BackupProps(retention=cdk.Duration.days(1)),
            removal_policy=cdk.RemovalPolicy.DESTROY,
            deletion_protection=False,
            writer=rds.ClusterInstance.provisioned(
                "Writer",
                instance_identifier=f"{cluster_id}-writer",
                instance_type=writer_instance_type,
            ),
            readers=[
                rds.ClusterInstance.provisioned(
                    "Reader",
                    instance_identifier=f"{cluster_id}-reader",
                    instance_type=reader_instance_type,
                    promotion_tier=15,  # never auto-promoted
                ),
            ],
        )

        # Tag for easy discovery + teardown
        cdk.Tags.of(cluster).add("cluster", cluster_id)
        cdk.Tags.of(cluster).add("v11_index", str(cluster_index))
        cdk.Tags.of(cluster).add(
            "v16_writer_class",
            os.environ.get("V11_WRITER_INSTANCE", "r7g.large"),
        )

        # ──────────────── Outputs ────────────────
        cdk.CfnOutput(self, f"ClusterEndpoint{cluster_index}",
                      value=cluster.cluster_endpoint.hostname,
                      export_name=f"AbtV11Cluster{cluster_index}Endpoint",
                      description=f"Cluster endpoint for {cluster_id}")
        cdk.CfnOutput(self, f"ClusterIdentifier{cluster_index}",
                      value=cluster_id,
                      export_name=f"AbtV11Cluster{cluster_index}Id")
        cdk.CfnOutput(self, f"ClusterArn{cluster_index}",
                      value=cluster.cluster_arn,
                      export_name=f"AbtV11Cluster{cluster_index}Arn")

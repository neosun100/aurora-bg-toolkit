"""
ClusterStack — one Aurora MySQL cluster (parameterized by index 1..5).

Each instance creates:
  - 1 cluster: test-v11-{idx}
  - 1 writer: test-v11-{idx}-writer (db.r7g.large)
  - 1 reader: test-v11-{idx}-reader (db.t3.medium)
  - aurora-iopt1 storage, port 4488, engine 8.0.mysql_aurora.3.10.4

The cluster is created with a manual master password (read at deploy time
from the shared SecretsManager secret produced by NetworkStack). This avoids
the v10 audit finding that `ManageMasterUserPassword` is incompatible with
Blue/Green Deployments.

Other shared resources (subnet group, security group, parameter group) are
imported by name from NetworkStack outputs.
"""
from __future__ import annotations

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_secretsmanager as sm,
)


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

        # Resolve cross-stack imports back into L2 constructs
        vpc = ec2.Vpc.from_lookup(self, "ImportedVpc", is_default=True)
        sg = ec2.SecurityGroup.from_security_group_id(self, "ImportedSg", sg_id)
        # NB: parameter_group is referenced by name in the CFN ref; we don't need
        # an L2 import here. Same for subnet_group: use vpc + vpc_subnets selection.
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
                instance_type=ec2.InstanceType.of(
                    ec2.InstanceClass.R7G, ec2.InstanceSize.LARGE
                ),
            ),
            readers=[
                rds.ClusterInstance.provisioned(
                    "Reader",
                    instance_identifier=f"{cluster_id}-reader",
                    instance_type=ec2.InstanceType.of(
                        ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM
                    ),
                    promotion_tier=15,  # never auto-promoted
                ),
            ],
        )

        # Tag for easy discovery + teardown
        cdk.Tags.of(cluster).add("cluster", cluster_id)
        cdk.Tags.of(cluster).add("v11_index", str(cluster_index))

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

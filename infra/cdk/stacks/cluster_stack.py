"""
ClusterStack — replaces infra/10-create-cluster.sh + infra/05-enable-bg-prereqs.sh.

Creates:
  * Aurora MySQL cluster (v8.0.mysql_aurora.3.10.4, port 4488)
  * Writer instance (db.r7g.large)
  * Reader instance (db.t3.medium)
  * Cluster parameter group with binlog flags pre-enabled (BG prereq)
  * Master password stored in Secrets Manager (auto-rotation off for test)

This is a SKELETON. Not deployed for v10. Implement when ready to migrate.
"""
from __future__ import annotations

from constructs import Construct
import aws_cdk as cdk
from aws_cdk import (
    aws_ec2 as ec2,
    aws_rds as rds,
)


class ClusterStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        vpc: ec2.IVpc,
        db_subnet_group: rds.ISubnetGroup,
        db_security_group: ec2.ISecurityGroup,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, **kwargs)

        # Cluster parameter group with BG prerequisites pre-applied.
        # ⚠️ aurora_enhanced_binlog requires reboot to apply; CDK will do
        # this automatically on first attach.
        param_group = rds.ParameterGroup(
            self, "AbtClusterParamGroup",
            engine=rds.DatabaseClusterEngine.aurora_mysql(
                version=rds.AuroraMysqlEngineVersion.of("8.0.mysql_aurora.3.10.4"),
            ),
            description="Aurora BG Toolkit — BG prerequisites enabled",
            parameters={
                "aurora_enhanced_binlog": "1",
                "binlog_backup": "0",
                "binlog_replication_globaldb": "0",
                "binlog_format": "ROW",
                "binlog_row_image": "FULL",
                "binlog_row_metadata": "FULL",
            },
        )

        cluster = rds.DatabaseCluster(
            self, "AbtCluster",
            engine=rds.DatabaseClusterEngine.aurora_mysql(
                version=rds.AuroraMysqlEngineVersion.of("8.0.mysql_aurora.3.10.4"),
            ),
            cluster_identifier="test-v11",
            credentials=rds.Credentials.from_generated_secret("admin"),
            default_database_name="demo",
            port=4488,
            parameter_group=param_group,
            subnet_group=db_subnet_group,
            security_groups=[db_security_group],
            storage_type=rds.DBClusterStorageType.AURORA_IOPT1,
            backup=rds.BackupProps(retention=cdk.Duration.days(1)),
            removal_policy=cdk.RemovalPolicy.DESTROY,
            deletion_protection=False,
            writer=rds.ClusterInstance.provisioned(
                "Writer",
                instance_type=ec2.InstanceType.of(ec2.InstanceClass.R7G, ec2.InstanceSize.LARGE),
            ),
            readers=[
                rds.ClusterInstance.provisioned(
                    "Reader",
                    instance_type=ec2.InstanceType.of(ec2.InstanceClass.T3, ec2.InstanceSize.MEDIUM),
                    promotion_tier=15,  # never auto-promoted
                ),
            ],
        )

        self.endpoint = cluster.cluster_endpoint.hostname
        self.secret_arn = cluster.secret.secret_arn

        cdk.CfnOutput(self, "ClusterEndpoint", value=self.endpoint)
        cdk.CfnOutput(self, "ClusterSecretArn", value=self.secret_arn)

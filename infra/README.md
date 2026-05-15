# Infrastructure scripts for the AWS E2E test plan.
#
# These scripts spin up the resources defined in docs/METHODOLOGY.md against
# the configured AWS account / region. They are deliberately written as
# small, idempotent shell scripts (not CDK / Terraform) because:
#
#   1. Easier to read line-by-line for a SA engagement
#   2. Easier to copy-tweak-rerun without state files
#   3. Easier to teardown completely (no orphan Terraform state)
#
# State is captured in `state/` after creation:
#   state/<resource-name>.env   — bash-sourceable variables
#   state/<resource-name>.json  — full describe output
#
# Order of operations:
#   1. ./00-bootstrap.sh             — DB subnet group, security group, IAM
#   2. ./10-create-cluster.sh test-01 customer-baseline
#      (repeat for test-02..test-05 with different config names)
#   3. ./20-create-ec2.sh            — test-client EC2 in same VPC
#   4. ./15-create-bg-deployment.sh test-01
#      (repeat for each cluster, only when ready to run the BG scenario)
#   5. orchestrate.sh                — runs 10 rounds of BG / Failover / Reboot
#   6. ./99-teardown.sh              — destroy everything
#
# All scripts use these env vars (set by 00-bootstrap.sh):
#   AWS_PROFILE         default jiasunm-neo
#   AWS_REGION          default us-east-1
#   ABT_VPC_ID          discovered (default VPC)
#   ABT_SG_ID           created by bootstrap
#   ABT_DB_SUBNET_GROUP created by bootstrap
#   ABT_KEY_NAME        created by bootstrap (EC2 key pair)

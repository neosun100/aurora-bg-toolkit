#!/usr/bin/env bash
# 20-create-ec2.sh — launch the test-client EC2 instance.
#
# Single c6i.2xlarge in the same VPC as the Aurora clusters, with:
#   * Java 17 (Amazon Corretto)
#   * The aurora-bg-toolkit fat-jar(s) for both wrapper 3.3.0 and 4.0.0
#   * IAM role with secretsmanager:GetSecretValue + rds:Describe* permissions
#
# After this script returns, you can SSH:
#   ssh -i infra/state/abt-test-key.pem ec2-user@<public-ip>

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_DIR="$REPO_ROOT/infra/state"
source "$STATE_DIR/bootstrap.env"

aws_() { aws --profile "$AWS_PROFILE" --region "$AWS_REGION" "$@"; }

INSTANCE_NAME="abt-test-client"

# 1) Create or look up an IAM role + instance profile for the EC2
ROLE_NAME="abt-test-client-role"
PROFILE_NAME="abt-test-client-profile"

if ! aws_ iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
    aws_ iam create-role --role-name "$ROLE_NAME" \
        --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
    aws_ iam put-role-policy --role-name "$ROLE_NAME" --policy-name "abt-test-client-perms" \
        --policy-document '{
            "Version":"2012-10-17",
            "Statement":[
                {"Effect":"Allow","Action":["secretsmanager:GetSecretValue","secretsmanager:DescribeSecret"],"Resource":"*"},
                {"Effect":"Allow","Action":["rds:DescribeDBClusters","rds:DescribeDBInstances","rds:DescribeBlueGreenDeployments"],"Resource":"*"},
                {"Effect":"Allow","Action":["rds:SwitchoverBlueGreenDeployment","rds:FailoverDBCluster","rds:RebootDBInstance"],"Resource":"*"}
            ]
        }' >/dev/null
    echo "iam role:        $ROLE_NAME (created)"
fi

if ! aws_ iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
    aws_ iam create-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null
    aws_ iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" --role-name "$ROLE_NAME" >/dev/null
    echo "instance profile:$PROFILE_NAME (created)"
    sleep 10  # IAM eventual consistency
fi

# 2) Pick a default subnet
SUBNET_ID=$(aws_ ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$ABT_VPC_ID" "Name=default-for-az,Values=true" \
    --query 'Subnets[0].SubnetId' --output text)

# 3) Skip if a tagged instance already exists
EXISTING=$(aws_ ec2 describe-instances \
    --filters "Name=tag:Name,Values=$INSTANCE_NAME" "Name=instance-state-name,Values=pending,running" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
if [[ -n "$EXISTING" ]]; then
    echo "ec2:             $EXISTING (existing, reusing)"
    INSTANCE_ID="$EXISTING"
else
    INSTANCE_ID=$(aws_ ec2 run-instances \
        --image-id "$ABT_AMI_ID" \
        --instance-type c6i.2xlarge \
        --key-name "$ABT_KEY_NAME" \
        --security-group-ids "$ABT_SG_ID" \
        --subnet-id "$SUBNET_ID" \
        --iam-instance-profile "Name=$PROFILE_NAME" \
        --associate-public-ip-address \
        --tag-specifications "ResourceType=instance,Tags=[{Key=project,Value=aurora-bg-toolkit},{Key=Name,Value=$INSTANCE_NAME}]" \
        --user-data '#!/bin/bash
set -e
yum update -y
yum install -y java-17-amazon-corretto-headless jq
# user-data runs as root; create the toolkit dir under ec2-user
mkdir -p /home/ec2-user/aurora-bg-toolkit/{configs,e2e-results}
chown -R ec2-user:ec2-user /home/ec2-user/aurora-bg-toolkit
echo "user-data complete: $(date -u)" >> /var/log/abt-bootstrap.log
' \
        --query 'Instances[0].InstanceId' --output text)
    echo "ec2:             $INSTANCE_ID (created)"
fi

aws_ ec2 wait instance-running --instance-ids "$INSTANCE_ID"
PUBLIC_IP=$(aws_ ec2 describe-instances --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

cat > "$STATE_DIR/ec2.env" <<EOF
export ABT_EC2_INSTANCE_ID="$INSTANCE_ID"
export ABT_EC2_PUBLIC_IP="$PUBLIC_IP"
export ABT_EC2_USER="ec2-user"
EOF

echo
echo "EC2 ready:"
echo "  instance: $INSTANCE_ID"
echo "  public:   $PUBLIC_IP"
echo "  ssh:      ssh -i $ABT_KEY_FILE ec2-user@$PUBLIC_IP"

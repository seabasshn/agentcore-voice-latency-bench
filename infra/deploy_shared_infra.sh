#!/usr/bin/env bash
# Deploy shared infrastructure and seed DynamoDB tables.
# DO NOT run this script against a production account without review.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Deploying CloudFormation stack voice-bench-shared-infra ..."
aws cloudformation deploy \
  --template-file "${SCRIPT_DIR}/shared-infra.yaml" \
  --stack-name voice-bench-shared-infra \
  --capabilities CAPABILITY_NAMED_IAM \
  --tags project=voice-agent-latency-bench \
  --region us-east-1 \
  --profile voice-bench

echo "==> Seeding DynamoDB tables ..."
AWS_PROFILE=voice-bench python3 "${SCRIPT_DIR}/seed_data.py"

echo "==> Done."

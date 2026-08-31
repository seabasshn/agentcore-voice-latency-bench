#!/usr/bin/env bash
# Deploy voice-bench-lambda-tools CloudFormation stack.
# Prerequisites:
#   - AWS CLI configured with profile "voice-bench" (region us-east-1)
#   - shared-infra stack (voice-bench-shared-infra) deployed first so the IAM
#     role voice-bench-lambda-exec-role exists in account 111122223333
# Usage: bash infra/deploy_lambda_tools.sh
set -euo pipefail

TEMPLATE="infra/lambda-tools.yaml"
STACK_NAME="voice-bench-lambda-tools"
REGION="us-east-1"
PROFILE="voice-bench"

# Optional: validate before deploying
echo "[validate] Checking template syntax..."
aws cloudformation validate-template \
  --template-body "file://${TEMPLATE}" \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --output text

echo "[deploy] Deploying stack ${STACK_NAME}..."
aws cloudformation deploy \
  --template-file "${TEMPLATE}" \
  --stack-name "${STACK_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --tags project=voice-agent-latency-bench \
  --region "${REGION}" \
  --profile "${PROFILE}"

echo "[done] Stack ${STACK_NAME} deployed."
echo ""
echo "Alias ARNs (PC=5) — use for warm/provisioned benchmark:"
aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --query "Stacks[0].Outputs[?contains(OutputKey,'AliasArn')].{Key:OutputKey,Value:OutputValue}" \
  --output table

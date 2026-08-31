#!/usr/bin/env bash
# Create/refresh the container-deployed runtime `voice-bench-agent` + its extra endpoints.
# Requires: GATEWAY_MCP_URL env var (from the gateway deploy summary).
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-voice-bench}"
REGION=us-east-1
ACCOUNT=111122223333
# NOTE: agentRuntimeName must match [a-zA-Z][a-zA-Z0-9_]{0,47} (no hyphens).
NAME=voice_bench_agent
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/voice-bench-runtime-role"
ECR_URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/voice-bench-agent:latest"
MODEL_ID="us.anthropic.claude-haiku-4-5-20251001-v1:0"
: "${GATEWAY_MCP_URL:?set GATEWAY_MCP_URL from the gateway deploy summary}"

ENV_JSON=$(printf '{"BEDROCK_MODEL_ID":"%s","GATEWAY_MCP_URL":"%s"}' "$MODEL_ID" "$GATEWAY_MCP_URL")

# Create if absent, else update.
RID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
      --query "agentRuntimes[?agentRuntimeName=='$NAME'].agentRuntimeId | [0]" --output text 2>/dev/null)

if [ "$RID" = "None" ] || [ -z "$RID" ]; then
  echo "creating runtime $NAME ..."
  aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
    --agent-runtime-name "$NAME" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$ECR_URI\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":900}' \
    --environment-variables "$ENV_JSON" \
    --tags project=voice-agent-latency-bench
else
  echo "updating runtime $NAME ($RID) ..."
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
    --agent-runtime-id "$RID" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$ECR_URI\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":900}' \
    --environment-variables "$ENV_JSON"
fi

RID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
      --query "agentRuntimes[?agentRuntimeName=='$NAME'].agentRuntimeId | [0]" --output text)
echo "runtime id: $RID"

# Extra endpoints (DEFAULT is auto-created).
for EP in voice_bench_ep_2 voice_bench_ep_3; do
  if ! aws bedrock-agentcore-control get-agent-runtime-endpoint --region "$REGION" \
        --agent-runtime-id "$RID" --endpoint-name "$EP" >/dev/null 2>&1; then
    echo "creating endpoint $EP ..."
    aws bedrock-agentcore-control create-agent-runtime-endpoint --region "$REGION" \
      --agent-runtime-id "$RID" --name "$EP" --tags project=voice-agent-latency-bench || true
  fi
done
echo "done. ARN:"
aws bedrock-agentcore-control get-agent-runtime --region "$REGION" --agent-runtime-id "$RID" \
  --query 'agentRuntimeArn' --output text

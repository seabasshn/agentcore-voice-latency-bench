#!/usr/bin/env bash
# Package the same agent as a ZIP and deploy it as the code (ZIP) runtime
# `voice-bench-agent-code` (codeConfiguration, PYTHON_3_12) for the container-vs-code
# cold-start comparison (T5). entryPoint is validated empirically at deploy.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-voice-bench}"
REGION=us-east-1
ACCOUNT=111122223333
# NOTE: agentRuntimeName must match [a-zA-Z][a-zA-Z0-9_]{0,47} (no hyphens).
NAME=voice_bench_agent_code
ROLE_ARN="arn:aws:iam::$ACCOUNT:role/voice-bench-runtime-role"
BUCKET="voice-bench-code-$ACCOUNT"
MODEL_ID="us.anthropic.claude-haiku-4-5-20251001-v1:0"
: "${GATEWAY_MCP_URL:?set GATEWAY_MCP_URL from the gateway deploy summary}"

cd "$(dirname "$0")/.."   # -> runtime/
BUILD=build/code_pkg
rm -rf "$BUILD" && mkdir -p "$BUILD"
cp -r agent "$BUILD/agent"
cp agent/main.py "$BUILD/main.py"        # flat entry: `from agent import agent_core` resolves at zip root
cp requirements.txt "$BUILD/requirements.txt"

# AgentCore code runtime executes from /var/task WITHOUT installing requirements.txt,
# so vendor the dependencies into the package as Linux/arm64 wheels (match Graviton).
echo "vendoring Linux/arm64 dependencies into the code package ..."
finch run --rm --platform linux/arm64 -v "$PWD/$BUILD:/pkg" -w /pkg python:3.12-slim \
  sh -c "pip install --no-cache-dir -r requirements.txt -t . >/tmp/vbpip.log 2>&1 && python -c 'import bedrock_agentcore, strands, boto3; print(\"vendored deps import OK\")'"

( cd "$BUILD" && zip -qr ../code_pkg.zip . -x '*__pycache__*' -x '*.pyc' -x '*.dist-info/RECORD' )
echo "zip size: $(du -h ../"$BUILD"/../code_pkg.zip 2>/dev/null | cut -f1 || du -h build/code_pkg.zip | cut -f1)"

# S3 bucket for code artifacts.
aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null || \
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" >/dev/null
aws s3api put-bucket-tagging --bucket "$BUCKET" \
  --tagging 'TagSet=[{Key=project,Value=voice-agent-latency-bench}]' >/dev/null 2>&1 || true
KEY="code/code_pkg.zip"
aws s3 cp build/code_pkg.zip "s3://$BUCKET/$KEY" >/dev/null
echo "uploaded s3://$BUCKET/$KEY"

ENV_JSON=$(printf '{"BEDROCK_MODEL_ID":"%s","GATEWAY_MCP_URL":"%s"}' "$MODEL_ID" "$GATEWAY_MCP_URL")
ARTIFACT=$(printf '{"codeConfiguration":{"code":{"s3":{"bucket":"%s","prefix":"%s"}},"runtime":"PYTHON_3_12","entryPoint":["main.py"]}}' "$BUCKET" "$KEY")

RID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
      --query "agentRuntimes[?agentRuntimeName=='$NAME'].agentRuntimeId | [0]" --output text 2>/dev/null)
if [ "$RID" = "None" ] || [ -z "$RID" ]; then
  echo "creating code runtime $NAME ..."
  aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
    --agent-runtime-name "$NAME" \
    --agent-runtime-artifact "$ARTIFACT" \
    --role-arn "$ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":900}' \
    --environment-variables "$ENV_JSON" \
    --tags project=voice-agent-latency-bench
else
  echo "updating code runtime $NAME ($RID) ..."
  aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
    --agent-runtime-id "$RID" --agent-runtime-artifact "$ARTIFACT" --role-arn "$ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --lifecycle-configuration '{"idleRuntimeSessionTimeout":900}' \
    --environment-variables "$ENV_JSON"
fi
aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?agentRuntimeName=='$NAME'].agentRuntimeArn | [0]" --output text

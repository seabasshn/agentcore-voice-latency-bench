#!/usr/bin/env bash
# Build the ARM64 AgentCore Runtime image and push to ECR.
set -euo pipefail
export AWS_PROFILE="${AWS_PROFILE:-voice-bench}"
ACCOUNT=111122223333
REGION=us-east-1
REPO=voice-bench-agent
URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO"
TAG="${1:-v1}"

cd "$(dirname "$0")/.."   # -> runtime/

# Ensure ECR repo (scan on push, tagged).
aws ecr describe-repositories --repository-names "$REPO" --region "$REGION" >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name "$REPO" --region "$REGION" \
    --image-scanning-configuration scanOnPush=true \
    --tags Key=project,Value=voice-agent-latency-bench >/dev/null

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"

# Ensure a buildx builder exists.
docker buildx inspect voicebench >/dev/null 2>&1 || docker buildx create --name voicebench --use >/dev/null
docker buildx use voicebench

docker buildx build --platform linux/arm64 \
  -t "$URI:$TAG" -t "$URI:latest" --push .

echo "pushed $URI:latest ($TAG)"
docker buildx imagetools inspect "$URI:latest" | grep -iE 'platform|arm64' | head

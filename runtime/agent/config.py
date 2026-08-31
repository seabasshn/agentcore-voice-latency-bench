"""Runtime configuration, sourced from environment variables.

Set at runtime-create time via `--environment-variables`. Non-sensitive values only.
The Gateway uses AWS_IAM (SigV4) inbound auth, so the agent signs its MCP calls
with the runtime's injected IAM credentials; no OAuth/Cognito secret is needed.
"""
import os

# Bedrock model (Haiku 4.5 inference profile).
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# AgentCore Gateway (MCP) — the agent calls tools through this endpoint.
GATEWAY_MCP_URL = os.environ.get("GATEWAY_MCP_URL", "")  # https://<id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp

SYSTEM_PROMPT = """You are a flight cancellation agent. When a caller wants to cancel:
1. Get their reservation using their booking ID
2. Check eligibility
3. Look up the refund method
4. Explain the refund clearly in 1-2 sentences (this will be spoken aloud)
5. If confirmed, execute the cancellation
Keep responses concise. You are in a voice context with a 2-second latency target."""

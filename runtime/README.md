# AgentCore Runtime — voice-bench-agent

Flight-cancellation agent deployed to Amazon Bedrock AgentCore Runtime, invoked by
the benchmark client. Two deployments share this one code base:

- **`voice-bench-agent`** — container deployment (ARM64 image in ECR).
- **`voice-bench-agent-code`** — code (ZIP) deployment (`codeConfiguration`, `PYTHON_3_12`).

## Contract

HTTP protocol. `BedrockAgentCoreApp` serves `POST /invocations` + `GET /ping` on `:8080`.

| Payload | Response |
|---|---|
| `{"type":"warmup"}` | `{"status":"warm","init_ms":<float>}` — builds MCP session + Strands agent |
| `{"type":"ping"}` | `{"status":"ready","session_init_ms":<float>}` — session overhead only |
| `{"message":"..."}` | `{"response","timing":{...},"usage":{...},"session_id"}` |
| `{"message":"...","stream":true}` + `Accept: text/event-stream` | SSE token stream; final event has `timing`+`usage` |

`timing.tools` carries per-tool `{name,start_ms,end_ms,duration_ms}`; `parallel_tools`
is true if any two tool intervals overlap (T15). `ttft_ms` is set on the streaming path.

## Tools

Exposed to the model as local `@tool` functions that call the **AgentCore Gateway**
(`voice-bench-tools`, MCP, AWS_IAM) via SigV4 — i.e. Agent → Gateway → Lambda, the real
path. Timing is captured in the tool wrappers.

## Environment variables (set at runtime-create time; non-sensitive only)

- `BEDROCK_MODEL_ID` (default `us.anthropic.claude-haiku-4-5-20251001-v1:0`)
- `AWS_REGION` (`us-east-1`)
- `GATEWAY_MCP_URL` — the gateway `/mcp` URL

## Build & deploy

See `scripts/` (populated during the deploy phase with the resolved ECR URI, runtime
role ARN, and gateway URL). Local smoke test (needs a deployed gateway + creds):

```bash
docker run --platform linux/arm64 -p 8080:8080 \
  -e GATEWAY_MCP_URL=<url> -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... -e AWS_SESSION_TOKEN=... \
  voice-bench-agent:latest
curl localhost:8080/ping
curl -XPOST localhost:8080/invocations -d '{"type":"ping"}'
```

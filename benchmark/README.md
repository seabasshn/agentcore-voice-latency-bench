# Voice-Agent Latency Benchmark

End-to-end latency benchmark harness for the AgentCore Runtime voice POC.
Covers T1–T16: Lambda PC, cold starts, pre-warm, E2E, streaming TTFT, multi-turn, burst, parallel tools, and Connect hop.

## Prerequisites

```bash
pip install boto3
aws configure --profile voice-bench   # or set AWS_PROFILE env var
```

## Setup

1. Copy `benchmark/config.example.json` to `benchmark/config.json`.
2. Fill every `<PLACEHOLDER>` value (ARNs, gateway URLs) after deploying infra.
3. The `cost_rates` block contains tunable pricing constants — update from AWS pricing pages if rates change.

## Running

```bash
# All tests (default 20 iters each, 5 for T9/T13)
python3 benchmark/runner.py --config benchmark/config.json

# Specific tests
python3 benchmark/runner.py --tests T1,T2,T9 --iterations 10

# Custom output dir + timestamp
python3 benchmark/runner.py --output-dir results --timestamp 20251101T120000
```

Results are written to:
- `results/voice-bench-raw-<timestamp>.json`  — all raw samples
- `results/voice-bench-summary-<timestamp>.txt` — human-readable report

## Test Matrix

| ID  | What it measures                                      | Default iters | Target          |
|-----|-------------------------------------------------------|---------------|-----------------|
| T1  | Lambda PC warm (Qualifier=live)                       | 20            | p50 < 100ms     |
| T2  | Lambda $LATEST cold baseline                          | 20            | baseline        |
| T3  | Gateway MCP tool calls vs T1 overhead                 | 20            | —               |
| T4  | AgentCore container cold start                        | 20            | —               |
| T5  | AgentCore code-deploy cold start                      | 20            | —               |
| T6  | Pre-warm: warmup+2s → ping                            | 20            | p50 < 500ms     |
| T7  | Round-robin pings 3 endpoints, new sessions           | 20            | —               |
| T8  | Same-session 20 pings after warmup                    | 20            | p50 < 200ms     |
| T9  | Full E2E cancel BK-001 w/ and w/o prewarm             | 5             | p50 < 2000ms    |
| T10 | Burst 15 concurrent new-session pings                 | 15 conc.      | warm pool count |
| T11 | Streaming TTFB (SSE); replaces impossible WS test     | 20            | —               |
| T12 | Streaming TTFT                                        | 20            | p50 < 1500ms    |
| T13 | Multi-turn: turn2 on warm session                     | 5             | p50 < 1500ms    |
| T14 | Pre-warm curve: varied wait [0.5,1,2,5,10]s           | 10/wait       | —               |
| T15 | Parallel vs sequential tools (BK-001 cancel)          | 20            | —               |
| T16 | Connect-sim Lambda hop vs direct (skip if uncfg'd)    | 20            | —               |

## Config Keys to Fill After Deploy

| Key path | Description |
|---|---|
| `agent_runtimes.voice-bench-agent-container.arn` | ARN for container-based runtime (T4) |
| `agent_runtimes.voice-bench-agent-code.arn` | ARN for code-based runtime (T5) |
| `endpoints.DEFAULT.agent_runtime_arn` | ARN for DEFAULT endpoint (T7) |
| `endpoints.voice-bench-ep-2.agent_runtime_arn` | ARN for endpoint 2 (T7) |
| `endpoints.voice-bench-ep-3.agent_runtime_arn` | ARN for endpoint 3 (T7) |
| `primary_runtime.arn` | ARN used for T6/T8/T9/T11/T12/T13/T15 |
| `gateway.mcp_url` | Gateway MCP endpoint URL (T3) |
| `gateway.auth` | `AWS_IAM` (Gateway uses SigV4; no OAuth token needed) |
| `gateway.gateway_id` | Gateway id |
| `connect_sim_lambda` | Connect-sim Lambda function name (T16, optional) |

T3 and T16 are auto-skipped with a clear message if their config keys remain as placeholders.

## Notes

- **T11**: AgentCore Runtime has no client-side WebSocket API. T11 uses `accept: text/event-stream` SSE over `invoke_agent_runtime` and measures time-to-first-byte of the streaming response.
- **Session IDs**: Generated as 64-char hex (two uuid4 concatenated) to satisfy the >= 33 char requirement.
- **Percentiles**: Nearest-rank, implemented without numpy in `benchmark/stats.py`.
- **Cost model**: Rates are in `config.json#cost_rates`. AgentCore Runtime pricing is not yet GA-published; `agentcore_idle_per_hour` and `agentcore_active_per_invocation` default to 0 — fill after pricing is announced.

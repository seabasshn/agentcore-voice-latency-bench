# Architecture

## Overview

This POC benchmarks the latency profile of a voice-agent flight-cancellation
flow built on AWS AgentCore Runtime, Lambda, and Bedrock.

## Call flow

```
Benchmark harness
  │  InvokeAgentRuntime (HTTPS, server-sent streaming)
  ▼
AgentCore Runtime
  │  Model: us.anthropic.claude-haiku-4-5-20251001-v1:0
  │  Sessions: voice-bench-agent (container), voice-bench-agent-code (code)
  │  Role:     voice-bench-runtime-role
  │
  │  [tool call]
  ▼
AgentCore Gateway  (voice-bench-tools)
  │  Role: voice-bench-gateway-role
  │
  ├─► Lambda: voice-bench-get-reservation
  │     reads  DynamoDB: voice-bench-reservations
  │
  ├─► Lambda: voice-bench-check-eligibility
  │     reads  DynamoDB: voice-bench-reservations
  │
  ├─► Lambda: voice-bench-get-refund-method
  │     reads  DynamoDB: voice-bench-refund-rules
  │
  └─► Lambda: voice-bench-execute-cancellation
        writes DynamoDB: voice-bench-reservations (status update)

All Lambdas: Python 3.12, ARM64/Graviton, role voice-bench-lambda-exec-role.
```

## Data model

### voice-bench-reservations
PK `booking_id` (S). Attributes: `passenger_name`, `flight_date`,
`fare_type`, `payment_method`, `pax_count`, `has_checked_bags`,
`already_traveled`, `is_group_booking`, `linked_minor`.

### voice-bench-refund-rules
PK `fare_type` (S), SK `payment_method` (S). Attributes: `refund_method`,
`timeline`, `transferable`, `expiry_note`.

## T11 reframing

The original claim T11 ("WebSocket time-to-first-byte") has been reframed to:

> **T11 — InvokeAgentRuntime streaming response time-to-first-byte:**
> The first response chunk from AgentCore Runtime arrives within 500 ms of the
> `InvokeAgentRuntime` API call.

**Rationale:** AgentCore Runtime does not expose a client-facing WebSocket on
its data plane. The correct data-plane API is `InvokeAgentRuntime` (HTTPS),
which supports server-to-client response streaming. The benchmark harness
records the wall-clock time from request dispatch to receipt of the first
streamed response chunk.

## IAM roles

| Role | Assumed by | Purpose |
|---|---|---|
| voice-bench-lambda-exec-role | lambda.amazonaws.com | Lambda execution + DynamoDB read |
| voice-bench-gateway-role | bedrock-agentcore.amazonaws.com | Invoke Lambda tools |
| voice-bench-runtime-role | bedrock-agentcore.amazonaws.com | Invoke Bedrock model + AgentCore ops |

## Tags

All resources carry tag `project=voice-agent-latency-bench`.

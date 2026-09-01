# agentcore-voice-latency-bench

Latency benchmark for the **backend** of a multi-tool voice agent on Amazon Bedrock AgentCore. Companion code for the article *"Where a voice agent's 2-second budget goes: benchmarking a Bedrock AgentCore backend."* (article link to be added)

It answers one question: can a pre-warmed AgentCore Runtime plus Lambda tools fit a 2-second-per-turn budget for a cancellation-style IVR flow? Short version: session cold start is not the problem, the sequential LLM rounds are.

> **Scope.** This measures the agent compute path only: text in, text out, over `InvokeAgentRuntime`. There is no Amazon Connect, telephony, ASR, or TTS. It is a small benchmark (tens of iterations per test), run from a laptop, at one point in time. Treat the numbers as directional and re-measure for your own workload.

## Headline results (us-east-1, Claude Haiku 4.5)

| Question | Result |
|---|---|
| Session cold start, pre-warmed | 224 ms p50 (down from 1,019 ms cold); under the 500 ms target |
| Full 3-tool turn, pre-warmed | ~4,800 ms p50, TTFT ~4,400 ms; roughly 2.5x a 2 s budget |
| Where the time goes | ~4.6 s is three sequential LLM rounds; tools are ~0.5 s |
| Provisioned Concurrency | No measurable difference for a warm, trivial handler |
| Strands vs LangGraph | A tie (~4.6 to 4.9 s); both parallelize tools; Strands used ~7 to 13% more tokens |
| Container vs code/ZIP cold start | ~1.0 s vs ~4.8 s |
| MCP Gateway hop | Adds ~175 to 300 ms per tool call |
| Backend cost at 1,000 calls/day | ~$290/mo, excluding AgentCore Runtime consumption; verify current pricing |

Full analysis: [`results/FINDINGS.md`](results/FINDINGS.md) and [`results/langgraph-comparison/COMPARISON.md`](results/langgraph-comparison/COMPARISON.md).

## Architecture (what this benchmarks)

```
Python client (boto3, HTTPS)
  |   InvokeAgentRuntime  (text in, text out)
  v
AgentCore Runtime   (Strands or LangGraph agent, container; Claude Haiku 4.5)
  |
  v
AgentCore Gateway   (MCP, AWS_IAM / SigV4 inbound auth)
  |
  v
4 x Lambda (ARM64, 512 MB)
  |
  v
DynamoDB
```

In production this backend would sit behind an Amazon Connect voice frontend (IVR, ASR, TTS). That frontend is not part of this benchmark. The Gateway uses AWS_IAM (SigV4) inbound auth, so the runtime and client sign MCP calls with their AWS credentials, with no Cognito to stand up.

## Repository layout

```
infra/        CloudFormation (DynamoDB, IAM, Lambda) + seed data + Gateway deploy
runtime/      Agent code (Strands + LangGraph), Dockerfile, deploy scripts
benchmark/    T1-T16 harness, report + cost model, Connect-integration Lambda
results/      Raw run data, findings, and the Strands vs LangGraph comparison
docs/         Architecture notes
```

## The 16 tests

| # | Test | # | Test |
|---|---|---|---|
| T1 | Lambda tool, Provisioned Concurrency | T9 | Full end-to-end turn (with/without pre-warm) |
| T2 | Lambda tool, `$LATEST` | T10 | Burst of 15 concurrent sessions |
| T3 | Tool via MCP Gateway | T11 | Streaming first-byte |
| T4 | Runtime cold start, container | T12 | Streaming time-to-first-token |
| T5 | Runtime cold start, code/ZIP | T13 | Multi-turn, 2nd turn on warm session |
| T6 | Pre-warmed session ping | T14 | Pre-warm timing curve (0.5 to 10 s) |
| T7 | Round-robin across 3 endpoints | T15 | Parallel vs sequential tools |
| T8 | Warm same-session ping | T16 | Integration Lambda hop vs direct |

## Prerequisites

- An AWS account with Amazon Bedrock access to Claude Haiku 4.5 in `us-east-1`.
- Python 3.12 and AWS CLI v2.
- Docker or [Finch](https://github.com/runfinch/finch) for the ARM64 container build.
- AWS credentials available under a profile named `voice-bench` (use your own mechanism, for example `aws sso login`).

## Reproduce

1. Choose an AWS account and use `us-east-1`.
2. Replace the placeholder account id `111122223333` with yours across the tree, and confirm the Haiku 4.5 inference-profile id for your account.
3. Put credentials in a `voice-bench` profile.
4. Deploy in order:
   - `infra/shared-infra.yaml` (DynamoDB, IAM roles) then `python3 infra/seed_data.py`
   - `infra/lambda-tools.yaml` (the 4 tool Lambdas + Provisioned Concurrency)
   - `python3 infra/gateway/deploy_gateway.py` (MCP Gateway + Lambda targets)
   - `runtime/scripts/build_and_push.sh` then `runtime/scripts/deploy_runtime_container.sh` (and `package_and_deploy_code.sh` for the code/ZIP variant)
5. Fill `benchmark/config.json` with the resulting ARNs (see `benchmark/config.example.json`) and run `python3 benchmark/runner.py`.

Each subdirectory has its own README with details.

## Caveats

- Small sample sizes (5 to 20 iterations per test), so treat sub-second gaps as noise.
- The client runs from a laptop (~70 ms RTT to `us-east-1`); an in-region caller would see lower absolute numbers.
- Cost and pricing constants must be verified against current Bedrock and Lambda pricing before you rely on them.
- Backend only. ASR, TTS, and telephony are additive and not measured here.

## Disclaimer

This is a personal, experimental project. It is not official AWS guidance, and the results are point-in-time measurements from a small benchmark. Verify against your own environment and current pricing.

## License

MIT. See [LICENSE](LICENSE).

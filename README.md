# voice-bench

Companion code for the blog post *"Where a voice agent's 2-second budget goes: benchmarking a Bedrock AgentCore backend."*

Measures the latency of a multi-tool agent backend built on:

- **Amazon Bedrock AgentCore Runtime** (container deployment, Strands + LangGraph variants)
- **AgentCore Gateway** (MCP, AWS_IAM / SigV4 inbound auth)
- **AWS Lambda** tools (ARM64, 512 MB, Provisioned Concurrency)
- **DynamoDB** as the backing store
- **Claude Haiku 4.5** on Bedrock

## Layout

```
infra/       CloudFormation + seed data + Gateway deploy
runtime/     Agent code (Strands + LangGraph) + Dockerfile + deploy scripts
benchmark/   T1-T16 harness + report + cost model + Connect-integration Lambda
results/     Raw run data + findings + comparison
docs/        Architecture notes
```

## Reproduce

1. Pick an AWS account and region (`us-east-1`).
2. Replace the placeholder account id `111122223333` with yours across the tree.
3. Get AWS credentials into a profile named `voice-bench` (via your own credential process, e.g. `aws sso login`).
4. Deploy in order: `infra/shared-infra.yaml` → `infra/lambda-tools.yaml` → `infra/gateway/deploy_gateway.py` → container build (`runtime/scripts/build_and_push.sh`) → runtime create (`runtime/scripts/deploy_runtime_container.sh`).
5. Fill `benchmark/config.json` with the resulting ARNs and run `python3 benchmark/runner.py`.

Full walkthrough is in `docs/architecture.md` and each subdirectory's README.

## Caveats

This is a **small benchmark** (tens of iterations per test), invoked as text over `InvokeAgentRuntime`. No Amazon Connect, ASR, or TTS is involved. See the blog post for scope and method.

## License

MIT.

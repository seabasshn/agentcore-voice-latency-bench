# Shared Infrastructure

CloudFormation template and seed script for the voice-bench POC.

## Prerequisites

- AWS profile `voice-bench` configured in `~/.aws/config` (account 111122223333, us-east-1).
- Python 3.12 with `boto3` installed.

## Deploy

```bash
bash infra/deploy_shared_infra.sh
```

This runs two steps:

1. `aws cloudformation deploy` — creates/updates the `voice-bench-shared-infra` stack
   (DynamoDB tables + IAM roles).
2. `python3 infra/seed_data.py` — populates the tables (idempotent).

## Deploy only CloudFormation

```bash
aws cloudformation deploy \
  --template-file infra/shared-infra.yaml \
  --stack-name voice-bench-shared-infra \
  --capabilities CAPABILITY_NAMED_IAM \
  --tags project=voice-agent-latency-bench \
  --region us-east-1 \
  --profile voice-bench
```

## Seed only

```bash
AWS_PROFILE=voice-bench python3 infra/seed_data.py
```

## Validate template (read-only)

```bash
export AWS_PROFILE=voice-bench AWS_REGION=us-east-1
aws cloudformation validate-template \
  --template-body file://infra/shared-infra.yaml \
  --region us-east-1
```

## Stack outputs (exported for the lambda-tools stack)

| Export name | Contents |
|---|---|
| `voice-bench-ReservationsTableName` | DynamoDB table name |
| `voice-bench-ReservationsTableArn`  | DynamoDB table ARN |
| `voice-bench-RefundRulesTableName`  | DynamoDB table name |
| `voice-bench-RefundRulesTableArn`   | DynamoDB table ARN |
| `voice-bench-LambdaExecRoleArn`     | Lambda execution role ARN |
| `voice-bench-GatewayRoleArn`        | AgentCore Gateway role ARN |
| `voice-bench-RuntimeRoleArn`        | AgentCore Runtime role ARN |

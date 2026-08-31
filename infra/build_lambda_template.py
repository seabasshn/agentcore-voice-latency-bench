#!/usr/bin/env python3
"""
build_lambda_template.py — regenerate infra/lambda-tools.yaml from standalone .py files.

Usage (from repo root):
    python3 infra/build_lambda_template.py

Reads:
    infra/lambda/get_reservation.py
    infra/lambda/check_eligibility.py
    infra/lambda/get_refund_method.py
    infra/lambda/execute_cancellation.py

Writes:
    infra/lambda-tools.yaml  (overwrites)

NOTE: This script generates the template header and resource stubs but embeds
the handler source verbatim. Edit the .py files; run this script; commit both.
The .py files are the single source of truth.
"""
import sys
from pathlib import Path

HERE = Path(__file__).parent
LAMBDA_DIR = HERE / "lambda"
OUTPUT = HERE / "lambda-tools.yaml"

ROLE_ARN = "arn:aws:iam::111122223333:role/voice-bench-lambda-exec-role"
RESERVATIONS_TABLE = "voice-bench-reservations"
REFUND_RULES_TABLE = "voice-bench-refund-rules"

FUNCTIONS = [
    {
        "logical": "GetReservation",
        "name": "voice-bench-get-reservation",
        "desc": "Fetch reservation record from DynamoDB by booking_id",
        "src": "get_reservation.py",
    },
    {
        "logical": "CheckEligibility",
        "name": "voice-bench-check-eligibility",
        "desc": "Deterministic eligibility rules — no DynamoDB, pure logic",
        "src": "check_eligibility.py",
    },
    {
        "logical": "GetRefundMethod",
        "name": "voice-bench-get-refund-method",
        "desc": "Lookup refund rules from DynamoDB by fare_type + payment_method",
        "src": "get_refund_method.py",
    },
    {
        "logical": "ExecuteCancellation",
        "name": "voice-bench-execute-cancellation",
        "desc": "Execute cancellation steps; 150ms sleep per step for latency simulation",
        "src": "execute_cancellation.py",
    },
]

HEADER = """\
AWSTemplateFormatVersion: "2010-09-09"
Description: >-
  voice-agent-latency-bench: 4 tool Lambdas (arm64/512MB) with Provisioned
  Concurrency = 5 on alias "live". Role ARN and table names are HARDCODED
  (not Fn::ImportValue) to decouple from the shared-infra stack.
  If the team later wants cross-stack coupling, the assumed export names are:
    voice-bench-lambda-exec-role-arn      (IAM role ARN)
    voice-bench-reservations-table-name   (DynamoDB table name)
    voice-bench-refund-rules-table-name   (DynamoDB table name)

Resources:
"""

RESOURCE_TMPL = """\
  # ── {name} ──
  {logical}Function:
    Type: AWS::Lambda::Function
    Properties:
      FunctionName: {name}
      Description: {desc}
      Runtime: python3.12
      Architectures: [arm64]
      MemorySize: 512
      Timeout: 30
      Role: {role}
      Handler: index.handler
      Environment:
        Variables:
          RESERVATIONS_TABLE: {res_table}
          REFUND_RULES_TABLE: {ref_table}
      Tags:
        - Key: project
          Value: voice-agent-latency-bench
      Code:
        ZipFile: |
{code}

  {logical}Version:
    Type: AWS::Lambda::Version
    Properties:
      FunctionName: !Ref {logical}Function
      Description: Published by voice-bench-lambda-tools CFN stack

  {logical}Alias:
    Type: AWS::Lambda::Alias
    Properties:
      FunctionName: !Ref {logical}Function
      FunctionVersion: !GetAtt {logical}Version.Version
      Name: live
      ProvisionedConcurrencyConfig:
        ProvisionedConcurrentExecutions: 5

"""

OUTPUT_HEADER = """
Outputs:
"""

OUTPUT_FN_TMPL = """\
  {logical}FunctionArn:
    Description: ARN of {name} ($LATEST)
    Value: !GetAtt {logical}Function.Arn
    Export:
      Name: {name}-arn

  {logical}AliasArn:
    Description: ARN of {name}:live alias (PC=5)
    Value: !Ref {logical}Alias
    Export:
      Name: {name}-live-arn

"""


def _indent(src: str, spaces: int) -> str:
    """Indent every non-empty line of src by `spaces` spaces."""
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else "" for line in src.splitlines())


def build():
    parts = [HEADER]
    for fn in FUNCTIONS:
        src_path = LAMBDA_DIR / fn["src"]
        code = src_path.read_text()
        indented_code = _indent(code, 10)
        block = RESOURCE_TMPL.format(
            logical=fn["logical"],
            name=fn["name"],
            desc=fn["desc"],
            role=ROLE_ARN,
            res_table=RESERVATIONS_TABLE,
            ref_table=REFUND_RULES_TABLE,
            code=indented_code,
        )
        parts.append(block)
    parts.append(OUTPUT_HEADER)
    for fn in FUNCTIONS:
        parts.append(OUTPUT_FN_TMPL.format(logical=fn["logical"], name=fn["name"]))
    return "".join(parts)


if __name__ == "__main__":
    template = build()
    OUTPUT.write_text(template)
    size = len(template.encode())
    print(f"Wrote {OUTPUT} ({size:,} bytes)")
    if size > 51_200:
        print(f"WARNING: template is {size:,} bytes > 51200; use --template-url for validate/deploy")
        sys.exit(1)
    print("Size OK for --template-body")

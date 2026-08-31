#!/usr/bin/env python3
"""Deploy the voice-bench-tools AgentCore Gateway (MCP, AWS_IAM inbound auth)
with 4 Lambda targets pointing at the :live provisioned-concurrency aliases.

Idempotent: reuses an existing gateway/targets by name. Prints a JSON summary
(gateway id/arn/url + discovered MCP tool names) to stdout as the final line.

Usage: AWS_PROFILE=voice-bench python3 infra/gateway/deploy_gateway.py
"""
import json
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.dirname(__file__))
from tool_schemas import TOOLS, lambda_alias_arn  # noqa: E402
from mcp_client import MCPClient  # noqa: E402

REGION = "us-east-1"
GATEWAY_NAME = "voice-bench-tools"
GATEWAY_ROLE_ARN = "arn:aws:iam::111122223333:role/voice-bench-gateway-role"
PROFILE = os.environ.get("AWS_PROFILE", "voice-bench")

session = boto3.Session(profile_name=PROFILE, region_name=REGION)
cp = session.client("bedrock-agentcore-control")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def find_gateway():
    paginator_args = {}
    while True:
        resp = cp.list_gateways(**paginator_args)
        for gw in resp.get("items", []):
            if gw.get("name") == GATEWAY_NAME:
                return gw
        token = resp.get("nextToken")
        if not token:
            return None
        paginator_args = {"nextToken": token}


def wait_gateway_ready(gw_id):
    for _ in range(60):
        g = cp.get_gateway(gatewayIdentifier=gw_id)
        st = g.get("status")
        if st == "READY":
            return g
        if st in ("FAILED", "DELETING"):
            raise RuntimeError(f"gateway status {st}: {g.get('statusReasons')}")
        time.sleep(5)
    raise TimeoutError("gateway not READY in time")


def ensure_gateway():
    gw = find_gateway()
    if gw:
        log(f"gateway exists: {gw['gatewayId']} status={gw.get('status')}")
        return wait_gateway_ready(gw["gatewayId"])
    log("creating gateway ...")
    resp = cp.create_gateway(
        name=GATEWAY_NAME,
        roleArn=GATEWAY_ROLE_ARN,
        protocolType="MCP",
        authorizerType="AWS_IAM",
        description="voice-bench tool gateway (4 Lambda targets)",
    )
    gw_id = resp["gatewayId"]
    log(f"created gateway {gw_id}")
    return wait_gateway_ready(gw_id)


def existing_targets(gw_id):
    names = {}
    args = {"gatewayIdentifier": gw_id}
    while True:
        resp = cp.list_gateway_targets(**args)
        for t in resp.get("items", []):
            names[t.get("name")] = t
        token = resp.get("nextToken")
        if not token:
            break
        args = {"gatewayIdentifier": gw_id, "nextToken": token}
    return names


def wait_target_active(gw_id, target_id):
    for _ in range(60):
        t = cp.get_gateway_target(gatewayIdentifier=gw_id, targetId=target_id)
        st = t.get("status")
        if st == "READY" or st == "ACTIVE":
            return t
        if st in ("FAILED", "DELETING"):
            raise RuntimeError(f"target {target_id} status {st}: {t.get('statusReasons')}")
        time.sleep(4)
    raise TimeoutError(f"target {target_id} not active in time")


def ensure_targets(gw_id):
    have = existing_targets(gw_id)
    for spec in TOOLS:
        tname = spec["target"]
        if tname in have:
            log(f"target exists: {tname}")
            wait_target_active(gw_id, have[tname]["targetId"])
            continue
        log(f"creating target: {tname} -> {spec['lambda']}:live")
        resp = cp.create_gateway_target(
            gatewayIdentifier=gw_id,
            name=tname,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_alias_arn(spec["lambda"]),
                        "toolSchema": {
                            "inlinePayload": [
                                {
                                    "name": spec["name"],
                                    "description": spec["description"],
                                    "inputSchema": spec["inputSchema"],
                                }
                            ]
                        },
                    }
                }
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        wait_target_active(gw_id, resp["targetId"])


def discover_tools(mcp_url):
    creds = session.get_credentials().get_frozen_credentials()
    client = MCPClient(mcp_url, creds, REGION)
    client.connect()
    tools = client.list_tools()
    return [t.get("name") for t in tools]


def main():
    gw = ensure_gateway()
    gw_id = gw["gatewayId"]
    mcp_url = gw.get("gatewayUrl") or gw.get("gatewayEndpoint")
    ensure_targets(gw_id)
    tool_names = []
    try:
        tool_names = discover_tools(mcp_url)
    except Exception as e:  # discovery is best-effort; deploy still succeeded
        log(f"tool discovery failed (non-fatal): {e}")
    summary = {
        "gateway_id": gw_id,
        "gateway_arn": gw.get("gatewayArn"),
        "mcp_url": mcp_url,
        "authorizer": "AWS_IAM",
        "targets": [t["target"] for t in TOOLS],
        "discovered_tool_names": tool_names,
    }
    log("=== SUMMARY ===")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

"""AgentCore Runtime + Gateway client helpers for the benchmark.

Runtime: boto3 bedrock-agentcore invoke_agent_runtime (buffered + SSE streaming).
Gateway: SigV4-signed MCP (AWS_IAM inbound auth) — initialize handshake then
tools/call, with logical->actual tool-name resolution. Stdlib + boto3/botocore only.
"""
from __future__ import annotations

import json
import os
import time
import uuid
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Tuple

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

MCP_PROTOCOL_VERSION = "2025-06-18"
_SERVICE = "bedrock-agentcore"


# --------------------------------------------------------------------------- #
# Session id + boto3 factories
# --------------------------------------------------------------------------- #
def new_session_id() -> str:
    """>= 33 chars required by invoke_agent_runtime; return 64-char hex."""
    return uuid.uuid4().hex + uuid.uuid4().hex


# Cache the session + clients so per-call latency reflects the API round-trip,
# NOT boto3 client construction or credential_process (your AWS credential process (e.g. aws sso login)) resolution.
_SESSION: Optional[boto3.Session] = None
_CLIENTS: Dict[str, Any] = {}


def _make_session(config: dict) -> boto3.Session:
    global _SESSION
    if _SESSION is None:
        profile = os.environ.get("AWS_PROFILE", config.get("aws", {}).get("profile", "voice-bench"))
        region = config.get("aws", {}).get("region", "us-east-1")
        _SESSION = boto3.Session(profile_name=profile, region_name=region)
        # Force one credential resolution up front so it isn't billed to the first sample.
        _SESSION.get_credentials().get_frozen_credentials()
    return _SESSION


def _client(config: dict, name: str):
    if name not in _CLIENTS:
        _CLIENTS[name] = _make_session(config).client(name)
    return _CLIENTS[name]


def _agentcore_client(config: dict):
    return _client(config, "bedrock-agentcore")


def _lambda_client(config: dict):
    return _client(config, "lambda")


# --------------------------------------------------------------------------- #
# Runtime invocation
# --------------------------------------------------------------------------- #
def invoke_runtime(
    config: dict,
    payload: dict,
    session_id: str,
    qualifier: str,
    agent_runtime_arn: Optional[str] = None,
    stream: bool = False,
) -> Tuple[Any, float, Optional[float]]:
    """Invoke the AgentCore Runtime.

    Returns (result, latency_ms, ttfb_ms):
      - non-stream: result = parsed dict, ttfb_ms = None
      - stream: result = list of SSE `data:` payload strings, ttfb_ms = time of first event
    """
    if agent_runtime_arn is None:
        agent_runtime_arn = config["primary_runtime"]["arn"]
    accept = "text/event-stream" if stream else "application/json"
    # Pass the runtime session id into the payload so the agent scopes conversation
    # state per session (fair multi-turn; no cross-invocation accumulation).
    payload = {**payload, "session_id": session_id}
    payload_bytes = json.dumps(payload).encode("utf-8")
    client = _agentcore_client(config)

    t0 = time.perf_counter()
    response = client.invoke_agent_runtime(
        agentRuntimeArn=agent_runtime_arn,
        runtimeSessionId=session_id,
        qualifier=qualifier,
        contentType="application/json",
        accept=accept,
        payload=payload_bytes,
    )

    if stream:
        body = response["response"]
        payloads: List[str] = []
        ttfb: Optional[float] = None
        line_iter = body.iter_lines() if hasattr(body, "iter_lines") else iter(body)
        for line in line_iter:
            if isinstance(line, (bytes, bytearray)):
                line = line.decode("utf-8", "replace")
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            if ttfb is None:
                ttfb = (time.perf_counter() - t0) * 1000.0
            payloads.append(line[len("data:"):].strip())
        latency_ms = (time.perf_counter() - t0) * 1000.0
        return payloads, latency_ms, ttfb

    raw = response["response"].read()
    latency_ms = (time.perf_counter() - t0) * 1000.0
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = {"_raw": raw.decode("utf-8", "replace")}
    return result, latency_ms, None


# --------------------------------------------------------------------------- #
# Lambda direct invocation
# --------------------------------------------------------------------------- #
def invoke_lambda(config: dict, function_name: str, arguments: dict,
                  qualifier: Optional[str] = None) -> Tuple[Any, float]:
    """Direct Lambda invoke. qualifier='live' -> PC alias (T1); None -> $LATEST (T2)."""
    lc = _lambda_client(config)
    kwargs: dict = {"FunctionName": function_name, "Payload": json.dumps(arguments).encode("utf-8")}
    if qualifier is not None:
        kwargs["Qualifier"] = qualifier
    t0 = time.perf_counter()
    resp = lc.invoke(**kwargs)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    raw = resp["Payload"].read()
    try:
        result = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        result = {"_raw": raw.decode("utf-8", "replace")}
    return result, latency_ms


# --------------------------------------------------------------------------- #
# Gateway MCP over SigV4 (AWS_IAM inbound auth)
# --------------------------------------------------------------------------- #
def _parse_mcp_body(raw: bytes, content_type: str):
    text = raw.decode("utf-8", "replace").strip()
    if "text/event-stream" in (content_type or "") or text.startswith(("event:", "data:")):
        obj = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                d = line[len("data:"):].strip()
                if d and d != "[DONE]":
                    try:
                        obj = json.loads(d)
                    except json.JSONDecodeError:
                        pass
        return obj
    return json.loads(text) if text else None


class _GatewayMCP:
    def __init__(self, mcp_url, creds, region):
        self.url = mcp_url
        self.creds = creds
        self.region = region
        self.session_id = None
        self._id = 0
        self.name_map: Dict[str, str] = {}

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _send(self, payload, capture_session=False):
        body = json.dumps(payload).encode("utf-8")
        signed = AWSRequest(method="POST", url=self.url, data=body, headers=self._headers())
        SigV4Auth(self.creds, _SERVICE, self.region).add_auth(signed)
        req = urllib.request.Request(self.url, data=body, method="POST")
        for k, v in signed.headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if capture_session:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
            return _parse_mcp_body(raw, ctype)

    def _notify(self, method):
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": {}}).encode("utf-8")
        signed = AWSRequest(method="POST", url=self.url, data=body, headers=self._headers())
        SigV4Auth(self.creds, _SERVICE, self.region).add_auth(signed)
        req = urllib.request.Request(self.url, data=body, method="POST")
        for k, v in signed.headers.items():
            req.add_header(k, v)
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError:
            pass

    def _nid(self):
        self._id += 1
        return self._id

    def connect(self):
        self._send({"jsonrpc": "2.0", "id": self._nid(), "method": "initialize",
                    "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                               "clientInfo": {"name": "voice-bench-bench", "version": "1.0"}}},
                   capture_session=True)
        self._notify("notifications/initialized")
        resp = self._send({"jsonrpc": "2.0", "id": self._nid(), "method": "tools/list", "params": {}})
        tools = ((resp or {}).get("result") or {}).get("tools", [])
        actual = [t.get("name") for t in tools]
        norm = lambda s: "".join(c for c in (s or "").lower() if c.isalnum())
        for logical in ("get_reservation", "check_eligibility", "get_refund_method", "execute_cancellation"):
            ln = norm(logical)
            self.name_map[logical] = next((n for n in actual if norm(n).endswith(ln) or ln in norm(n)), logical)
        return actual

    def call(self, logical_name, arguments):
        name = self.name_map.get(logical_name, logical_name)
        t0 = time.perf_counter()
        resp = self._send({"jsonrpc": "2.0", "id": self._nid(), "method": "tools/call",
                           "params": {"name": name, "arguments": arguments}})
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp and "error" in resp:
            raise RuntimeError(f"MCP tool error: {resp['error']}")
        return (resp or {}).get("result"), latency_ms


_MCP_SINGLETON: Optional[_GatewayMCP] = None


def _get_mcp(config: dict) -> _GatewayMCP:
    global _MCP_SINGLETON
    if _MCP_SINGLETON is None:
        gw = config.get("gateway", {})
        mcp_url = gw.get("mcp_url", "")
        if "<PLACEHOLDER" in mcp_url or not mcp_url:
            raise RuntimeError("[Gateway] mcp_url not configured.")
        region = config.get("aws", {}).get("region", "us-east-1")
        creds = _make_session(config).get_credentials().get_frozen_credentials()
        client = _GatewayMCP(mcp_url, creds, region)
        client.connect()
        _MCP_SINGLETON = client
    return _MCP_SINGLETON


def call_gateway_tool(config: dict, tool_name: str, arguments: dict) -> Tuple[Any, float]:
    """Call a tool through the AgentCore Gateway (MCP, SigV4/AWS_IAM). Returns (result, latency_ms)."""
    return _get_mcp(config).call(tool_name, arguments)


# Back-compat no-op (gateway uses AWS_IAM/SigV4, not Cognito tokens).
def get_gateway_token(config: dict):  # pragma: no cover
    return None

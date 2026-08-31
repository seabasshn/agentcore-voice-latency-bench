"""Minimal SigV4-signed MCP client for an AgentCore Gateway (AWS_IAM inbound auth).

Streamable-HTTP MCP transport: POST JSON-RPC to the gateway /mcp URL, SigV4-signed
with service name 'bedrock-agentcore'. Handles both application/json and
text/event-stream (SSE) responses. Stdlib + botocore only (no requests).
"""
import json
import time
import urllib.request
import urllib.error

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVICE = "bedrock-agentcore"


def _parse_body(raw: bytes, content_type: str):
    """Return the JSON-RPC object from a response body (json or SSE-framed)."""
    text = raw.decode("utf-8", "replace").strip()
    if "text/event-stream" in (content_type or "") or text.startswith("event:") or text.startswith("data:"):
        # SSE: collect the last `data:` JSON payload.
        obj = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                data = line[len("data:"):].strip()
                if data and data != "[DONE]":
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        pass
        return obj
    return json.loads(text) if text else None


class MCPClient:
    def __init__(self, mcp_url, credentials, region):
        self.url = mcp_url
        self.creds = credentials              # frozen botocore credentials
        self.region = region
        self.session_id = None
        self._rpc_id = 0

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _send(self, payload, capture_session=False):
        body = json.dumps(payload).encode("utf-8")
        req = AWSRequest(method="POST", url=self.url, data=body, headers=self._headers())
        SigV4Auth(self.creds, SERVICE, self.region).add_auth(req)
        prepared = urllib.request.Request(self.url, data=body, method="POST")
        for k, v in req.headers.items():
            prepared.add_header(k, v)
        try:
            with urllib.request.urlopen(prepared, timeout=30) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if capture_session:
                    sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                    if sid:
                        self.session_id = sid
                return _parse_body(raw, ctype)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"MCP HTTP {e.code}: {detail}") from e

    def _notify(self, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}).encode("utf-8")
        req = AWSRequest(method="POST", url=self.url, data=body, headers=self._headers())
        SigV4Auth(self.creds, SERVICE, self.region).add_auth(req)
        prepared = urllib.request.Request(self.url, data=body, method="POST")
        for k, v in req.headers.items():
            prepared.add_header(k, v)
        try:
            urllib.request.urlopen(prepared, timeout=30).read()
        except urllib.error.HTTPError:
            pass  # notifications may return 202/empty

    def _next_id(self):
        self._rpc_id += 1
        return self._rpc_id

    def connect(self):
        resp = self._send({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "voice-bench", "version": "1.0"},
            },
        }, capture_session=True)
        self._notify("notifications/initialized")
        return resp

    def list_tools(self):
        resp = self._send({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}})
        result = (resp or {}).get("result", {})
        return result.get("tools", [])

    def call_tool(self, name, arguments):
        """Returns (result_obj, latency_ms)."""
        t0 = time.perf_counter()
        resp = self._send({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp and "error" in resp:
            raise RuntimeError(f"MCP tool error: {resp['error']}")
        return (resp or {}).get("result"), latency_ms

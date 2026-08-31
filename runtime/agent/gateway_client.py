"""SigV4-signed MCP client used *inside* the AgentCore Runtime to reach the
voice-bench-tools Gateway. Uses the runtime's injected IAM credentials.

Streamable-HTTP MCP: initialize -> notifications/initialized -> tools/list / tools/call.
Handles application/json and text/event-stream (SSE) responses.
"""
import json
import threading
import time
import urllib.request
import urllib.error

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVICE = "bedrock-agentcore"


def _parse_body(raw: bytes, content_type: str):
    text = raw.decode("utf-8", "replace").strip()
    if "text/event-stream" in (content_type or "") or text.startswith(("event:", "data:")):
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


class GatewayMCP:
    def __init__(self, mcp_url, region="us-east-1"):
        self.url = mcp_url
        self.region = region
        self._session = boto3.Session(region_name=region)   # picks up injected env creds
        self.session_id = None
        self._rpc_id = 0
        self._id_lock = threading.Lock()

    def _creds(self):
        return self._session.get_credentials().get_frozen_credentials()

    def _headers(self):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _send(self, payload, capture_session=False, timeout=30):
        body = json.dumps(payload).encode("utf-8")
        signed = AWSRequest(method="POST", url=self.url, data=body, headers=self._headers())
        SigV4Auth(self._creds(), SERVICE, self.region).add_auth(signed)
        req = urllib.request.Request(self.url, data=body, method="POST")
        for k, v in signed.headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get("Content-Type", "")
                if capture_session:
                    sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                    if sid:
                        self.session_id = sid
                return _parse_body(raw, ctype)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"MCP HTTP {e.code}: {e.read().decode('utf-8','replace')}") from e

    def _notify(self, method, params=None):
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}}).encode("utf-8")
        signed = AWSRequest(method="POST", url=self.url, data=body, headers=self._headers())
        SigV4Auth(self._creds(), SERVICE, self.region).add_auth(signed)
        req = urllib.request.Request(self.url, data=body, method="POST")
        for k, v in signed.headers.items():
            req.add_header(k, v)
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError:
            pass

    def _next_id(self):
        with self._id_lock:
            self._rpc_id += 1
            return self._rpc_id

    def connect(self):
        resp = self._send({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": MCP_PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "voice-bench-runtime", "version": "1.0"}},
        }, capture_session=True)
        self._notify("notifications/initialized")
        return resp

    def list_tools_raw(self):
        resp = self._send({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": {}})
        return ((resp or {}).get("result") or {}).get("tools", [])

    def call_tool(self, name, arguments):
        t0 = time.perf_counter()
        resp = self._send({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        latency_ms = (time.perf_counter() - t0) * 1000.0
        if resp and "error" in resp:
            raise RuntimeError(f"MCP tool error: {resp['error']}")
        return (resp or {}).get("result"), latency_ms


def extract_tool_payload(result):
    """Normalise an MCP tools/call result into a plain dict/str for the model."""
    if result is None:
        return {}
    # Prefer structured content when present.
    if isinstance(result, dict):
        if "structuredContent" in result and result["structuredContent"] is not None:
            sc = result["structuredContent"]
            # Gateway may wrap as {"result": ...}
            return sc.get("result", sc) if isinstance(sc, dict) else sc
        content = result.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    txt = item.get("text", "")
                    try:
                        return json.loads(txt)
                    except (json.JSONDecodeError, TypeError):
                        return txt
    return result

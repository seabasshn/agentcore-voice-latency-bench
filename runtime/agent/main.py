"""AgentCore Runtime entrypoint (HTTP protocol).

BedrockAgentCoreApp serves POST /invocations and GET /ping on 0.0.0.0:8080 and
streams SSE when the entrypoint returns an async generator.

The framework is selected at deploy time via AGENT_FRAMEWORK=strands|langgraph so
the two runtimes are byte-identical except for the orchestration library.

Payload contract:
  {"type":"warmup"}  -> {"status":"warm","init_ms":<float>}
  {"type":"ping"}    -> {"status":"ready","session_init_ms":<float>}
  {"message":"..."}  -> {"response":..., "timing":{...}, "usage":{...}}
  {"message":"...","stream":true} (Accept: text/event-stream) -> SSE token stream
"""
import inspect
import json
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

FRAMEWORK = os.environ.get("AGENT_FRAMEWORK", "strands").lower()
if FRAMEWORK == "langgraph":
    from agent import agent_core_lg as core
else:
    from agent import agent_core as core

app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload, context=None):
    if not isinstance(payload, dict):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    ptype = payload.get("type")
    if ptype in ("warmup", "ping"):
        return core.handle_control(ptype)

    message = payload.get("message") or payload.get("prompt")
    if message is None:
        return {"error": "no 'message' or 'type' in payload", "keys": list(payload.keys())}

    if payload.get("stream"):
        # async generator -> BedrockAgentCoreApp streams it as SSE
        return core.stream_turn(message, payload.get("session_id"))

    result = core.run_turn(message, payload.get("session_id"))
    if inspect.isawaitable(result):   # langgraph run_turn is async
        result = await result
    return result


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))

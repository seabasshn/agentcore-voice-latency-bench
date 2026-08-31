"""Core agent logic for the voice-bench flight-cancellation agent.

- Lazy, cold-start-timed initialisation of the Strands agent + Gateway MCP session.
- 4 tools exposed to the model as local @tool functions that call the AgentCore
  Gateway (Agent -> Gateway -> Lambda, the real path), each instrumented with
  thread-safe timing so we can measure per-tool latency and detect whether the
  framework runs independent tools in parallel (T15).
- warmup / ping control payloads, buffered run_turn, and streaming stream_turn.

Strands specifics (BedrockModel, Agent(), stream_async) are validated during the
local container test loop before deploy.
"""
import threading
import time

from strands import Agent, tool
from strands.models import BedrockModel

from . import config
from .gateway_client import GatewayMCP, extract_tool_payload

PROCESS_START = time.perf_counter()

_INIT_LOCK = threading.Lock()
_STATE = {
    "ready": False,
    "model": None,
    "mcp": None,
    "cold_init_ms": None,
    "name_map": {},        # logical tool name -> actual gateway MCP tool name
}

# Per-session conversation state. Strands Agent objects are stateful (they retain
# message history), so we key one agent per runtime session id: turns on the same
# session share context (real multi-turn), while independent invocations start
# clean instead of accumulating history across the whole run.
_SESSIONS = {}
_SESSION_ORDER = []
_MAX_SESSIONS = 64

# Module-level current-turn trace. AgentCore processes one turn at a time per
# session/instance, so a single guarded slot is sufficient and avoids
# contextvar-vs-threadpool propagation issues when tools run on worker threads.
_CURRENT_TURN = None


# --------------------------------------------------------------------------- #
# Tool timing
# --------------------------------------------------------------------------- #
def _new_turn():
    return {"t0": time.perf_counter(), "tools": [], "lock": threading.Lock()}


def _record_tool(logical_name, start, end, ok=True):
    turn = _CURRENT_TURN
    if turn is None:
        return
    with turn["lock"]:
        turn["tools"].append({
            "name": logical_name,
            "start_ms": (start - turn["t0"]) * 1000.0,
            "end_ms": (end - turn["t0"]) * 1000.0,
            "duration_ms": (end - start) * 1000.0,
            "ok": ok,
        })


def _call_gateway(logical_name, arguments):
    mcp = _STATE["mcp"]
    actual = _STATE["name_map"].get(logical_name, logical_name)
    start = time.perf_counter()
    ok = True
    try:
        result, _ = mcp.call_tool(actual, arguments)
        return extract_tool_payload(result)
    except Exception as e:  # surface to model as a tool result, still record timing
        ok = False
        return {"error": str(e)}
    finally:
        _record_tool(logical_name, start, time.perf_counter(), ok)


# --------------------------------------------------------------------------- #
# Tools exposed to the model
# --------------------------------------------------------------------------- #
@tool
def get_reservation(booking_id: str) -> dict:
    """Retrieve a passenger's flight reservation by booking ID."""
    return _call_gateway("get_reservation", {"booking_id": booking_id})


@tool
def check_eligibility(booking_id: str, already_traveled: bool = False,
                      is_group_booking: bool = False, pax_count: int = 1,
                      has_checked_bags: bool = False, linked_minor: bool = False) -> dict:
    """Evaluate cancellation eligibility from the reservation's attributes."""
    return _call_gateway("check_eligibility", {
        "booking_id": booking_id, "already_traveled": already_traveled,
        "is_group_booking": is_group_booking, "pax_count": pax_count,
        "has_checked_bags": has_checked_bags, "linked_minor": linked_minor,
    })


@tool
def get_refund_method(fare_type: str, payment_method: str) -> dict:
    """Look up refund method, timeline, transferability and expiry."""
    return _call_gateway("get_refund_method", {"fare_type": fare_type, "payment_method": payment_method})


@tool
def execute_cancellation(booking_id: str, steps: list) -> dict:
    """Execute the cancellation plan (split_booking, remove_passenger, process_refund)."""
    return _call_gateway("execute_cancellation", {"booking_id": booking_id, "steps": steps})


_LOGICAL_TOOLS = ["get_reservation", "check_eligibility", "get_refund_method", "execute_cancellation"]


# --------------------------------------------------------------------------- #
# Initialisation
# --------------------------------------------------------------------------- #
def _build_name_map(mcp):
    """Map our logical tool names to the actual gateway MCP tool names
    (the gateway may prefix them, e.g. '<target>___<tool>')."""
    def norm(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())

    name_map = {}
    try:
        raw = mcp.list_tools_raw()
        actual_names = [t.get("name") for t in raw]
        for logical in _LOGICAL_TOOLS:
            ln = norm(logical)
            # Gateway may expose as "<target>___<tool>" with hyphens; match on the
            # normalized suffix (alphanumerics only), so get-reservation___get_reservation
            # resolves to logical get_reservation.
            match = next((n for n in actual_names if norm(n).endswith(ln) or ln in norm(n)), logical)
            name_map[logical] = match
    except Exception:
        name_map = {n: n for n in _LOGICAL_TOOLS}
    return name_map


def ensure_init():
    """Lazily build the MCP session + Strands agent. Returns init duration (ms);
    0.0 if already warm."""
    if _STATE["ready"]:
        return 0.0
    with _INIT_LOCK:
        if _STATE["ready"]:
            return 0.0
        t0 = time.perf_counter()
        mcp = GatewayMCP(config.GATEWAY_MCP_URL, region=config.AWS_REGION)
        mcp.connect()
        _STATE["mcp"] = mcp
        _STATE["name_map"] = _build_name_map(mcp)
        _STATE["model"] = BedrockModel(model_id=config.BEDROCK_MODEL_ID,
                                       region_name=config.AWS_REGION, streaming=True)
        _build_agent()  # build one up front so the model client is warm
        _STATE["cold_init_ms"] = (time.perf_counter() - t0) * 1000.0
        _STATE["ready"] = True
        return _STATE["cold_init_ms"]


def _build_agent():
    return Agent(
        model=_STATE["model"],
        system_prompt=config.SYSTEM_PROMPT,
        tools=[get_reservation, check_eligibility, get_refund_method, execute_cancellation],
    )


def _agent_for(session_id):
    """Return the agent for this session (fresh, memory-scoped). Ephemeral (no
    retained history) when session_id is absent."""
    if not session_id:
        return _build_agent()
    ag = _SESSIONS.get(session_id)
    if ag is None:
        ag = _build_agent()
        _SESSIONS[session_id] = ag
        _SESSION_ORDER.append(session_id)
        if len(_SESSION_ORDER) > _MAX_SESSIONS:
            _SESSIONS.pop(_SESSION_ORDER.pop(0), None)
    return ag


# --------------------------------------------------------------------------- #
# Trace analysis
# --------------------------------------------------------------------------- #
def _analyze_tools(tools):
    if not tools:
        return 0.0, 0.0, False
    intervals = sorted(((t["start_ms"], t["end_ms"]) for t in tools), key=lambda x: x[0])
    # union of intervals = wall-clock time in tools
    union = 0.0
    cur_s, cur_e = intervals[0]
    parallel = False
    for s, e in intervals[1:]:
        if s < cur_e - 1e-6:      # overlap
            parallel = True
            cur_e = max(cur_e, e)
        else:
            union += cur_e - cur_s
            cur_s, cur_e = s, e
    union += cur_e - cur_s
    sum_individual = sum(t["duration_ms"] for t in tools)
    return union, sum_individual, parallel


def _extract_usage(result):
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        metrics = getattr(result, "metrics", None)
        acc = getattr(metrics, "accumulated_usage", None) if metrics else None
        if isinstance(acc, dict):
            usage["input_tokens"] = int(acc.get("inputTokens", 0) or 0)
            usage["output_tokens"] = int(acc.get("outputTokens", 0) or 0)
    except Exception:
        pass
    return usage


def _result_text(result):
    try:
        return str(result)
    except Exception:
        return ""


# --------------------------------------------------------------------------- #
# Control + turn handlers
# --------------------------------------------------------------------------- #
def handle_control(ptype):
    if ptype == "warmup":
        init_ms = ensure_init()
        return {"status": "warm", "init_ms": init_ms if init_ms else (_STATE["cold_init_ms"] or 0.0)}
    # ping
    t0 = time.perf_counter()
    ensure_init()
    return {"status": "ready", "session_init_ms": (time.perf_counter() - t0) * 1000.0}


def run_turn(message, session_id=None):
    global _CURRENT_TURN
    ensure_init()
    turn = _new_turn()
    _CURRENT_TURN = turn
    try:
        result = _agent_for(session_id)(message)
        total_ms = (time.perf_counter() - turn["t0"]) * 1000.0
        tool_ms, sum_individual, parallel = _analyze_tools(turn["tools"])
        return {
            "response": _result_text(result),
            "timing": {
                "total_ms": total_ms,
                "llm_ms": max(0.0, total_ms - tool_ms),
                "tool_ms": tool_ms,
                "tool_sum_individual_ms": sum_individual,
                "ttft_ms": None,
                "tools": turn["tools"],
                "parallel_tools": parallel,
                "num_turns": len(turn["tools"]) and (len(turn["tools"]) + 1) or 1,
            },
            "usage": _extract_usage(result),
            "session_id": session_id,
        }
    finally:
        _CURRENT_TURN = None


async def stream_turn(message, session_id=None):
    """Async generator yielding SSE `data:` events. First text chunk = TTFT.
    Final event carries {'done': true, 'timing': {...}, 'usage': {...}}."""
    global _CURRENT_TURN
    import json as _json
    ensure_init()
    turn = _new_turn()
    _CURRENT_TURN = turn
    first_token_ms = None
    text_parts = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        agent = _agent_for(session_id)
        async for event in agent.stream_async(message):
            delta = event.get("data") if isinstance(event, dict) else None
            if delta:
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - turn["t0"]) * 1000.0
                text_parts.append(delta)
                # Yield the raw object; BedrockAgentCoreApp frames it as an SSE
                # `data: <json>` event (do NOT pre-frame, or it double-wraps).
                yield {"type": "token", "delta": delta}
            # capture usage if present on a result event
            if isinstance(event, dict) and event.get("result") is not None:
                usage = _extract_usage(event["result"])
        total_ms = (time.perf_counter() - turn["t0"]) * 1000.0
        tool_ms, sum_individual, parallel = _analyze_tools(turn["tools"])
        final = {
            "done": True,
            "response": "".join(text_parts),
            "timing": {
                "total_ms": total_ms,
                "llm_ms": max(0.0, total_ms - tool_ms),
                "tool_ms": tool_ms,
                "tool_sum_individual_ms": sum_individual,
                "ttft_ms": first_token_ms,
                "tools": turn["tools"],
                "parallel_tools": parallel,
                "num_turns": len(turn["tools"]) and (len(turn["tools"]) + 1) or 1,
            },
            "usage": usage,
            "session_id": session_id,
        }
        yield final
    finally:
        _CURRENT_TURN = None

"""LangGraph variant of the flight-cancellation agent.

Mirrors agent_core.py's control/turn/stream interface and the EXACT response +
timing contract, so the benchmark harness and the Strands variant are compared
apples-to-apples. Only the orchestration framework differs (LangGraph prebuilt
ReAct agent + langchain-aws ChatBedrockConverse).

Trace/gateway helpers are duplicated here (not imported from agent_core) so the
validated Strands module is left untouched and the measurement logic is identical.
"""
import threading
import time
import uuid

from langchain_core.tools import tool as lc_tool
from langchain_aws import ChatBedrockConverse
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver

from . import config
from .gateway_client import GatewayMCP, extract_tool_payload

PROCESS_START = time.perf_counter()

_INIT_LOCK = threading.Lock()
_STATE = {"ready": False, "agent": None, "mcp": None, "cold_init_ms": None, "name_map": {}}
_CURRENT_TURN = None


# --------------------------------------------------------------------------- #
# Tool timing (identical to the Strands variant)
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
    except Exception as e:
        ok = False
        return {"error": str(e)}
    finally:
        _record_tool(logical_name, start, time.perf_counter(), ok)


# --------------------------------------------------------------------------- #
# Tools (LangChain tools; same 4, same gateway path)
# --------------------------------------------------------------------------- #
@lc_tool
def get_reservation(booking_id: str) -> dict:
    """Retrieve a passenger's flight reservation by booking ID."""
    return _call_gateway("get_reservation", {"booking_id": booking_id})


@lc_tool
def check_eligibility(booking_id: str, already_traveled: bool = False,
                      is_group_booking: bool = False, pax_count: int = 1,
                      has_checked_bags: bool = False, linked_minor: bool = False) -> dict:
    """Evaluate cancellation eligibility from the reservation's attributes."""
    return _call_gateway("check_eligibility", {
        "booking_id": booking_id, "already_traveled": already_traveled,
        "is_group_booking": is_group_booking, "pax_count": pax_count,
        "has_checked_bags": has_checked_bags, "linked_minor": linked_minor,
    })


@lc_tool
def get_refund_method(fare_type: str, payment_method: str) -> dict:
    """Look up refund method, timeline, transferability and expiry."""
    return _call_gateway("get_refund_method", {"fare_type": fare_type, "payment_method": payment_method})


@lc_tool
def execute_cancellation(booking_id: str, steps: list) -> dict:
    """Execute the cancellation plan (split_booking, remove_passenger, process_refund)."""
    return _call_gateway("execute_cancellation", {"booking_id": booking_id, "steps": steps})


_LOGICAL_TOOLS = ["get_reservation", "check_eligibility", "get_refund_method", "execute_cancellation"]


# --------------------------------------------------------------------------- #
# Init
# --------------------------------------------------------------------------- #
def _build_name_map(mcp):
    def norm(s):
        return "".join(c for c in (s or "").lower() if c.isalnum())
    name_map = {}
    try:
        raw = mcp.list_tools_raw()
        actual = [t.get("name") for t in raw]
        for logical in _LOGICAL_TOOLS:
            ln = norm(logical)
            name_map[logical] = next((n for n in actual if norm(n).endswith(ln) or ln in norm(n)), logical)
    except Exception:
        name_map = {n: n for n in _LOGICAL_TOOLS}
    return name_map


def ensure_init():
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
        model = ChatBedrockConverse(
            model=config.BEDROCK_MODEL_ID,
            region_name=config.AWS_REGION,
            max_tokens=512,
        )
        # Checkpointer gives per-thread (per-session) conversation memory so
        # multi-turn shares context, matching the Strands variant's behavior.
        _STATE["agent"] = create_react_agent(
            model,
            tools=[get_reservation, check_eligibility, get_refund_method, execute_cancellation],
            prompt=config.SYSTEM_PROMPT,
            checkpointer=MemorySaver(),
        )
        _STATE["cold_init_ms"] = (time.perf_counter() - t0) * 1000.0
        _STATE["ready"] = True
        return _STATE["cold_init_ms"]


# --------------------------------------------------------------------------- #
# Trace analysis (identical to Strands variant)
# --------------------------------------------------------------------------- #
def _analyze_tools(tools):
    if not tools:
        return 0.0, 0.0, False
    intervals = sorted(((t["start_ms"], t["end_ms"]) for t in tools), key=lambda x: x[0])
    union = 0.0
    cur_s, cur_e = intervals[0]
    parallel = False
    for s, e in intervals[1:]:
        if s < cur_e - 1e-6:
            parallel = True
            cur_e = max(cur_e, e)
        else:
            union += cur_e - cur_s
            cur_s, cur_e = s, e
    union += cur_e - cur_s
    return union, sum(t["duration_ms"] for t in tools), parallel


def _text_of(content):
    """AIMessage(Chunk).content may be a str or a list of content blocks."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text" and b.get("text"):
                    out.append(b["text"])
            elif isinstance(b, str):
                out.append(b)
        return "".join(out)
    return ""


def _final_text(result):
    try:
        msgs = result.get("messages", [])
        for m in reversed(msgs):
            txt = _text_of(getattr(m, "content", None))
            if txt.strip():
                return txt
    except Exception:
        pass
    return ""


def _usage(result):
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        for m in result.get("messages", []):
            um = getattr(m, "usage_metadata", None)
            if isinstance(um, dict):
                usage["input_tokens"] += int(um.get("input_tokens", 0) or 0)
                usage["output_tokens"] += int(um.get("output_tokens", 0) or 0)
    except Exception:
        pass
    return usage


# --------------------------------------------------------------------------- #
# Control + turns
# --------------------------------------------------------------------------- #
def handle_control(ptype):
    if ptype == "warmup":
        init_ms = ensure_init()
        return {"status": "warm", "init_ms": init_ms if init_ms else (_STATE["cold_init_ms"] or 0.0)}
    t0 = time.perf_counter()
    ensure_init()
    return {"status": "ready", "session_init_ms": (time.perf_counter() - t0) * 1000.0}


async def run_turn(message, session_id=None):
    global _CURRENT_TURN
    ensure_init()
    turn = _new_turn()
    _CURRENT_TURN = turn
    try:
        cfg = {"configurable": {"thread_id": session_id or uuid.uuid4().hex}}
        result = await _STATE["agent"].ainvoke(
            {"messages": [{"role": "user", "content": message}]}, config=cfg)
        total_ms = (time.perf_counter() - turn["t0"]) * 1000.0
        tool_ms, sum_individual, parallel = _analyze_tools(turn["tools"])
        return {
            "response": _final_text(result),
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
            "usage": _usage(result),
            "session_id": session_id,
            "framework": "langgraph",
        }
    finally:
        _CURRENT_TURN = None


async def stream_turn(message, session_id=None):
    global _CURRENT_TURN
    ensure_init()
    turn = _new_turn()
    _CURRENT_TURN = turn
    first_token_ms = None
    parts = []
    try:
        cfg = {"configurable": {"thread_id": session_id or uuid.uuid4().hex}}
        async for chunk, _meta in _STATE["agent"].astream(
            {"messages": [{"role": "user", "content": message}]}, stream_mode="messages", config=cfg
        ):
            piece = _text_of(getattr(chunk, "content", None))
            if piece:
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - turn["t0"]) * 1000.0
                parts.append(piece)
                yield {"type": "token", "delta": piece}
        total_ms = (time.perf_counter() - turn["t0"]) * 1000.0
        tool_ms, sum_individual, parallel = _analyze_tools(turn["tools"])
        yield {
            "done": True,
            "response": "".join(parts),
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
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "session_id": session_id,
            "framework": "langgraph",
        }
    finally:
        _CURRENT_TURN = None

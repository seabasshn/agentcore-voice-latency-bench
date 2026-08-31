"""
Voice-Agent Latency Benchmark Runner — T1 through T16.

Usage:
    python3 benchmark/runner.py --help
    python3 benchmark/runner.py --config benchmark/config.json --tests T1,T2,T9
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure package is importable when run as a script
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = str(_HERE.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from benchmark.agentcore_client import (
    new_session_id,
    invoke_runtime,
    invoke_lambda,
    call_gateway_tool,
)
from benchmark.stats import summary_stats, p50 as _p50

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BK001_CANCEL_MSG = "I need to cancel booking BK-001"
COLD_START_THRESHOLD_MS = 1500.0  # T10 warm/cold classifier


def _load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _iter_count(default: int, override: Optional[int]) -> int:
    return override if override is not None else default


def _primary_arn(cfg: dict) -> str:
    return cfg["primary_runtime"]["arn"]


def _primary_qualifier(cfg: dict) -> str:
    return cfg["primary_runtime"].get("qualifier", "DEFAULT")


def _prewarm(cfg: dict, session_id: str) -> None:
    """Fire-and-forget warmup payload on the primary runtime."""
    try:
        invoke_runtime(cfg, {"type": "warmup"}, session_id, _primary_qualifier(cfg))
    except Exception:
        pass  # best-effort


def _ping(cfg: dict, session_id: str, arn: Optional[str] = None, qualifier: Optional[str] = None) -> float:
    """Send a ping and return latency_ms. Raises on error."""
    q = qualifier or _primary_qualifier(cfg)
    result, lat, _ = invoke_runtime(cfg, {"type": "ping"}, session_id, q, agent_runtime_arn=arn)
    return lat


# ---------------------------------------------------------------------------
# T1 — Lambda Provisioned Concurrency (warm)
# ---------------------------------------------------------------------------

def run_t1(cfg: dict, iterations: int = 20) -> dict:
    """T1: Lambda direct with Provisioned Concurrency (Qualifier='live')."""
    samples: List[float] = []
    raw: List[dict] = []
    fn = cfg["lambda"]["get_reservation"]
    for i in range(iterations):
        try:
            result, lat = invoke_lambda(cfg, fn, {"booking_id": "BK-001"}, qualifier="live")
            samples.append(lat)
            raw.append({"iter": i, "latency_ms": lat, "result": result})
        except Exception as exc:
            raw.append({"iter": i, "error": str(exc)})
    return {"test": "T1", "description": "Lambda PC warm (Qualifier=live)", "samples": samples, "raw": raw, "stats": summary_stats(samples)}


# ---------------------------------------------------------------------------
# T2 — Lambda $LATEST (cold baseline)
# ---------------------------------------------------------------------------

def run_t2(cfg: dict, iterations: int = 20) -> dict:
    """T2: Lambda direct $LATEST — cold baseline."""
    samples: List[float] = []
    raw: List[dict] = []
    fn = cfg["lambda"]["get_reservation"]
    for i in range(iterations):
        try:
            result, lat = invoke_lambda(cfg, fn, {"booking_id": "BK-001"}, qualifier=None)
            samples.append(lat)
            raw.append({"iter": i, "latency_ms": lat, "result": result})
        except Exception as exc:
            raw.append({"iter": i, "error": str(exc)})
    return {"test": "T2", "description": "Lambda $LATEST (cold baseline)", "samples": samples, "raw": raw, "stats": summary_stats(samples)}


# ---------------------------------------------------------------------------
# T3 — Gateway tool calls (overhead vs T1)
# ---------------------------------------------------------------------------

_GATEWAY_TOOLS = [
    ("get_reservation", {"booking_id": "BK-001"}),
    ("check_eligibility", {"booking_id": "BK-001", "already_traveled": False, "is_group_booking": False, "pax_count": 1, "has_checked_bags": False, "linked_minor": False}),
    ("get_refund_method", {"fare_type": "STANDARD", "payment_method": "CREDIT_CARD"}),
]


def run_t3(cfg: dict, iterations: int = 20, t1_stats: Optional[dict] = None) -> dict:
    """T3: Each tool via Gateway MCP endpoint; compute overhead vs T1."""
    results_by_tool: dict = {}
    gateway_configured = "<PLACEHOLDER" not in cfg.get("gateway", {}).get("mcp_url", "<PLACEHOLDER")

    if not gateway_configured:
        return {
            "test": "T3",
            "description": "Gateway MCP tool calls",
            "skipped": True,
            "reason": "Gateway not configured (mcp_url is placeholder). Fill config after deploy.",
        }

    for tool_name, args in _GATEWAY_TOOLS:
        samples: List[float] = []
        raw: List[dict] = []
        for i in range(iterations):
            try:
                result, lat = call_gateway_tool(cfg, tool_name, args)
                samples.append(lat)
                raw.append({"iter": i, "latency_ms": lat})
            except Exception as exc:
                raw.append({"iter": i, "error": str(exc)})
        t1_p50 = t1_stats["p50"] if t1_stats else None
        overhead = round(_p50(samples) - t1_p50, 2) if t1_p50 and samples else None
        results_by_tool[tool_name] = {"samples": samples, "stats": summary_stats(samples), "overhead_vs_t1_p50_ms": overhead, "raw": raw}

    return {"test": "T3", "description": "Gateway MCP tool calls", "tools": results_by_tool}


# ---------------------------------------------------------------------------
# T4 — Runtime cold start (container)
# ---------------------------------------------------------------------------

def run_t4(cfg: dict, iterations: int = 20) -> dict:
    """T4: Runtime cold start — new session per iter, container agent."""
    arn = cfg["agent_runtimes"]["voice-bench-agent-container"]["arn"]
    qualifier = _primary_qualifier(cfg)
    samples: List[float] = []
    raw: List[dict] = []
    for i in range(iterations):
        sid = new_session_id()
        try:
            lat = _ping(cfg, sid, arn=arn, qualifier=qualifier)
            samples.append(lat)
            raw.append({"iter": i, "session_id": sid, "latency_ms": lat})
        except Exception as exc:
            raw.append({"iter": i, "session_id": sid, "error": str(exc)})
    return {"test": "T4", "description": "Runtime cold start — container (new session each iter)", "samples": samples, "raw": raw, "stats": summary_stats(samples)}


# ---------------------------------------------------------------------------
# T5 — Runtime cold start (code deploy)
# ---------------------------------------------------------------------------

def run_t5(cfg: dict, iterations: int = 20) -> dict:
    """T5: Runtime cold start — code-based deploy agent."""
    arn = cfg["agent_runtimes"]["voice-bench-agent-code"]["arn"]
    qualifier = _primary_qualifier(cfg)
    samples: List[float] = []
    raw: List[dict] = []
    for i in range(iterations):
        sid = new_session_id()
        try:
            lat = _ping(cfg, sid, arn=arn, qualifier=qualifier)
            samples.append(lat)
            raw.append({"iter": i, "session_id": sid, "latency_ms": lat})
        except Exception as exc:
            raw.append({"iter": i, "session_id": sid, "error": str(exc)})
    return {"test": "T5", "description": "Runtime cold start — code-deploy agent (new session each iter)", "samples": samples, "raw": raw, "stats": summary_stats(samples)}


# ---------------------------------------------------------------------------
# T6 — Pre-warm: warmup then ping
# ---------------------------------------------------------------------------

def run_t6(cfg: dict, iterations: int = 20) -> dict:
    """T6: Pre-warm: new session → warmup → sleep 2s → ping (measure ping only)."""
    samples: List[float] = []
    raw: List[dict] = []
    for i in range(iterations):
        sid = new_session_id()
        try:
            _prewarm(cfg, sid)
            time.sleep(2.0)
            lat = _ping(cfg, sid)
            samples.append(lat)
            raw.append({"iter": i, "session_id": sid, "latency_ms": lat})
        except Exception as exc:
            raw.append({"iter": i, "session_id": sid, "error": str(exc)})
    return {"test": "T6", "description": "Pre-warm: warmup + 2s sleep → ping (target p50 < 500ms)", "target_p50_ms": 500, "samples": samples, "raw": raw, "stats": summary_stats(samples)}


# ---------------------------------------------------------------------------
# T7 — 20 pings across 3 endpoints round-robin
# ---------------------------------------------------------------------------

def run_t7(cfg: dict, iterations: int = 20) -> dict:
    """T7: Round-robin pings across DEFAULT, voice-bench-ep-2, voice-bench-ep-3."""
    endpoint_names = ["DEFAULT", "voice-bench-ep-2", "voice-bench-ep-3"]
    per_endpoint: dict = {ep: {"samples": [], "raw": []} for ep in endpoint_names}
    all_samples: List[float] = []

    for i in range(iterations):
        ep_name = endpoint_names[i % len(endpoint_names)]
        ep_cfg = cfg["endpoints"][ep_name]
        sid = new_session_id()
        try:
            lat = _ping(cfg, sid, arn=ep_cfg["agent_runtime_arn"], qualifier=ep_cfg["qualifier"])
            per_endpoint[ep_name]["samples"].append(lat)
            per_endpoint[ep_name]["raw"].append({"iter": i, "session_id": sid, "latency_ms": lat})
            all_samples.append(lat)
        except Exception as exc:
            per_endpoint[ep_name]["raw"].append({"iter": i, "session_id": sid, "error": str(exc)})

    for ep in endpoint_names:
        per_endpoint[ep]["stats"] = summary_stats(per_endpoint[ep]["samples"])

    return {"test": "T7", "description": "Round-robin pings across 3 endpoints (new sessions)", "per_endpoint": per_endpoint, "all_samples": all_samples, "stats": summary_stats(all_samples)}


# ---------------------------------------------------------------------------
# T8 — Same session: warmup + 20 pings
# ---------------------------------------------------------------------------

def run_t8(cfg: dict, iterations: int = 20) -> dict:
    """T8: Single session: warmup → sleep 2s → 20 pings on same session. Target p50 < 200ms."""
    sid = new_session_id()
    samples: List[float] = []
    raw: List[dict] = []
    try:
        _prewarm(cfg, sid)
        time.sleep(2.0)
    except Exception as exc:
        return {"test": "T8", "description": "Same-session 20 pings after warmup", "error": f"Warmup failed: {exc}"}
    for i in range(iterations):
        try:
            lat = _ping(cfg, sid)
            samples.append(lat)
            raw.append({"iter": i, "latency_ms": lat})
        except Exception as exc:
            raw.append({"iter": i, "error": str(exc)})
    return {"test": "T8", "description": "Same-session 20 pings after warmup (target p50 < 200ms)", "target_p50_ms": 200, "session_id": sid, "samples": samples, "raw": raw, "stats": summary_stats(samples)}


# ---------------------------------------------------------------------------
# T9 — Full E2E: warmup → cancel BK-001
# ---------------------------------------------------------------------------

def _run_e2e_single(cfg: dict, prewarm: bool) -> dict:
    sid = new_session_id()
    if prewarm:
        _prewarm(cfg, sid)
        time.sleep(3.0)
    payload = {"message": BK001_CANCEL_MSG}
    t0 = time.perf_counter()
    try:
        result, lat, _ = invoke_runtime(cfg, payload, sid, _primary_qualifier(cfg))
        return {"latency_ms": lat, "session_id": sid, "prewarm": prewarm, "timing": result.get("timing"), "usage": result.get("usage")}
    except Exception as exc:
        return {"error": str(exc), "session_id": sid, "prewarm": prewarm}


def run_t9(cfg: dict, iterations: int = 5) -> dict:
    """T9: Full E2E cancel BK-001, with and without prewarm (5 iters each). Target p50 < 2000ms."""
    warm_samples: List[float] = []
    cold_samples: List[float] = []
    raw_warm: List[dict] = []
    raw_cold: List[dict] = []

    for i in range(iterations):
        r = _run_e2e_single(cfg, prewarm=True)
        raw_warm.append({**r, "iter": i})
        if "latency_ms" in r:
            warm_samples.append(r["latency_ms"])

    for i in range(iterations):
        r = _run_e2e_single(cfg, prewarm=False)
        raw_cold.append({**r, "iter": i})
        if "latency_ms" in r:
            cold_samples.append(r["latency_ms"])

    return {
        "test": "T9",
        "description": "Full E2E cancel BK-001 (target p50 < 2000ms)",
        "target_p50_ms": 2000,
        "with_prewarm": {"samples": warm_samples, "stats": summary_stats(warm_samples), "raw": raw_warm},
        "without_prewarm": {"samples": cold_samples, "stats": summary_stats(cold_samples), "raw": raw_cold},
    }


# ---------------------------------------------------------------------------
# T10 — Burst: 15 concurrent new-session pings
# ---------------------------------------------------------------------------

def _burst_ping(cfg: dict, idx: int) -> dict:
    sid = new_session_id()
    try:
        lat = _ping(cfg, sid)
        warm = lat <= COLD_START_THRESHOLD_MS
        return {"idx": idx, "session_id": sid, "latency_ms": lat, "warm": warm}
    except Exception as exc:
        return {"idx": idx, "session_id": sid, "error": str(exc)}


def run_t10(cfg: dict, burst_size: int = 15) -> dict:
    """T10: 15 concurrent new-session pings; classify warm/cold."""
    results: List[dict] = []
    with ThreadPoolExecutor(max_workers=burst_size) as pool:
        futures = {pool.submit(_burst_ping, cfg, i): i for i in range(burst_size)}
        for fut in as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r.get("idx", 0))
    warm = [r["latency_ms"] for r in results if r.get("warm") is True]
    cold = [r["latency_ms"] for r in results if r.get("warm") is False and "latency_ms" in r]

    return {
        "test": "T10",
        "description": f"Burst {burst_size} concurrent new-session pings",
        "cold_start_threshold_ms": COLD_START_THRESHOLD_MS,
        "warm_count": len(warm),
        "cold_count": len(cold),
        "warm_stats": summary_stats(warm),
        "cold_stats": summary_stats(cold),
        "all_stats": summary_stats(warm + cold),
        "raw": results,
    }


# ---------------------------------------------------------------------------
# T11 — Streaming TTFB (replaces client WebSocket test)
# ---------------------------------------------------------------------------

def _run_stream_ttfb(cfg: dict, prewarm: bool) -> dict:
    sid = new_session_id()
    if prewarm:
        _prewarm(cfg, sid)
        time.sleep(2.0)
    payload = {"message": BK001_CANCEL_MSG, "stream": True}
    try:
        chunks, lat, ttfb = invoke_runtime(cfg, payload, sid, _primary_qualifier(cfg), stream=True)
        return {"latency_ms": lat, "ttfb_ms": ttfb, "session_id": sid, "prewarm": prewarm, "num_chunks": len(chunks)}
    except Exception as exc:
        return {"error": str(exc), "session_id": sid, "prewarm": prewarm}


def run_t11(cfg: dict, iterations: int = 20) -> dict:
    """
    T11: Streaming TTFB — prewarm session → stream cancel BK-001 → record time-to-first-byte.
    Runs with and without prewarm.
    NOTE: Replaces the (impossible) client-WebSocket test — AgentCore Runtime has no
    client-side WebSocket API; streaming is via text/event-stream SSE over invoke_agent_runtime.
    """
    warm_ttfb: List[float] = []
    cold_ttfb: List[float] = []
    raw: List[dict] = []

    for i in range(iterations // 2 or 1):
        r = _run_stream_ttfb(cfg, prewarm=True)
        r["iter"] = i
        raw.append(r)
        if r.get("ttfb_ms") is not None:
            warm_ttfb.append(r["ttfb_ms"])

    for i in range(iterations // 2 or 1):
        r = _run_stream_ttfb(cfg, prewarm=False)
        r["iter"] = i
        raw.append(r)
        if r.get("ttfb_ms") is not None:
            cold_ttfb.append(r["ttfb_ms"])

    return {
        "test": "T11",
        "description": "Streaming TTFB (text/event-stream SSE); NOTE: replaces impossible client-WebSocket test",
        "with_prewarm": {"ttfb_samples": warm_ttfb, "stats": summary_stats(warm_ttfb)},
        "without_prewarm": {"ttfb_samples": cold_ttfb, "stats": summary_stats(cold_ttfb)},
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# T12 — Streaming TTFT
# ---------------------------------------------------------------------------

def run_t12(cfg: dict, iterations: int = 20) -> dict:
    """T12: Streaming TTFT — prewarm, stream cancel BK-001, record TTFT + completion delta. Target TTFT p50 < 1500ms."""
    ttft_samples: List[float] = []
    total_samples: List[float] = []
    raw: List[dict] = []

    for i in range(iterations):
        sid = new_session_id()
        _prewarm(cfg, sid)
        time.sleep(2.0)
        payload = {"message": BK001_CANCEL_MSG, "stream": True}
        try:
            chunks, lat, ttfb = invoke_runtime(cfg, payload, sid, _primary_qualifier(cfg), stream=True)
            # Parse SSE events for done+timing; ttfb_ms is TTFT proxy
            ttft = ttfb  # first-byte is first token in SSE stream
            done_timing = None
            for chunk in chunks:
                try:
                    obj = json.loads(chunk)
                except Exception:
                    continue
                if isinstance(obj, dict) and obj.get("done"):
                    done_timing = obj.get("timing")
            entry: dict = {"iter": i, "session_id": sid, "ttft_ms": ttft, "total_ms": lat, "done_timing": done_timing}
            if ttft is not None:
                ttft_samples.append(ttft)
            total_samples.append(lat)
            raw.append(entry)
        except Exception as exc:
            raw.append({"iter": i, "error": str(exc)})

    delta_samples = [t - f for t, f in zip(total_samples, ttft_samples[:len(total_samples)])]

    return {
        "test": "T12",
        "description": "Streaming TTFT (target TTFT p50 < 1500ms)",
        "target_ttft_p50_ms": 1500,
        "ttft_stats": summary_stats(ttft_samples),
        "total_stats": summary_stats(total_samples),
        "delta_stats": summary_stats(delta_samples),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# T13 — Multi-turn: turn2 on same warm session
# ---------------------------------------------------------------------------

def run_t13(cfg: dict, iterations: int = 5) -> dict:
    """T13: Multi-turn — prewarm → turn1 → turn2 on same session. Measure turn2. Target p50 < 1500ms."""
    turn2_samples: List[float] = []
    raw: List[dict] = []

    for i in range(iterations):
        sid = new_session_id()
        _prewarm(cfg, sid)
        time.sleep(2.0)
        try:
            result1, lat1, _ = invoke_runtime(cfg, {"message": BK001_CANCEL_MSG}, sid, _primary_qualifier(cfg))
            result2, lat2, _ = invoke_runtime(cfg, {"message": "Yes, please go ahead and cancel"}, sid, _primary_qualifier(cfg))
            turn2_samples.append(lat2)
            raw.append({"iter": i, "session_id": sid, "turn1_ms": lat1, "turn2_ms": lat2, "turn1_timing": result1.get("timing"), "turn2_timing": result2.get("timing")})
        except Exception as exc:
            raw.append({"iter": i, "session_id": sid, "error": str(exc)})

    return {
        "test": "T13",
        "description": "Multi-turn: turn2 on same warm session (target p50 < 1500ms)",
        "target_p50_ms": 1500,
        "turn2_stats": summary_stats(turn2_samples),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# T14 — Pre-warm curve
# ---------------------------------------------------------------------------

def run_t14(cfg: dict, iterations: int = 10) -> dict:
    """T14: Pre-warm curve — warmup then ping after [0.5,1,2,5,10]s waits."""
    wait_times = [0.5, 1.0, 2.0, 5.0, 10.0]
    per_wait: dict = {}

    for wait in wait_times:
        samples: List[float] = []
        raw: List[dict] = []
        for i in range(iterations):
            sid = new_session_id()
            try:
                _prewarm(cfg, sid)
                time.sleep(wait)
                lat = _ping(cfg, sid)
                samples.append(lat)
                raw.append({"iter": i, "session_id": sid, "latency_ms": lat})
            except Exception as exc:
                raw.append({"iter": i, "session_id": sid, "error": str(exc)})
        per_wait[str(wait)] = {"wait_s": wait, "samples": samples, "stats": summary_stats(samples), "raw": raw}

    return {
        "test": "T14",
        "description": "Pre-warm curve: warmup → varied wait → ping",
        "wait_times_s": wait_times,
        "per_wait": per_wait,
    }


# ---------------------------------------------------------------------------
# T15 — Parallel vs sequential tool calls
# ---------------------------------------------------------------------------

def run_t15(cfg: dict, iterations: int = 20) -> dict:
    """
    T15: Parallel vs sequential tool execution.
    Prewarm session → send BK-001 cancel message (triggers reservation→eligibility+refund parallel).
    Parse timing.tools for overlap; report total_tool_time vs sum_of_tool_durations; report parallel_tools flag.
    """
    samples: List[float] = []
    parallel_counts = {"true": 0, "false": 0}
    raw: List[dict] = []

    for i in range(iterations):
        sid = new_session_id()
        _prewarm(cfg, sid)
        time.sleep(2.0)
        payload = {"message": BK001_CANCEL_MSG}
        try:
            result, lat, _ = invoke_runtime(cfg, payload, sid, _primary_qualifier(cfg))
            timing = result.get("timing", {})
            tools = timing.get("tools", [])
            parallel = timing.get("parallel_tools", False)
            sum_tool_ms = sum(t.get("duration_ms", 0) for t in tools)
            # total tool wall-clock = max(end_ms) - min(start_ms) if tools present
            if tools:
                min_start = min(t.get("start_ms", 0) for t in tools)
                max_end = max(t.get("end_ms", 0) for t in tools)
                wall_tool_ms = max_end - min_start
            else:
                wall_tool_ms = None
            entry = {
                "iter": i, "session_id": sid, "total_ms": lat,
                "sum_tool_ms": sum_tool_ms, "wall_tool_ms": wall_tool_ms,
                "parallel_tools": parallel, "num_tools": len(tools),
                "tools": tools,
            }
            samples.append(lat)
            if parallel:
                parallel_counts["true"] += 1
            else:
                parallel_counts["false"] += 1
            raw.append(entry)
        except Exception as exc:
            raw.append({"iter": i, "error": str(exc)})

    return {
        "test": "T15",
        "description": "Parallel vs sequential tool execution for BK-001 cancel",
        "parallel_counts": parallel_counts,
        "total_stats": summary_stats(samples),
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# T16 — Connect hop (optional)
# ---------------------------------------------------------------------------

def run_t16(cfg: dict, iterations: int = 20) -> dict:
    """T16: Invoke via connect-sim Lambda vs direct. Skip if not configured."""
    connect_fn = cfg.get("connect_sim_lambda", "")
    if "<PLACEHOLDER" in connect_fn or not connect_fn:
        return {
            "test": "T16",
            "description": "Connect hop latency vs direct",
            "skipped": True,
            "reason": "connect_sim_lambda is placeholder — set in config after deploy.",
        }

    direct_samples: List[float] = []
    connect_samples: List[float] = []
    raw: List[dict] = []

    for i in range(iterations):
        # Direct
        sid = new_session_id()
        try:
            lat_direct = _ping(cfg, sid)
            direct_samples.append(lat_direct)
        except Exception as exc:
            lat_direct = None
            raw.append({"iter": i, "direct_error": str(exc)})

        # Connect-sim hop
        sid2 = new_session_id()
        try:
            connect_payload = {
                "agent_runtime_arn": _primary_arn(cfg),
                "session_id": sid2,
                "qualifier": _primary_qualifier(cfg),
                "payload": {"type": "ping"},
            }
            result, lat_connect = invoke_lambda(cfg, connect_fn, connect_payload, qualifier=None)
            connect_samples.append(lat_connect)
        except Exception as exc:
            lat_connect = None
            raw.append({"iter": i, "connect_error": str(exc)})

        if lat_direct is not None and lat_connect is not None:
            raw.append({"iter": i, "direct_ms": lat_direct, "connect_ms": lat_connect, "overhead_ms": lat_connect - lat_direct})

    return {
        "test": "T16",
        "description": "Connect hop vs direct invocation",
        "direct_stats": summary_stats(direct_samples),
        "connect_stats": summary_stats(connect_samples),
        "overhead_stats": summary_stats([c - d for c, d in zip(connect_samples, direct_samples)]) if connect_samples and direct_samples else {},
        "raw": raw,
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_TESTS = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T11", "T12", "T13", "T14", "T15", "T16"]


def run_all(cfg: dict, tests: List[str], iterations: int) -> dict:
    results: dict = {}
    t1_stats = None

    for t in tests:
        print(f"[{datetime.now().isoformat()} ] Running {t}...", flush=True)
        try:
            if t == "T1":
                r = run_t1(cfg, _iter_count(20, iterations))
                t1_stats = r.get("stats")
            elif t == "T2":
                r = run_t2(cfg, _iter_count(20, iterations))
            elif t == "T3":
                r = run_t3(cfg, _iter_count(20, iterations), t1_stats=t1_stats)
            elif t == "T4":
                r = run_t4(cfg, _iter_count(20, iterations))
            elif t == "T5":
                r = run_t5(cfg, _iter_count(20, iterations))
            elif t == "T6":
                r = run_t6(cfg, _iter_count(20, iterations))
            elif t == "T7":
                r = run_t7(cfg, _iter_count(20, iterations))
            elif t == "T8":
                r = run_t8(cfg, _iter_count(20, iterations))
            elif t == "T9":
                r = run_t9(cfg, _iter_count(5, iterations))
            elif t == "T10":
                r = run_t10(cfg, burst_size=15)
            elif t == "T11":
                r = run_t11(cfg, _iter_count(20, iterations))
            elif t == "T12":
                r = run_t12(cfg, _iter_count(20, iterations))
            elif t == "T13":
                r = run_t13(cfg, _iter_count(5, iterations))
            elif t == "T14":
                r = run_t14(cfg, _iter_count(10, iterations))
            elif t == "T15":
                r = run_t15(cfg, _iter_count(20, iterations))
            elif t == "T16":
                r = run_t16(cfg, _iter_count(20, iterations))
            else:
                r = {"test": t, "error": "unknown test"}
        except Exception as exc:
            r = {"test": t, "error": str(exc), "traceback": traceback.format_exc()}
        results[t] = r
        status = "SKIPPED" if r.get("skipped") else ("ERROR" if "error" in r else "OK")
        print(f"  -> {t} {status}", flush=True)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Voice-Agent Latency Benchmark")
    parser.add_argument("--config", default="benchmark/config.json", help="Path to config.json")
    parser.add_argument("--tests", default=",".join(ALL_TESTS), help="Comma-separated test IDs, e.g. T1,T2,T9")
    parser.add_argument("--iterations", type=int, default=None, help="Override default iteration count per test")
    parser.add_argument("--timestamp", default=None, help="Timestamp string for output filenames")
    parser.add_argument("--output-dir", default="results", help="Directory for output files")
    args = parser.parse_args()

    cfg = _load_config(args.config)
    tests = [t.strip().upper() for t in args.tests.split(",") if t.strip()]
    ts = args.timestamp or datetime.now().strftime("%Y%m%dT%H%M%S")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Starting benchmark: {tests}", flush=True)
    results = run_all(cfg, tests, args.iterations)

    raw_path = out_dir / f"voice-bench-raw-{ts}.json"
    with open(raw_path, "w") as f:
        json.dump({"timestamp": ts, "config_profile": cfg.get("aws", {}).get("profile"), "results": results}, f, indent=2, default=str)
    print(f"Raw results: {raw_path}", flush=True)

    # Generate summary report
    from benchmark.report import generate_report
    summary = generate_report(results, cfg, ts)
    summary_path = out_dir / f"voice-bench-summary-{ts}.txt"
    with open(summary_path, "w") as f:
        f.write(summary)
    print(f"Summary: {summary_path}", flush=True)
    print(summary)


if __name__ == "__main__":
    main()

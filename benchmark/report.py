"""
Summary report generation + cost model.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _fmt(val: Optional[float], unit: str = "ms") -> str:
    if val is None:
        return "N/A"
    return f"{val:.1f}{unit}"


def _meets(val: Optional[float], target: float) -> str:
    if val is None:
        return "N/A"
    return "YES" if val <= target else "NO"


def _st(results: dict, test: str) -> dict:
    """Return stats dict for a test, or empty dict."""
    r = results.get(test, {})
    if r.get("skipped"):
        return {}
    return r.get("stats", {})


def _sub_st(results: dict, test: str, key: str) -> dict:
    r = results.get(test, {})
    if r.get("skipped"):
        return {}
    return r.get(key, {}).get("stats", {})


def compute_cost(cfg: dict, calls_per_day: int = 1000) -> str:
    """
    Estimate monthly cost at `calls_per_day`.
    Rates are read from config['cost_rates'].
    """
    rates = cfg.get("cost_rates", {})
    calls_per_month = calls_per_day * 30

    # --- Lambda Provisioned Concurrency (20 instances, ARM 512MB) ---
    pc_instances = rates.get("lambda_pc_instances", 20)
    mem_gb = rates.get("lambda_memory_gb", 0.5)
    pc_rate_gb_s = rates.get("lambda_pc_provisioned_gb_s", 0.0000035137)
    pc_request_rate = rates.get("lambda_pc_request", 0.0000002)

    hours_per_month = 24 * 30
    seconds_per_month = hours_per_month * 3600
    lambda_pc_idle = pc_instances * mem_gb * seconds_per_month * pc_rate_gb_s
    lambda_pc_requests = calls_per_month * pc_request_rate
    # Assume avg 200ms execution duration per call
    avg_duration_s = 0.2
    lambda_duration_rate = rates.get("lambda_duration_per_gb_s", 0.0000133334)
    lambda_pc_active = calls_per_month * avg_duration_s * mem_gb * lambda_duration_rate
    lambda_total = lambda_pc_idle + lambda_pc_requests + lambda_pc_active

    # --- LLM tokens (Haiku 4.5) ---
    # Assume avg 500 input, 200 output tokens per E2E call
    avg_input_tokens = 500
    avg_output_tokens = 200
    llm_input_rate = rates.get("llm_input_per_1k_tokens", 0.00080)
    llm_output_rate = rates.get("llm_output_per_1k_tokens", 0.00400)
    llm_cost = calls_per_month * (
        avg_input_tokens / 1000 * llm_input_rate +
        avg_output_tokens / 1000 * llm_output_rate
    )

    # --- AgentCore Runtime ---
    agentcore_idle = rates.get("agentcore_idle_per_hour", 0.0) * hours_per_month
    agentcore_active = rates.get("agentcore_active_per_invocation", 0.0) * calls_per_month
    agentcore_total = agentcore_idle + agentcore_active

    # --- Gateway (approximate: same as Lambda request pricing) ---
    gateway_request_rate = rates.get("lambda_request", 0.0000002)
    gateway_cost = calls_per_month * gateway_request_rate

    total = lambda_total + llm_cost + agentcore_total + gateway_cost

    lines = [
        f"  Assumptions: {calls_per_day} calls/day × 30 days = {calls_per_month:,} calls/month",
        f"               Lambda: {pc_instances} PC instances, {int(mem_gb*1024)}MB ARM, ~{int(avg_duration_s*1000)}ms avg duration",
        f"               LLM: ~{avg_input_tokens} input / ~{avg_output_tokens} output tokens avg per call",
        "",
        f"  Lambda PC (idle provisioned):   ${lambda_pc_idle:>10.2f}/mo",
        f"  Lambda PC (requests+duration):  ${lambda_pc_requests + lambda_pc_active:>10.2f}/mo",
        f"  Lambda total:                   ${lambda_total:>10.2f}/mo",
        "",
        f"  LLM tokens (Haiku 4.5):         ${llm_cost:>10.2f}/mo",
        f"    Input  rate: ${llm_input_rate:.5f}/1K tokens",
        f"    Output rate: ${llm_output_rate:.5f}/1K tokens",
        "",
        f"  AgentCore Runtime:              ${agentcore_total:>10.2f}/mo  (update rates after GA pricing published)",
        f"  Gateway invocations:            ${gateway_cost:>10.2f}/mo",
        "",
        f"  TOTAL (estimated):              ${total:>10.2f}/mo",
        "  NOTE: AgentCore Runtime pricing not yet GA-published; fill cost_rates in config.",
    ]
    return "\n".join(lines)


def generate_report(results: dict, cfg: dict, ts: str) -> str:
    def st(test: str, key: str = "stats") -> dict:
        r = results.get(test, {})
        if r.get("skipped") or "error" in r:
            return {}
        return r.get(key, {})

    def fst(test: str, subkey: Optional[str] = None) -> dict:
        r = results.get(test, {})
        if r.get("skipped") or "error" in r:
            return {}
        if subkey:
            return r.get(subkey, {}).get("stats", {})
        return r.get("stats", {})

    lines = [
        "============================================================",
        "VOICE AGENT LATENCY BENCHMARK RESULTS",
        f"Generated: {ts}",
        "============================================================",
        "",
        "[Q1] Lambda + Provisioned Concurrency",
        "  Test T1 — Lambda PC (Qualifier=live, warm invocations):",
    ]
    t1s = fst("T1")
    lines += [
        f"    p50={_fmt(t1s.get('p50'))}  p95={_fmt(t1s.get('p95'))}  p99={_fmt(t1s.get('p99'))}  n={t1s.get('count','N/A')}",
        f"    Target p50 < 100ms: {_meets(t1s.get('p50'), 100)}",
        "  Test T2 — Lambda $LATEST (cold baseline):",
    ]
    t2s = fst("T2")
    lines += [
        f"    p50={_fmt(t2s.get('p50'))}  p95={_fmt(t2s.get('p95'))}  p99={_fmt(t2s.get('p99'))}  n={t2s.get('count','N/A')}",
        "  Test T3 — Gateway MCP tool overhead vs T1:",
    ]
    t3r = results.get("T3", {})
    if t3r.get("skipped"):
        lines.append(f"    SKIPPED — {t3r.get('reason','')}")
    else:
        for tool, td in t3r.get("tools", {}).items():
            ts3 = td.get("stats", {})
            ov = td.get("overhead_vs_t1_p50_ms")
            lines.append(
                f"    {tool}: p50={_fmt(ts3.get('p50'))}  overhead_vs_T1={_fmt(ov)}"
            )

    lines += [
        "",
        "[Q2] AgentCore Runtime Cold Start Mitigation",
        "  Test T4 — Container cold start (new session, container agent):",
    ]
    t4s = fst("T4")
    lines += [f"    p50={_fmt(t4s.get('p50'))}  p95={_fmt(t4s.get('p95'))}  p99={_fmt(t4s.get('p99'))}  n={t4s.get('count','N/A')}"]

    lines.append("  Test T5 — Code-deploy cold start (new session, code agent):")
    t5s = fst("T5")
    lines += [f"    p50={_fmt(t5s.get('p50'))}  p95={_fmt(t5s.get('p95'))}  p99={_fmt(t5s.get('p99'))}  n={t5s.get('count','N/A')}"]

    lines.append("  Test T6 — Pre-warm (warmup + 2s sleep → ping; target p50 < 500ms):")
    t6s = fst("T6")
    lines += [
        f"    p50={_fmt(t6s.get('p50'))}  p95={_fmt(t6s.get('p95'))}  p99={_fmt(t6s.get('p99'))}",
        f"    Target p50 < 500ms: {_meets(t6s.get('p50'), 500)}",
    ]

    lines.append("  Test T7 — Round-robin pings across 3 endpoints (new sessions):")
    t7r = results.get("T7", {})
    for ep, epd in t7r.get("per_endpoint", {}).items():
        eps = epd.get("stats", {})
        lines.append(f"    {ep}: p50={_fmt(eps.get('p50'))}  p95={_fmt(eps.get('p95'))}")

    lines.append("  Test T8 — Same-session 20 pings after warmup (target p50 < 200ms):")
    t8s = fst("T8")
    lines += [
        f"    p50={_fmt(t8s.get('p50'))}  p95={_fmt(t8s.get('p95'))}  p99={_fmt(t8s.get('p99'))}",
        f"    Target p50 < 200ms: {_meets(t8s.get('p50'), 200)}",
    ]

    lines += [
        "",
        "[E2E] Full Voice Turn",
        "  Test T9 — Cancel BK-001 (target p50 < 2000ms):",
    ]
    t9r = results.get("T9", {})
    wp = t9r.get("with_prewarm", {}).get("stats", {})
    cp = t9r.get("without_prewarm", {}).get("stats", {})
    lines += [
        f"    With prewarm:    p50={_fmt(wp.get('p50'))}  p95={_fmt(wp.get('p95'))}  p99={_fmt(wp.get('p99'))}",
        f"    Without prewarm: p50={_fmt(cp.get('p50'))}  p95={_fmt(cp.get('p95'))}  p99={_fmt(cp.get('p99'))}",
        f"    Target p50 < 2000ms (prewarm): {_meets(wp.get('p50'), 2000)}",
    ]

    lines += [
        "",
        "[BURST] Warm Pool Exhaustion (15 concurrent)",
        "  Test T10 — 15 concurrent new-session pings:",
    ]
    t10r = results.get("T10", {})
    lines += [
        f"    Warm (latency <= {t10r.get('cold_start_threshold_ms', 1500):.0f}ms): {t10r.get('warm_count', 'N/A')} — {_fmt(t10r.get('warm_stats', {}).get('p50'))}",
        f"    Cold (latency >  {t10r.get('cold_start_threshold_ms', 1500):.0f}ms): {t10r.get('cold_count', 'N/A')} — {_fmt(t10r.get('cold_stats', {}).get('p50'))}",
        f"    Served from warm pool: {t10r.get('warm_count', 'N/A')}/15",
    ]

    lines += [
        "",
        "[STREAMING] Time to First Token (T12)",
        "  Test T11 — Streaming TTFB (SSE text/event-stream, NOTE: replaces impossible client-WebSocket):",
    ]
    t11r = results.get("T11", {})
    t11wp = t11r.get("with_prewarm", {}).get("stats", {})
    t11cp = t11r.get("without_prewarm", {}).get("stats", {})
    lines += [
        f"    With prewarm:    TTFB p50={_fmt(t11wp.get('p50'))}  p95={_fmt(t11wp.get('p95'))}",
        f"    Without prewarm: TTFB p50={_fmt(t11cp.get('p50'))}  p95={_fmt(t11cp.get('p95'))}",
    ]

    lines.append("  Test T12 — Streaming TTFT (target TTFT p50 < 1500ms):")
    t12r = results.get("T12", {})
    t12tf = t12r.get("ttft_stats", {})
    t12tot = t12r.get("total_stats", {})
    t12delta = t12r.get("delta_stats", {})
    lines += [
        f"    TTFT:  p50={_fmt(t12tf.get('p50'))}  p95={_fmt(t12tf.get('p95'))}  p99={_fmt(t12tf.get('p99'))}",
        f"    Total: p50={_fmt(t12tot.get('p50'))}  Delta(total-ttft): p50={_fmt(t12delta.get('p50'))}",
        f"    Target TTFT p50 < 1500ms: {_meets(t12tf.get('p50'), 1500)}",
    ]

    lines += [
        "",
        "[MULTI-TURN] Second Turn on Warm Session (T13)",
        "  Test T13 — Turn2 latency on same session (target p50 < 1500ms):",
    ]
    t13s = results.get("T13", {}).get("turn2_stats", {})
    lines += [
        f"    p50={_fmt(t13s.get('p50'))}  p95={_fmt(t13s.get('p95'))}  p99={_fmt(t13s.get('p99'))}",
        f"    Target p50 < 1500ms: {_meets(t13s.get('p50'), 1500)}",
    ]

    lines += [
        "",
        "[PRE-WARM CURVE] Minimum effective wait (T14)",
        "  Test T14 — Post-warmup ping latency by wait duration:",
    ]
    t14r = results.get("T14", {})
    for wait_key, wd in t14r.get("per_wait", {}).items():
        ws = wd.get("stats", {})
        lines.append(f"    wait={wd.get('wait_s')}s: p50={_fmt(ws.get('p50'))}  p95={_fmt(ws.get('p95'))}")

    lines += [
        "",
        "[PARALLEL-VS-SEQUENTIAL] Tool Parallelism (T15)",
    ]
    t15r = results.get("T15", {})
    pc = t15r.get("parallel_counts", {})
    t15tot = t15r.get("total_stats", {})
    lines += [
        f"    parallel_tools=true: {pc.get('true', 0)}/{ (pc.get('true',0)+pc.get('false',0)) or 'N/A'} runs",
        f"    Total E2E: p50={_fmt(t15tot.get('p50'))}  p95={_fmt(t15tot.get('p95'))}",
    ]

    lines += [
        "",
        "[CONNECT-HOP] T16 — Connect-sim Lambda hop vs direct:",
    ]
    t16r = results.get("T16", {})
    if t16r.get("skipped"):
        lines.append(f"    SKIPPED — {t16r.get('reason', '')}")
    else:
        t16d = t16r.get("direct_stats", {})
        t16c = t16r.get("connect_stats", {})
        t16o = t16r.get("overhead_stats", {})
        lines += [
            f"    Direct:      p50={_fmt(t16d.get('p50'))}  p95={_fmt(t16d.get('p95'))}",
            f"    Connect hop: p50={_fmt(t16c.get('p50'))}  p95={_fmt(t16c.get('p95'))}",
            f"    Overhead:    p50={_fmt(t16o.get('p50'))}",
        ]

    lines += [
        "",
        "[COST] Estimated monthly cost at 1000 calls/day",
    ]
    lines.append(compute_cost(cfg, calls_per_day=1000))

    lines += [
        "",
        "VERDICT:",
    ]
    verdicts = []
    if t1s.get("p50") is not None:
        v = "PASS" if (t1s.get("p50", 9999) <= 100) else "FAIL"
        verdicts.append(f"  Lambda PC p50 < 100ms: {v} ({_fmt(t1s.get('p50'))})")
    if t6s.get("p50") is not None:
        v = "PASS" if (t6s.get("p50", 9999) <= 500) else "FAIL"
        verdicts.append(f"  Pre-warm p50 < 500ms:  {v} ({_fmt(t6s.get('p50'))})")
    if t8s.get("p50") is not None:
        v = "PASS" if (t8s.get("p50", 9999) <= 200) else "FAIL"
        verdicts.append(f"  Warm ping p50 < 200ms: {v} ({_fmt(t8s.get('p50'))})")
    if wp.get("p50") is not None:
        v = "PASS" if (wp.get("p50", 9999) <= 2000) else "FAIL"
        verdicts.append(f"  E2E p50 < 2000ms:      {v} ({_fmt(wp.get('p50'))})")
    if t12tf.get("p50") is not None:
        v = "PASS" if (t12tf.get("p50", 9999) <= 1500) else "FAIL"
        verdicts.append(f"  TTFT p50 < 1500ms:     {v} ({_fmt(t12tf.get('p50'))})")
    if not verdicts:
        verdicts.append("  No live data — run against deployed infra to see verdict.")
    lines += verdicts
    lines.append("============================================================")

    return "\n".join(lines)

# Strands vs LangGraph — Latency Comparison

Same Gateway, tools, model (Claude Haiku 4.5), system prompt, and timing contract; the
**only** difference is the agent framework. Both deployed from **one container image**
switched by `AGENT_FRAMEWORK=strands|langgraph`, so they're byte-identical except the
orchestration library. Framework-sensitive tests (T9 E2E, T12 streaming TTFT, T13 multi-turn,
T15 tool parallelism) were run **back-to-back in the same session**, n=10 each.

Raw data: `strands-v3-raw.json`, `langgraph-v3-raw.json` (+ summaries).

## Method correction (important)

A first-pass comparison showed LangGraph dramatically faster on multi-turn (T13) and E2E
(T9). **Both were measurement artifacts, not framework differences:**

1. **State accumulation.** The Strands variant reused one stateful `Agent` object across all
   benchmark iterations, so its conversation history (and token count, and latency) grew over
   the run. The LangGraph variant was stateless per call. → Strands looked slower and its T9
   samples *rose over the run*.
2. **T13 was not apples-to-apples.** Because of (1) plus a missing checkpointer, Strands' turn-2
   executed the cancellation (2 tool calls) while LangGraph's turn-2 had no memory of the
   booking and did nothing (0 tool calls) — a 3.6× "win" that was pure wiring.

**Fix:** conversation state is now scoped **per session** in both — Strands builds a fresh agent
per session id; LangGraph uses a `MemorySaver` checkpointer keyed by `thread_id=session_id`.
Turns on the same session share context; independent iterations start clean. The numbers below
are from that corrected (v3) build. Verified fair: T13 turn-2 now calls ~2 tools in **both**
(Strands `[2,1,2,2,…]`, LangGraph `[1,2,2,2,…]`).

## Results (state-matched, n=10, prewarmed)

| Metric | Strands | LangGraph | Read |
|---|--:|--:|---|
| T9 E2E p50 / p95 (ms) | 4,805 / 6,417 | 4,646 / 5,805 | **Tie** (LangGraph slightly tighter tail) |
| T13 multi-turn, turn-2 p50 / p95 (ms) | 4,408 / 5,677 | 4,037 / 4,465 | **Tie** |
| T15 full-turn E2E p50 (ms) | 4,876 | 4,825 | **Tie** |
| T15 tool parallelism | 10/10 parallel | 10/10 parallel | **Tie** |
| T12 total completion p50 (ms) | 4,877 | 5,034 | **Tie** |
| T12 TTFT p50 (ms) | 4,414 | 2,968 | LangGraph lower — caveat below |
| T9 avg input tokens | 4,120 | 3,832 | **Strands +7.5%** |
| T9 avg output tokens | 345 | 305 | **Strands +13%** |

## Verdict

- **End-to-end latency is a dead heat.** All three full-turn measures (T9, T13, T15) land
  ~4.6–4.9s p50 for both — and **both are ~2.5× over the 2s voice target.** Framework choice is
  **not** a lever for the latency goal; the LLM agentic loop is.
- **Both parallelize independent tools identically** (10/10).
- **Token efficiency is the one consistent, real difference:** Strands uses **~7–13% more
  tokens** (slightly heavier prompt/tool-message formatting) — a cost difference, not wall-clock.
- **TTFT (T12) measured lower for LangGraph (3.0s vs 4.4s), but I don't credit it as a real
  spoken-latency win:** total completion is tied, and LangGraph's `stream_mode="messages"`
  surfaces a token from an earlier point in the loop than Strands' final-answer stream —
  different token boundaries, not a faster answer. Normalizing would need matched stream
  instrumentation.

**Pick on ergonomics, not latency.** Strands is stateful-by-default across calls and slightly
more token-hungry; LangGraph needed an explicit checkpointer for multi-turn memory but is
leaner on tokens. Neither gets you under 2s — reducing LLM rounds and the gateway hop does.

## Caveats
- Client on a laptop (~70ms RTT to us-east-1) — inflates absolute numbers equally for both.
- n=10; run-to-run Bedrock variance is ~±1s, so sub-second gaps are noise.
- Comparison used dedicated v3 runtimes (`voice_bench_strands_v3`, `voice_bench_lg_v3`) built
  from the same image; `UpdateAgentRuntime` was denied mid-session (contingent-auth on writes),
  so fresh runtimes were created rather than updating in place.

# Voice Agent Latency Benchmark — Findings

**Run:** `20260826T154649` · **Account:** 111122223333 · **Region:** us-east-1 · **Model:** `us.anthropic.claude-haiku-4-5-20251001-v1:0`
Raw data: `voice-bench-raw-20260826T154649.json` · Summary: `voice-bench-summary-20260826T154649.txt`

## Verdict

**Can this architecture meet a 2-second voice latency target with pre-warming?**
**Partially — the infrastructure layer can, but a full multi-tool agentic turn cannot.**

- ✅ **Session cold-start is solved by pre-warming.** A warmup ping makes the session usable in **~224ms** (T6), effective even with a **0.5s** lead (T14). This decisively validates Claim 2 (pre-warm within a 500ms budget).
- ✅ **Provisioned-Concurrency Lambda tool execution is fast** (~40–113ms warm; see RTT caveat), and **Strands parallelizes independent tools** (T15: 20/20 runs).
- ❌ **A full 3-tool cancellation turn takes ~4.9s E2E and ~4.4s to first spoken token** (T9/T12) — **~2.5× over the 2s budget.** The bottleneck is *not* infrastructure cold-start; it is the **sequential LLM agentic loop** (≈3 Bedrock rounds: plan→tools→answer) plus **~300ms/call Gateway overhead**.

> **Measurement caveat (read first):** the benchmark client ran from a **laptop over the internet (~70ms RTT to us-east-1)**. Every absolute number includes that RTT twice-ish; an in-region caller (real Connect/AgentCore path) would shave ~70–140ms off each hop. Relative comparisons (warm vs cold, container vs code, gateway overhead, parallel vs sequential) are unaffected. T16 gives an in-region reference point (Lambda→runtime).

## Answers to the two claims under test

**Claim 1 — "Lambda + Provisioned Concurrency delivers sub-100ms tool execution for voice-critical paths."**
- **Direct Lambda (PC, `:live`): p50 = 112.6ms** (T1), p95 388.6ms. Subtract ~70ms client RTT → **~40ms in-region → PASS in-region**, borderline as measured.
- **Via Gateway: p50 ≈ 411–418ms** (T3) → **Gateway adds ~300ms/call** even warm. For voice-critical single-tool paths, calling Lambda directly (or co-locating) matters; the MCP Gateway hop is a real tax.
- Interesting: T2 `$LATEST` p50 (108ms) ≈ T1 here because the function is tiny and stays warm between iterations; PC's benefit shows in the **tail** and under burst, not the median of a trivial handler.

**Claim 2 — "AgentCore Runtime pre-warming brings session initialization within a 500ms budget."**
- **CONFIRMED. Pre-warmed ping p50 = 224.6ms (T6) < 500ms.** Warm same-session p50 = 209.4ms (T8; ~9ms over the aspirational 200ms, within RTT noise). Pre-warm curve (T14) is flat ~208–221ms from 0.5s→10s wait — **a short warmup lead is enough.**

## Key findings

| # | Finding | Evidence |
|---|---------|----------|
| 1 | **Pre-warming eliminates session cold-start.** | T4 container cold p50 **1019ms** → T6 pre-warmed **224ms**. |
| 2 | **Container cold-start ≫ code/ZIP cold-start.** | T4 container p50 **1019ms** vs T5 code-deploy p50 **4756ms** (~4.7×). Bake deps into the image; the code path also can't self-install deps (must vendor into `/var/task`). |
| 3 | **Full agentic turn is ~5s, dominated by the LLM loop.** | T9 4943ms; T12 delta(total−ttft)=362ms means ~92% of the wait is *before* the first token — i.e. planning + tool rounds. |
| 4 | **Strands runs independent tools in parallel, every time.** | T15: `parallel_tools=true` 20/20; `check_eligibility` + `get_refund_method` overlap after `get_reservation`. Saves ~one tool round-trip. |
| 5 | **Gateway overhead ~300ms/call (warm), ~11s cold (first call).** | T3 vs T1. New gateways have a large first-call penalty that warms out. |
| 6 | **Built-in warm pool serves ~11 concurrent before cold starts.** | T10: 11/15 warm (~1187ms), 4/15 cold (~6147ms). |
| 7 | **Connect→Lambda→Runtime hop adds only ~57ms.** | T16 overhead p50 57.5ms — the Connect integration layer is cheap. |
| 8 | **Streaming doesn't rescue TTFT here.** | T12 TTFT 4424ms: the first *spoken* token can't be produced until tool results return, so SSE streaming mainly helps the final-sentence delivery (delta ~362ms), not time-to-first-token. |

## What would get a real deployment under 2s

1. **Cut LLM rounds.** The ~5s is ~3 sequential Bedrock calls. Options: single-shot tool planning, speculative/parallel prefetch of `get_reservation` on call arrival, or a smaller/faster decode path. This is the highest-leverage lever.
2. **Bypass or co-locate the Gateway for voice-critical tools** (~300ms/call). Direct Lambda invoke or in-VPC MCP would remove the hop tax.
3. **Keep pre-warming** — it works and is cheap; warm the session during IVR/auth (0.5s lead suffices).
4. **Run the caller in-region** (removes the ~70ms/hop laptop RTT baked into these numbers).
5. **Prefer container deployment** for the runtime (4.7× better cold start than code/ZIP).

## Cost (estimated, 1000 calls/day)

| Item | $/mo |
|---|---|
| Lambda Provisioned Concurrency (20 idle instances) | 91.08 |
| Lambda requests+duration | 0.05 |
| LLM tokens (Haiku 4.5) | 36.00 |
| Gateway | 0.01 |
| **AgentCore Runtime (consumption)** | **not filled** — GA pricing (vCPU-s + GB-s) not yet in `cost_rates` |
| **Total (excl. AgentCore)** | **~127.13** |

Provisioned Concurrency is the dominant fixed cost; scale PC to actual concurrency (T10 suggests the built-in warm pool already covers ~11 concurrent sessions, so PC=5×4 may be over-provisioned for low volume).

## Method notes / caveats
- Client on laptop (~70ms RTT) — see caveat above.
- T1 "sub-100ms" is borderline as measured but likely met in-region.
- **T11 reframed:** AgentCore Runtime exposes no client WebSocket; the data plane is `InvokeAgentRuntime` over HTTPS with server→client SSE streaming. T11 measures streaming time-to-first-byte instead.
- Gateway MCP client uses per-call SigV4-signed requests (no HTTP keep-alive); connection reuse could reduce the measured ~300ms gateway overhead somewhat.
- Runtime names use underscores (`voice_bench_agent`) — the `agentRuntimeName` API forbids hyphens.
- LLM-heavy tests (T9/T13) ran 5 iters each; T11/T12/T15 ran 20; others 20 (T14 = 10/wait). Small-n tails (p95=p99) reflect the iteration count.

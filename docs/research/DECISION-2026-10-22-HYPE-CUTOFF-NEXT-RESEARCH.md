# NoFomo decision: preserve HYPE terminal evidence and prepare BTC H1/H4
Date: 2026-10-22 (Europe/Bratislava)
Status: **PREPARATION / READ-ONLY PLAN ONLY — NOT AN APPROVAL TO RUN, LOCK, MERGE, DEPLOY OR TRADE**
Base branch: `main` at `b200950cffa6c349a6044c4ff70d28f9d7b9e8dd`.

## Decision
1. Close the **existing HYPE/USDT 5m v8 Official and Scale paper forward tests** under their existing gates. Do not change settings, simulation/optimizer code, costs, scheduler, frontend, market-data reads, DB contents or runtime bindings.
2. Prepare an isolated, BTC-only, long-only slow-trend hypothesis **H1** and a volatility-managed BTC benchmark **H4**. Their **draft** is in `docs/research/DRAFT-BTC-H1-H4-RESEARCH-SPEC-2026-10-22.md`.
3. Retain the frozen 90-day ZEC Strategy B TEST-SPEC-002 through 2026-12-23 11:59:59.999 UTC; do not inspect partial performance or adjust B based on HYPE.
4. Defer all HYPE v9, high-frequency RSI/BB rescue filters, same N=24 breakout coin repetitions, Strategy D implementation/merge, ledger cutover and live trading. Leave the existing Strategy D R2 **as a separate unapproved draft**.

## Verified HYPE clocks — 2026-10-22 read-only Supabase query
Source: `public.simulation_runs.progress.trading_start_time` interpreted as Unix **milliseconds**, not `started_at`, warmup time or `finished_at`.

| Test | Verified trading start UTC | Minimum 30 x 24h time gate UTC | Minimum time gate Slovakia (CET, daylight saving ends Oct 25) |
|---|---|---|---|
| Official 100 USDT / 50 USDT | 2026-09-26 07:18:59.654 UTC | 2026-10-26 07:18:59.654 UTC | **2026-10-26 08:18:59.654 CET** |
| Scale 300 USDT / 150 USDT | 2026-09-27 07:40:51.329 UTC | 2026-10-27 07:40:51.329 UTC | **2026-10-27 08:40:51.329 CET** |

The full gate is **30 days AND at least 30 closed trades**, independently for each test. Reaching 30 trades before the time gate does **not** end a test. A Scale 30th trade occurring after its time gate moves the eligible cutoff later.

Observed rolling recalculations as of 2026-10-22 approx. 14:06–14:07 CEST (not terminal, not immutable):
- Official: 30 closed trades, 100 -> 96.3147 USDT, win rate 60.0%, net expectancy -0.11023197 USDT/trade, reported DD 7.72%.
- Scale: 29 closed trades, 300 -> 287.9803 USDT, win rate 58.6%, net expectancy -0.37468034 USDT/trade, reported DD 7.77%.
- Model label `current_okx_book` on both. Rolling changes in historic execution costs mean these snapshots are **not independent, certified trade tapes**.
Never sum distinct `simulated_trades` rows or repeated recalculations as though they were new unique executions.

## Terminal evidence protocol (no runtime change authorized)
At the eligible terminal instant for each test, define:
- `S` = verified trading-start timestamp above;
- `c30` = 30th eligible closed-trade timestamp on **one identified single tape** (sorted deterministically);
- `T* = max(S + 30*24h, c30)`.
- Terminal sample: eligible trades closed from S through T*, plus open-position mark-to-market exposure for drawdown, as defined by the reviewed accounting convention.

The existing client-driven simulation is a **rolling replay**, not a durable exchange-order paper ledger. `use_current_cost_model=true` can fetch different L2 books on successive evaluations. Consequently an available snapshot with `finished_at > T*` is not automatically a certified exact-cutoff snapshot. The cost observation timestamps, evaluation `test_end`, `finished_at`, tape identity and market snapshot provenance must be distinguished.

At closeout, prepare a **read-only packet** for each test, using already stored evidence only:
- `S`, eligibility timing, identified `c30`, `T*`, time zone and source field.
- A single source `simulation_runs.id`, `finished_at`, `test_end`, `progress.paper_label`, `progress.trading_start_time`, `summary.closed_trades`, `summary.realized_profit`, `summary.unrealized_profit`, `summary.portfolio_value`, `summary.expectancy`, net PF computed on an internally coherent single tape, and backend `summary.max_drawdown_percent`.
- Single-tape trade IDs/order, exact timestamps, costs, cost profile/book observation timestamps, raw source coverage, hashes where truly present; label missing hashes/provenance **missing**, never fabricate.
- Open positions at cutoff separately (not in closed-trade PF, included in DD).
- Any inability to prove a reproducible cutoff and cost/tape identity must be explicit **INVALID / UNVERIFIABLE**, not an economic FAIL or a favorable later-run substitute. No retroactive cost re-estimation permitted.
- Any changes to capture machinery would be prospective and separately approved **before** use; this preparation authorizes no such changes.

Numerical **proposal** from `docs/strategy-d-and-hype-decision/docs/preregistered-hype-v8-paper-decision.md`:
Official economic PASS requires closed trades >=30 after the 30-day gate, net closed-trade expectancy >0, net PF >=1.10 and max DD <=15%. Scale is a separate diagnostic; there is **no independent Scale vote** to override Official.
**Critical validity disclosure:** this economic decision rule was written after inspection of interim HYPE results, so cannot be represented as a clean pre-experiment blind preregistration. The R2 proposal is **not finally reviewed/locked**; this document does not silently adopt it as approved production policy.
If eligible evidence is invalid or absent, use explicit **NOT_EVALUATED / INVALID / INSUFFICIENT_EVIDENCE** with reasons; do not invent a PASS/FAIL.

## Near-term sequence
1. Now: read-only integrity inspection, prepare cutoff evidence checklist; do not change running jobs.
2. 2026-10-26 no earlier than 07:18:59.654 UTC: Official time eligible, subject to accurate 30th-trade cutoff and evidence; assess only with validated exact convention.
3. 2026-10-27 no earlier than 07:40:51.329 UTC: Scale time eligible, subject to its actual 30th closed trade and evidence.
4. After both eligibility checks: produce one dated review with valid economic verdict **or** precise reason for inconclusive/invalid evidence. No automatic paper-ledger migration, no trading, no revised HYPE stop/trail.
5. In parallel: review the draft H1/H4 spec **without reading frozen B outcomes**. After separately approved spec lock, create isolated research code and synthetic tests. Fresh strategy performance evaluation/data download requires separate authorization.

## References / conflict boundaries
- GitHub `main` `b200950`: `backend/app/simulation.py`, `backend/app/costs.py`, `frontend/src/main.tsx`, `backend/app/optimizer.py`.
- Separate unmerged branch `docs/strategy-d-and-hype-decision` — HYPE pre-terminal decision and Strategy D R2 are **proposals, not approved merge/lock**.
- Separate unmerged Claude branch `claude/beautiful-bardeen-n4xe2x` — audit `docs/research/RESEARCH-AUDIT-2026-10-strategy-candidates.md` is a research document, not executable alpha approval.
- TEST-SPEC-002 B frozen through 2026-12-23 (read-only blind conditions).
- DB `paper_trade_ledger`, `paper_signals`, `paper_orders`, `paper_fills` and `paper_strategy_runtime_bindings` had zero rows at 2026-10-22 inspection; do not equate HYPE rolling replays to an active order-level paper backend.

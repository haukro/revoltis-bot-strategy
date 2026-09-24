FOR GROK REVIEW — TEST-SPEC-003

PROJECT STATE

Strategy A ZEC mean reversion is closed as research-only after a negative 3x90d economic scorecard.

TEST-SPEC-002 ZEC TSMOM B v1 is fully frozen and running as a blind 90-day forward test:
2026-09-24 12:00 UTC → 2026-12-23 11:59:59.999 UTC.
It must not be modified.

We now want a separate cross-market replication test on BTC, without touching ZEC B v1.

CURRENT QUESTION

Is TEST-SPEC-003 BTC TSMOM C v1 methodologically clean enough to lock and run once on a clean historical BTC fold?

REPLICATION LOGIC

The point is NOT to optimize BTC.

We intentionally copy unchanged:
- N=24
- Wilder ATR(24)
- 2x ATR initial/chandelier stop
- 1h signals
- 5m execution
- long + short
- max 1 position
- exact next-hour-boundary entry
- no timeout
- no additional filters

If this mechanism survives independently on BTC, that is evidence of portability.
If it fails, we do not tune these parameters on the same fold.

MARKET

BTC/USDT spot on OKX
5m execution
fully closed 1h UTC signal bars
50 USDT gross entry notional
no leverage

SIGNAL

range_high = max high of previous 24 fully closed 1h bars
range_low = min low of previous 24 fully closed 1h bars

signal candle excluded from range

long = close > range_high
short = close < range_low

Exact entry:
5m.open_time = signal_1h.close_time + 1ms

Missing exact entry bar => official run invalid.
No delayed fallback.

ATR

Wilder ATR(24)
signal bar TR included
following incomplete hour excluded

EXIT

initial stop = 2x ATR

chandelier:
long = peak completed 5m high - 2x latest closed 1h ATR
short = trough completed 5m low + 2x latest closed 1h ATR

stop never loosens

Active stop is frozen before each 5m candle.
Current candle extrema can only tighten stop for the next candle.
Gap-through stop exits at 5m open.
Otherwise touch exits at stop.
Same-bar stop-out after entry is allowed.

COSTS

BTC gets its own frozen cost snapshot, not ZEC costs.

Proposed snapshot:
- book_ts 1790275677755
- fee 0.001 per side
- entry_cost_rate 0.0010005926681029335
- exit_cost_rate 0.0010005926681029335
- impact 0 at 50 USDT
- current-book snapshot, not historical L2

Shorts remain research marks on spot prices.
No claim of borrow-free spot-short executability.
No perp/funding substitution.

PROPOSED OFFICIAL FOLD

2025-09-02 00:00:00.000 UTC
to
2025-11-30 23:59:59.999 UTC

90 days.

Project audit found no stored evaluation starting before 2025-12-01 and no prior BTC TEST-SPEC-003 result on this fold.

Dec 2025-Sep 2026 is deliberately not used because BTC was already inspected there in the ZEC momentum pre-screen.

Before lock/run we will certify only data availability/continuity, not Strategy C performance.

ETH OVERLAP

ETH overlap is diagnostic only, NOT a PASS gate.

Definition:
- ETH/USDT spot on OKX
- same 1h N=24 breakout
- same direction on T-1h, T or T+1h
- report overlap among taken BTC trades with complete ETH timestamps
- report completeness
- does not alter verdict

Reason for no overlap gate:
this is a replication test of BTC TSMOM profitability, not a test that BTC is independent of ETH.

PASS / FAIL

If <20 closed trades:
INSUFFICIENT_SAMPLE

Else PASS only if all:
1. net expectancy/trade > 0
2. PF >= 1.10
3. if both long and short trade, both side net PnLs > 0

Else FAIL.

No DD or B&H gate.
Those are reported only.

FORBIDDEN

- no N grid
- no ATR grid
- no multiplier grid
- no volume/volatility filters
- no ETH/ZEC entry filter
- no long-only/short-only variants
- no timeout
- no opposite breakout exit
- no Strategy A exit
- no Dec 2025-Sep 2026 PASS/FAIL
- no changing BTC cost snapshot after lock
- no using this test to alter frozen ZEC B v1

REQUEST

Return exactly:

1. BLOCKING flaws that must be clarified before TEST-SPEC-003 is locked
2. NON-BLOCKING caveats
3. whether using the exact same TSMOM mechanics on BTC is a valid cross-market replication design
4. whether ETH overlap should remain diagnostic only, and why
5. LOCK VERDICT: clean enough to lock as written / not clean enough

Do not optimize parameters.
Do not suggest selecting the best of multiple coins or variants.

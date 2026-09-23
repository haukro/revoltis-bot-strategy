# Research checks, without a new optimizer job

The XRP/DOGE/ZEC/UNI/SUI research run remains rejected. This change neither
rewrites that lock nor reruns its grid, training, holdout or changed parameters.

## Persistence and version identity

`strategy_versions.settings._universe` remains authoritative for the saved lock.
The job reports `version_id`, `locked_pairs` and its requested subset of pairs.
Cards differing from the server lock stop with `Karty nie sú locknutý universe`.

The live health endpoint was observed in `demo` mode before deployment.
Supabase credentials are unavailable in this workspace. Durable persistence
and survival of a production cold start remain incomplete, not inferred from
unit tests. Production now refuses to save a new lock/settings or start a job
without the configured Supabase store, and the UI states the limitation.

To complete infrastructure, configure the existing Supabase project via Vercel
Production variables `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, apply the
repository's SQL migrations 001–005, redeploy and verify a real saved lock after
refresh/cold start. Keep the service role secret server-side. No credentials,
replacement lock or guessed historical record are manufactured by this change.

## Gates and diagnostic display

20 validation / 10 holdout trades, net positive holdout, return at least equal
to hold over identical dates/capital, and maximum holdout DD 15% remain mandatory.
Validation must also have positive net PnL, DD at most 15%, and 2/3 positive WF
windows. One validation finalist per coin gets holdout, with no replacement.

When all variants fail validation, diagnostic display prioritizes fewer
non-sample failures, then smaller trade-count deficit, then higher validation
PnL/lower DD. It does not rank low-sample payoff or create a finalist/candidate.
The saved 17-trade positive UNI variant is therefore displayed before the
5-trade XRP variant or the losing 21/23-trade rows. Presentation on read does
not rewrite original results or evaluate any holdout.

## New cost model

For future optimizer jobs: `fee_schedule: okx_global_regular`, configured
`fee_taker: 0.001`, `fee_maker: 0.0008`, default `role: taker`.
These are the requested research assumptions, not verification of the user's
account fees. OKX explains that actual fees depend on the account and pair:
https://www.okx.com/help/trading-fee-rules-faq

Each pair saves the timestamped OKX book, via derived VWAP, consumed level counts,
notional and cost components: half-spread, buy impact and sell impact. The model
consumes actual quantities across up to 400 bid/ask levels. It does not divide
stake by aggregate top-five liquidity. `thin_L1` flags top-level notional below
the proposed stake; it is advisory, not a strategy entry filter.

Entry/exit cost rates = fee + half-spread + corresponding impact. The net
capital return is `price_ratio * (1 - exit_rate) / (1 + entry_rate) - 1`.
Hold benchmark uses the same formula. Profile estimates calibrate the full
configured stake, including when available capital is lower; this is a
conservative approximation. Maker is recorded as a schedule rate, while new
jobs use taker execution only.

The current book is explicitly an estimate, not historical L2 data. The original
UNI/ZEC replay retains 0.15% per side and cannot use the new cost model.
`GET /api/market/cost-model` inspects books without candles, jobs or writes.
Missing/stale/crossed/shallow books do not silently use zero costs. A affected
pair is excluded from scoring; other fully sourced pairs can continue.

## Universe and Freqtrade

Universe sources remain OKX only. Existing 3L/3S/5L/5S exclusion and 0.08%
maximum spread reject the supplied 0.11% BONK fixture. This is not a claim about
today's BONK spread. Any failed detailed candidate yields `degraded_no_pick`.
Correlation aligns return timestamps over the configured lookback. New proposals
retain the 0.75 hard limit and subtract 10 × absolute correlation points before
each subsequent pick. Existing locked pairs are never rewritten.

Freqtrade is visibly blocked without KANDIDÁT. Export API rechecks a stored
candidate from the active version. Export config uses OKX spot and `fee: 0.001`,
which excludes spread/impact. It contains no exchange credentials, defaults to
stopped and cannot enable live trading. Historical validation/replay is not
equivalent to a Freqtrade backtest. This change runs neither `freqtrade trade`
nor download/backtest/dry-run commands.

Reference: https://www.freqtrade.io/en/stable/backtesting/

## Verification boundary

Synthetic tests cover trade tape, close-only SL, precedence, replay isolation,
gates, diagnostic display, costs, universe failures and disabled export. They
do not prove a historical replay or durable production storage. The deployment
must separately verify health, strategy context after refresh, replay preflight,
public cost inspection and disabled Freqtrade UI, without launching a new job.

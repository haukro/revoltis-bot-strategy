# Validation trade tape and bounded replay

Historical replay preserves original execution, costs and selection policy.
Logging observes the existing close-price simulator without changing SL/trailing
thresholds, entry rules, the parameter grid, universe lock or 20/10 gates.
The separate new cost model applies to future jobs only; see research checks.

Every variant now saves `trades[]` for WF1–WF3. Training trades are discarded.
Holdout trades are appended only where the existing eligible-finalist branch
already executes holdout. Fields: pair, bar, variant_id, window, entry_ts, exit_ts,
duration_min, entry_px, exit_px, pnl_net, stake_amount, cost_per_side, mae, mfe,
exit_reason, sl_before_trail, trailing_start_percent,
mfe_reached_trailing_start, trail_active_before_exit_candle.

- `pnl_net` is after the existing per-side cost model (5m: 0.15% each side).
- MAE is signed (zero or negative), MFE is zero or positive, both gross price
  percentages from entry. They include only candles after the entry candle,
  through the exit candle, because execution is at close. A zero-duration trade
  closed at a window boundary has MAE/MFE zero.
- `stop_loss`, `trailing`, `window_end`, `other` map actual engine exit reasons.
- A low wick alone does NOT trigger this engine's stop. `sl_before_trail` is true
  only when the stop AND trailing exit conditions are true at the same close and
  SL wins priority. It does not infer the intrabar order of high and low.
- `trail_active_before_exit_candle` distinguishes activation in a previous candle
  from activation first observed in the exit candle. MFE above the threshold is
  not evidence of intrabar execution or a trailing bug.

The result stores one gzip/base64 validation-only candle snapshot per pair/bar,
SHA-256, engine version, exact WF index/time boundaries and a settings checksum.
Holdout candles are excluded. Existing JSON optimizer storage persists these
fields without a schema migration. Storage durability still depends on the
configured store (Supabase or the existing temporary LocalStore fallback).

`GET /api/optimizer/validation-replay` performs read-only preflight against
archived records. `POST` accepts only `source_job_ids`; no dates, parameters or
coin overrides. It supports only the explicitly requested historical rows:

If the original report or required evidence is missing, preflight returns
`replay_unavailable`. The UI shows this even when no optimizer report loads.
Loading the page only reads preflight; it never starts replay.

| Pair/bar/variant | Validation trades | Net PnL | WF counts | WF PnL |
| --- | ---: | ---: | --- | --- |
| UNI/USDT 5m v2 | 21 | -0.6733 | 5, 7, 9 | 1.3003, -3.2424, 1.2688 |
| ZEC/USDT 5m v2 | 23 | -0.6865 | 9, 7, 7 | 1.2986, -1.7375, -0.2476 |

Both source records and snapshots must pass preflight before any simulation.
Missing/ambiguous originals, different versions, altered settings, costs, hashes
or boundaries block replay. There is no current-OKX download fallback and no
holdout or training replay. Only six WF simulations can execute. Every stored
window metric and aggregate metric must match (tolerance below 1e-7). A mismatch
discards both tapes. Replay is always diagnostic, `qualified: false`, no strategy
code, verdict NEPREŠIEL. No source parameter or qualification field is rewritten.

An attempt is cached in process; its outcome is also appended under
`result.validation_replay` in the source record for later idempotent reads.
This is not a distributed exactly-once guarantee during concurrent requests or
storage failures. No automatic retry occurs. The UI has no replay/start button
in the diagnostic tape panel. Legacy rows explicitly report missing evidence;
deployment does not manufacture their tapes or launch any job.

Tests use synthetic fixtures only. Their 44-row replay is a test of isolation,
reconciliation and rendering, not a reconstruction of the user's historical run.

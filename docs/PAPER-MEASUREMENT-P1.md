# Paper measurement P1 — offline implementation and acceptance evidence

Roman approved B1–B8 rev3 as FINAL and authorized proceeding with P1 on
2026-10-28. The reviewed specification is preserved in
`PAPER-MEASUREMENT-B1-B8-REV3.md`; only its draft status sentence was changed.

Base: `b200950cffa6c349a6044c4ff70d28f9d7b9e8dd` (`main`).
Branch: `codex/paper-measurement-p1`. PR for review only, without merge.

## Implementation

`backend/app/paper_measurement.py` is an independent offline module:

- `candle_window` and `closed_only` calculate the 5m boundary from open times,
  align warmup, require confirmed complete coverage, preserve raw close times,
  and reject gaps/duplicates/future confirmed rows. They transform supplied
  source rows; the HTTP downloader belongs to P3.
- `canonical_json` / `hash_json` define exact, sorted UTF-8 JSON with decimal
  numbers, no exponent notation, no rounding, normalized signed zero, and no
  NaN/Infinity. Tape rows are ordered by ordinal before hashing. Archived
  candles are normalized to open/close timestamps, OHLCV, quote volume and
  confirmation; storage-only metadata such as fetched_at is not hashed.
- `engine_sha256` hashes a canonical manifest containing the SHA-256 of the
  source bytes of `simulation.py` and `costs.py`.
- `simulation_tape` maps closed engine trades to run-owned rows. Missing tape
  is `None`; an explicitly present, count-verified empty tape is `[]`.
- `evaluate_test` uses the first certified persisted candidate, the candidate's
  own complete tape, inclusive [S, T*] net PnL, and full-run summary DD rounded
  to two decimals. The 30-day/30-trade gates, PF ≥ 1.10, positive expectancy,
  DD ≤ 15%, zero-loss PF semantics and diagnostic Scale behavior are fixed by
  the approved protocol. Reasons remain separate from economic FAIL.
- `replay_run(run_id, read_snapshot=...)` reads an injected immutable snapshot
  and uses its settings, candles and saved cost profile with the existing
  `simulate()` and `force_close_at_end=False`. It compares the complete closed
  tape hash, summary and open-position count. It rejects an engine mismatch.

The evaluator accepts `RunEvidence.origin` only from a trusted future adapter:
`verified`, `pending`, or `unverifiable`. P1 does not certify cron records.
`chronology_verified` defaults to false and requires the boolean `True` for
post-gate runs; truthy strings/numbers are not certificates. The presence of a
sequence number alone cannot certify first-run priority. Pre-gate runs are
excluded before checking sequence metadata. Missing evidence yields INVALID with
`reason=unverifiable_candidate`, the specific `invalid_reason`, and the same
specific reason in the eligibility trace. A pending origin returns
NOT_EVALUATED / origin_pending before classifying or skipping that run.

Code identity fields require a lowercase 40-character commit SHA and a
64-character engine SHA-256. This validates their representation; the future
trusted adapter must bind them to the actual deployed source. Open-position
rows require run ownership, ordinal, pair, opened timestamp, positive entry
rate/stake, and a raw object, with no closed timestamp. They remain open and
are not included in closed-trade metrics.

Metric sums and threshold comparisons use an isolated decimal context sized
to the input values. PF/expectancy do not depend on the caller's decimal
precision. Numeric outputs must remain finite and must not turn a nonzero
decimal into floating-point zero; otherwise the candidate is INVALID with
`unrepresentable_metrics`. DD uses explicit half-even rounding to two decimals.

No existing runtime path imports the new module. `main.py`, `simulation.py`,
`costs.py`, `optimizer.py`, ledger, strategies and migrations retain their base
contents. There is no RPC, scheduler, endpoint registration or DB client in P1.
Only this feature branch is disabled in `vercel.json` under
`git.deploymentEnabled` so publishing the PR does not request a preview deploy.

## Acceptance mapping

All listed tests are in `backend/tests/test_paper_measurement.py`.

| Spec test | Coverage |
| --- | --- |
| AT4 | Unconfirmed final candle excluded; open trade is not force-closed. |
| AT5 | Replay matches tape and complete summary; altered summary / wrong engine rejected. |
| AT12 | Earlier unverifiable persisted run prevents selection of a later complete run. |
| AT13 | Invalid pre-gate run produces no test verdict or eligibility entry. |
| AT14 | PnL after T* excluded; full-run DD still determines verdict. |
| AT18 | Legacy historical endpoint and optimizer match saved b200950 synthetic outputs. |
| AT19 | Off-grid S: test_end opens the time gate while B remains before it. |
| AT20 | Missing own tape remains first candidate; cannot become zero trades. |
| AT30 | Book age 0, 60000, 60001, -1 and missing timestamps. |
| AT31 | Positive zero-loss PF, all-zero PnL, PF 1.09/1.10 and losses. |
| AT32 | Aligned and off-grid S with varied warmup. |
| AT33 | Thirty trades before 30 days do not produce a verdict. |
| AT34(a) | .999 and exact-boundary raw close conventions share the open-derived B. |
| AT40, evaluator | Absent ticks/claims are not runs; persisted invalid run is a candidate. |
| AT42 | Infinite PF cannot override DD; complete empty tape is ineligible. |

Additional cases cover tied c30 close times, canonical hashes, corrupt evidence,
missing trade fields, unassociated rows, forced/warmup/null-PnL exclusions,
future trades, pending origin traversal, and the pure Scale classification.
These unit cases do not claim completion of later DB/cron acceptance tests.

## P1 implementation review — 2026-10-28

Review of the original P1 head `e5182cda` reproduced the following failures.
All fixes remain in the offline module and preserve the FINAL specification.

| Reproduced issue | Fix and regression coverage |
| --- | --- |
| `chronology_verified="false"` and placeholder code hashes could permit PASS. | Strict boolean certificate and full hash syntax; 8 cases. |
| A pre-gate invalid run without `seq` could invalidate the test. | Apply the time gate before sequence checks; 2 cases, including a later valid candidate. |
| An open-position row without entry/stake/raw evidence could permit PASS. | Validate essential fields; 3 missing-field cases and 1 complete open-position case. |
| Null tape/position/candle rows or a list cost profile could raise instead of returning INVALID. | Validate evidence shapes; 4 cases. |
| A missing replay snapshot raised an uncaught lookup error. | Return INVALID / missing_run_evidence; 1 case. |
| PF/aggregate overflow could produce Infinity, tiny values could become zero, ambient decimal precision could change PF 1.09 to 1.10, and large DD could fail quantization. | Isolate decimal arithmetic and validate float outputs; 5 cases. |

These 24 regression cases extend the original 56 P1 cases. They do not
renumber AT1–AT46 or claim completion of any later phase.

## Validation and reproducibility

Run from `backend` after installing the pinned `requirements.txt` and pytest:

```sh
python -m pytest -q
```

Local validation uses Python 3.12 and synthetic data, mocked HTTP/storage for
legacy endpoint tests, cleared production configuration, and an external
socket guard. The pre-change backend suite passed 151 cases. The completed
suite contains those 151 cases plus 80 P1 cases. CI runs the existing workflow
on the PR. This record does not certify P2–P7 or an economic strategy result.

Local result after review on 2026-10-28: **231 passed, 0 failed, 0 skipped**
(19.42 s). The original P1 validation passed 207 cases before these additions.
One pre-existing Starlette/AnyIO deprecation warning remains. `git diff --check`
passed. The only changed pre-existing file is the branch-specific Vercel guard.

`tests/fixtures/paper_p1_legacy_b200950.json` contains baseline outputs for the
fixed synthetic inputs in `legacy_outputs()`. Before capturing them, downloaded
source bytes were checked against the Git blob hashes at b200950. Volatile run
UUID/timestamps and compressed snapshot bytes are excluded; source snapshot
hashes/counts and all other outputs remain. Do not regenerate this fixture to
accept a changed legacy result.

## Boundaries for the next phases

P2 owns persistence, sealed run tapes/open-position rows, sequence certification,
origin evidence resolution (including the immutable grace clock), assessments
and authoritative verdict transactions. P3 owns network adapters, claim
consumption and worker input/output collection. Real archived runs and cron behavior are
verified in P4. The proposed origin grace of 600000 ms is unchanged and is not
validated by P1.

No DB access, applied migration, deploy, merge, new forward/optimizer job, or
production strategic test is authorized by this PR. HYPE v8 remains INVALID;
rev13 and Strategy D DRAFT R2 remain unchanged.

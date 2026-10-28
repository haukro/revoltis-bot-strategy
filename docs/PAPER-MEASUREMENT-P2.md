# Paper measurement P2 — persistence preparation

Roman authorized preparation of P2 code and tests on 2026-10-28 after the
read-only preflight. This is a stacked draft over PR #71, head `212353d4`.
No connected Supabase database is changed. This does not complete the
specification's acceptance gate on an actual Supabase branch.

## Read-only preflight

Project `revoltis-bot-strategy` (`vdglyxhlmukxeqynguke`): migration history has
20 records, including `20261005175015_paper_ledger_028_029_ec15a24`. Main only
contains numbered migration files through 027. Do not reuse 028/029 or infer
schema equivalence solely from migration names. The new timestamped migration
was generated with Supabase CLI 2.122.0 `migration new paper_measurement_p2`.

`pg_net` 0.20.4 is installed. `pg_cron` 1.6.4 is available but not installed.
There are 31 catalog roles; neither `paper_cron_owner` nor `paper_rpc_owner`
exists. The preflight only read migration history, available extensions and
selected columns of `pg_roles`; it did not inspect strategic records or secrets.

## Implementation boundary

The migration adds the run/test/tick/claim/tape/candle/assessment/verdict tables,
RLS, explicit function ACLs, append-only triggers, deferred tape counts and
transaction-bound sealing. Protected run timestamps and identity come from the
claim, never worker payload. Test state changes are guarded and immutable test
settings include `origin_grace_ms`.

RPCs create an explicitly requested test, claim a run, archive candles, persist
a complete run transaction, read an immutable snapshot, assess it, record an
audit and stop a shadow test. No function is called by migration installation.
No cron job, extension installation, HTTP dispatch, endpoint, or production
configuration is added. The tick function only inserts a restricted origin row;
P4 owns cron scheduling, pg_net dispatch and real session identity verification.

P1 remains the evidence validator and economic evaluator. `prepare_assessment`
accepts the exact `paper_read_run` snapshot and returns a **provisional server
attestation**, including its canonical evidence hash and evaluator source hash.
Only a trusted server with `service_role` may submit it. It must never be shown
as an authoritative test verdict before `paper_assess_run` commits.

The RPC binds the attestation to stored evidence, locks the test, enforces
sequence, checks actual closed-trade gate counts and resolves cron origin itself.
It does not trust a caller-supplied chronology or origin flag. P1 performs full
hash/content validation and metric calculation; SQL does not duplicate that
algorithm. As with the existing server trust boundary, a compromised server
credential is not a supported evaluator. Binding code/evaluator commit fields
to the deployed server build belongs to P3.

Origin resolution requires matching job name, database, username, backend PID
and the tick time inside the successful cron run interval. Pending evidence
does not write an assessment or extend the immutable grace clock. Failure is
immediate; a committed INVALID cannot be replaced by late evidence. New claims
remain blocked until prior post-gate runs are assessed. Every RPC takes the
test lock before observing the clock or changing test state.

The migration installs owner roles as NOLOGIN and grants no membership to the
HTTP worker. Real cron role activation and narrowly scoped read permissions on
`cron.job` / `cron.job_run_details` must be configured and proven in P4. No
600000-ms default grace or 240000-ms lease is silently approved: both must be
provided explicitly when a test is created.

## Validation

Integration tests require real PostgreSQL and synthetic evidence only. The CI
job starts an isolated PostgreSQL 17 service; each case uses its own disposable
database. The harness refuses non-loopback hosts or a database name without
the `paper_p2_test` prefix. Ordinary backend tests skip this integration module
when no local test DSN is provided. Production dependencies remain unchanged.

The local workspace cannot install/run a normal PostgreSQL service because of
its restricted system accounts/filesystem. Integration verification is therefore
performed in the isolated CI service, not in the connected Supabase project.
The test-only clock and synthetic cron tables are installed by the harness;
neither is part of the migration. Actual cron/session behavior remains P4.

Acceptance mapping: AT1/7 run identity and independent costs; AT2 all-table
immutability; AT6 archive conflicts; AT10/23 duplicate boundary and active lease;
AT21/22/39 expiry and retry; AT25 invalid/reused ticks; AT26 sealing and rollback;
AT28 idempotence and audit; AT34(b) single claim clock; AT35 stored horizon;
AT36 real lock wait crossing expiry; AT37/44 finalization race and assessment
order; AT38(a,c,d,e) ACL/origin handling with synthetic logs; AT41/45 shadow
boundary; AT43 claim gating and concurrent assess; AT46 grace/failure/late proof.

Validation results will be recorded after CI completes. This draft does not
claim successful Supabase-branch validation or an economic strategy result.

## Review and later authorization

The branch-specific Vercel deploy guard covers P2 as well as P1, before the P2
branch is published. Both rules require an explicit disposition before merge.
Approved FINAL rev3, the P1 engine/evaluator, legacy paths, migrations 001–029,
HYPE v8, rev13 and Strategy D DRAFT R2 remain unchanged.

Applying this migration to a Supabase branch, enabling extensions/cron, any
merge/deploy, and a new strategic test remain separate authorization steps.

# Paper measurement P2 — persistence preparation

Roman authorized preparation of P2 code and tests on 2026-10-28 after the
read-only preflight. This is a stacked draft over PR #71, head `212353d4`.
No connected Supabase database is changed. This does not complete the
specification's acceptance gate on an actual Supabase branch.

On 2026-10-29 Roman selected continued CI validation after Supabase rejected
branch creation because it requires Pro or higher. No test branch, connected
database SQL, migration, or strategic run was executed. No paid-plan upgrade
is authorized; actual Supabase branch acceptance remains deferred.

Roman subsequently authorized **additional local Supabase integration in CI**,
as a separate commit on this draft. This is supplementary evidence, not a
replacement acceptance gate: P2 managed-branch acceptance and P3/P4 remain open.

The subsequent review is captured in the separate
[proposed rev3 addendum A1](PAPER-MEASUREMENT-B1-B8-REV3-ADDENDUM-CRON-READ.md).
It awaits Roman's approval; FINAL rev3 itself is unchanged. CI now pins every
job to `ubuntu-24.04`; the Supabase CLI was already pinned to 2.122.0.

## Additional local Supabase CI

The `paper-p2-supabase` job pins Supabase CLI 2.122.0 and starts an unlinked,
temporary project with the actual Supabase Postgres 17 image, Auth, PostgREST
and API gateway. It never loads repository CLI state, hosted credentials or a
project ref. API/DB URLs must be loopback addresses on the dedicated local ports;
the Docker database must have the expected project label before test SQL runs.
The stack and volumes are removed even after a failed test.

The CLI applies the **exact P2 migration bytes**, preceded by two explicitly
synthetic `001`/`029` ordering probes. This verifies mixed numeric/timestamp
filenames, installation identity and local migration history. It does not replay
legacy migrations 001–029 or reconcile production migration history. The known
remote `20261005175015_paper_ledger_028_029_ec15a24` record has no corresponding
file on this branch; a production application plan must resolve that mismatch
before any eventual `db push`. No repair or renaming is performed here.

The integration cases check real Supabase roles and RLS, Auth-issued tokens,
`authenticator` sessions switching to anon/authenticated/service_role, denial
of table access and tick manufacture, and server RPC creation/shadow stop.
One actual minutely cron job runs as `paper_cron_owner`, records its session/PID,
and sends a `pg_net` POST to an echo container on the same Docker network (no
published HTTP port). The test links the real succeeded log interval to the
tick and verifies the HTTP response. It then exercises claim, archive, persist,
read and idempotent assess through PostgREST with synthetic candles and no trades.
There is no strategy run or economic verdict.

Cron extension activation, LOGIN/CONNECT, metadata grants and narrow metadata
RLS policies are **CI-only P4 setup candidates**, outside the P2 migration.
The test first demonstrates origin_pending without the metadata RLS policies,
then proven_ineligible with verified origin after those policies are installed.
The policies now restrict both job name and owner/database, bind log rows by
jobid, and include restrictive counterparts so pg_cron's existing permissive
own-job policy cannot widen access. Actual decoy cron runs verify that other
jobs of the same owner and the same name under another owner are hidden; an
inactive reader-owned job is hidden too. Separate cases deny access to four
other application roles and verify that the reader has no cron-table write
privileges. This is CI-only policy DDL performed by `supabase_admin`; it does
not prove the managed platform permits the same DDL or preserves it on upgrade.
No fake clock, hand-inserted cron catalog row, or application-function replacement
is used. Extension versions, role flags and the cron connection mode are reported
by the test. Proposed production grace/lease values remain unapproved; the fixture
uses explicit 120000-ms values only for this short integration check.

Passing this job would not establish managed-platform compatibility, deployed
code identity, Vercel preview protection bypass, 24-hour scheduling continuity,
or suitability of the proposed production timing settings. Those remain future
phase acceptance requirements. Startup/service and test results must be read from
the exact commit's CI run before claiming this new integration has passed.

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
a complete run transaction, persist a separate manual diagnostic, read an
immutable snapshot, assess it, record an audit and stop a shadow test. Diagnostic
metadata mirrors automatic run columns without claim/tick/sequence; open
positions are sealed in the diagnostic row and its trade rows are run-owned.
No function is called by migration installation.
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
remain blocked until prior post-gate runs are assessed. Claim, persist, assess
and stop take the test lock before observing their decision clock or changing
test state. Claim uses exactly one clock sample for all persisted timing fields.

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

Local offline result: **240 passed**, with one module-level skip for the
PostgreSQL integration suite when no test DSN is set (22.37 s). This includes
all 231 P1/base cases and 9 new snapshot-attestation cases. The existing
Starlette/AnyIO deprecation warning remains.

The isolated CI job runs **58 PostgreSQL cases plus those 9 adapter cases**.
Before that suite, a separate installation case runs the exact migration with
both session and current role set to a non-superuser installer (CREATEROLE,
CREATEDB, BYPASSRLS). It checks ownership transfer, removal of temporary runtime
membership, RLS/worker permissions, a server RPC smoke test and preservation of
a synthetic legacy table's ACL and the installer's default privileges. A fresh
CI database and roles are removed before the transaction suite starts. This
models a privilege boundary, not all managed Supabase platform behavior.
Its current-head result is recorded in [PR #72](https://github.com/haukro/revoltis-bot-strategy/pull/72).
The extra storage case covers diagnostic/automatic isolation (the AT24 HTTP
endpoint remains P3); four negative origin cases reject a wrong PID, role,
job name or successful interval. Passing these tests does not certify an
actual Supabase branch, cron scheduler, or an economic strategy result.

## Review corrections

Review started from `a51eacfe80d2a7b0c3b80df261db1c710327490e` against FINAL
rev3. Regression-only commit `f0732cb2e444b560d1e16af0e0ece1917dd3713f`
preserves the initial seven reproductions before the SQL fixes. Two additional
PID-reuse cases cover an old failed execution with no terminal timestamp.
The current-head CI result and links are recorded in PR #72.

| Finding | Trigger and correction | Regression coverage |
| --- | --- | --- |
| P2-RV1 — missing required configuration | SQL CHECK previously accepted NULL for a shadow deadline or warmup. Explicit non-NULL checks now reject both at test creation. | Two creation failures with no test row. |
| P2-RV2 — reused rejected tick | A tick with a `lease_held` outcome could later acquire a claim while retaining its earlier outcome. Claim now rejects ticks already present in either claims or outcomes. | Retry after lease expiry is rejected; a fresh tick succeeds. |
| P2-RV3 — stale cron failure | Aggregating historical rows by PID allowed an old failed execution to invalidate a newer successful/pending tick. Origin now selects the most recent matching execution and excludes completed intervals before the tick. | Successful and pending current runs, each with an older failed row with/without end time; existing immediate-failure/grace cases remain. |
| P2-RV4 — changing evidence digest on completion | Snapshot hashing included mutable test status, completion time and verdict ID. Those operational fields are excluded from measurement evidence. | The same snapshot/digest survives completion and can be used for an audit. |
| P2-RV5 — later archive fills changed old evidence | Snapshot candle selection used a live date range. Persist now captures `candle_open_times_ms` from visible archive rows; snapshot reads only those immutable members. Worker-supplied membership is overwritten. | Adding a missing shared candle later leaves the original snapshot unchanged and its assessment INVALID. |

The membership array stores archive keys, not copies of OHLCV data. It is sealed
with automatic and diagnostic runs. The P1 evaluator and FINAL specification
are unchanged. These corrections address persistence behavior within P2; they
do not approve settings, cron activation, deployment or a strategic test.

## Review and later authorization

The branch-specific Vercel deploy guard covers P2 as well as P1, before the P2
branch is published. Both rules require an explicit disposition before merge.
Approved FINAL rev3, the P1 engine/evaluator, legacy paths, migrations 001–029,
HYPE v8, rev13 and Strategy D DRAFT R2 remain unchanged.

Branch application and synthetic tests were authorized, but Roman subsequently
selected CI because branching requires a paid plan. Outside the authorized
disposable CI fixtures, enabling extensions/cron,
any merge/deploy, a paid-plan change and a new strategic test remain separate
authorization steps. Passing CI does not close the actual-branch P2 gate.

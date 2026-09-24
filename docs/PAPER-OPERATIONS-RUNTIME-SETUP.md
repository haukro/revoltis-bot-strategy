# Paper Operations Runtime Setup

Status: **CONFIGURATION REQUIRED BEFORE SCHEDULED WORKERS**

This document covers deployment configuration only. It does not change alpha logic, TEST-SPEC-002, TEST-SPEC-003, or live-trading status.

## Current production state

- SPEC-005 backend is deployed.
- Reconciliation, recovery, outbox worker, snapshot collector, blind-safe API and UI are present.
- Active ops policy in Supabase:
  - name: `SPEC005_OPS_INITIAL_V1`
  - `max_worker_batch = 10`
  - `market_book_depth = 20`
  - automatic warning/halt thresholds: disabled (NULL)
- Manual reconciliation after policy activation:
  - PASSED
  - 0 CRITICAL
  - 0 WARNING
  - audit integrity PASS
- No paper strategy runtime binding is enabled.
- `live_trading = false`.

## Required Vercel Production environment variables

Set these only in the Vercel project server environment.

### OPS_ADMIN_TOKEN

Purpose:

- authorizes kill-switch mutation endpoints
- must never be exposed to the browser/frontend
- use a long cryptographically random value

Routes protected:

- `POST /api/paper/kill-switch/activate`
- `POST /api/paper/kill-switch/request-recovery`
- `POST /api/paper/kill-switch/enable`

Without this variable the endpoints fail closed with:

`ops_admin_token_not_configured`

### PAPER_SCHEDULER_TOKEN

Purpose:

- authorizes internal scheduled worker routes
- must be different from OPS_ADMIN_TOKEN
- must never be exposed to the browser/frontend

Routes protected include:

- `POST /api/internal/paper/outbox-tick`
- `POST /api/internal/paper/reconcile`
- `POST /api/internal/paper/recover`

Without this variable the worker routes fail closed with:

`paper_scheduler_token_not_configured`

### PAPER_OUTBOX_LEASE_SECONDS

Purpose:

- deployment-time lease duration for a claimed outbox item
- not an alpha parameter
- current example/default deployment choice: `30`

Correctness does not depend on this exact number because lease generation, idempotency and DB fencing remain authoritative.

## Activation sequence after env configuration

Do not enable a strategy runtime binding yet.

First:

1. add the three Production env vars in Vercel
2. redeploy Production
3. confirm unauthorized internal route returns 401, not 503
4. invoke one authorized reconciliation worker
5. confirm worker heartbeat appears
6. invoke one authorized recovery worker
7. confirm no unexpected recovery action
8. confirm blind-safe UI/API contains no strategy trade/performance data
9. only then design/lock how TEST-SPEC-002 signals are fed into paper_signals
10. create a paper runtime binding only after that signal-ingest path is reviewed

## Important boundary

The official TEST-SPEC-002 forward verdict remains one batch replay after the locked fold ends.

Paper-execution infrastructure may later observe operational execution quality, but:

- it must not change TEST-SPEC-002 alpha logic
- it must not expose interim Strategy B PnL/PF/expectancy
- it must not become the official PASS/FAIL calculation
- no runtime binding is enabled by this setup document

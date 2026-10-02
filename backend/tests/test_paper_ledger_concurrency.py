"""Real PostgreSQL concurrency regressions for the paper trade ledger.

Each test applies migrations 028 + 029 to a throwaway database and drives the
ledger through independent connections, the way parallel browser refreshes hit
Supabase. Set PAPER_LEDGER_TEST_DSN to a server where the user may CREATE
DATABASE (CI starts a postgres service). In CI a missing DSN is a failure, not
a skip, so the guard can never silently stop being tested.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import uuid
from pathlib import Path

import pytest

DSN = os.getenv("PAPER_LEDGER_TEST_DSN")
if not DSN:
    if os.getenv("CI"):
        raise RuntimeError("PAPER_LEDGER_TEST_DSN is required in CI for the ledger concurrency guard")
    pytest.skip("PAPER_LEDGER_TEST_DSN not set; start PostgreSQL to run ledger concurrency tests",
                allow_module_level=True)

import psycopg  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402

from app.paper_ledger import append_closed_trades, ledger_rows  # noqa: E402
from test_paper_ledger import trade  # noqa: E402

MIGRATIONS = Path(__file__).parents[2] / "supabase" / "migrations"


@pytest.fixture
def db():
    name = f"ledger_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        admin.execute(f'create database "{name}"')
    dsn = psycopg.conninfo.make_conninfo(DSN, dbname=name)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn:
            for migration in ("028_paper_trade_ledger.sql", "029_paper_trade_ledger_no_overlap.sql"):
                conn.execute((MIGRATIONS / migration).read_text())
        yield dsn
    finally:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f'drop database if exists "{name}" with (force)')


def row(label="official", pair="HYPE/USDT", opened="2026-09-26T20:49:59.999+00:00",
        closed="2026-09-26T22:39:59.999+00:00", profit=0.257, run_id="run"):
    item = trade(profit)
    item.update(pair=pair, opened_at=opened, closed_at=closed)
    return ledger_rows(label, [item], run_id)[0]


OVERLAPPING = dict(opened="2026-09-26T21:04:59.999+00:00", closed="2026-09-26T23:14:59.999+00:00", profit=-0.4)


def append(conn, rows):
    return {r[0]: r[1] for r in conn.execute(
        "select ledger_id, outcome from public.append_paper_trade_ledger(%s)", [Jsonb(rows)]).fetchall()}


def stored(dsn, label="official"):
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "select id, profit_usdt::float8, run_id from public.paper_trade_ledger "
            "where paper_label = %s order by opened_at", [label]).fetchall()


def wait_until_blocked_on_lock(dsn, pid):
    deadline = time.time() + 5
    with psycopg.connect(dsn, autocommit=True) as conn:
        while True:
            found = conn.execute("select wait_event_type from pg_stat_activity where pid = %s", [pid]).fetchone()
            if found and found[0] == "Lock":
                return
            assert time.time() < deadline, "second connection was not serialised by the constraint"
            time.sleep(.02)


def test_second_connection_waits_for_first_and_cannot_freeze_an_overlap(db):
    first, second = row(run_id="tab-a"), row(run_id="tab-b", **OVERLAPPING)
    with psycopg.connect(db) as a, psycopg.connect(db, autocommit=True) as b:
        assert append(a, [first]) == {first["id"]: "inserted"}  # transaction still open
        result = {}
        worker = threading.Thread(target=lambda: result.update(append(b, [second])))
        worker.start()
        wait_until_blocked_on_lock(db, b.info.backend_pid)
        assert worker.is_alive()
        a.commit()
        worker.join(5)
    assert result == {second["id"]: "overlap"}
    assert stored(db) == [(first["id"], 0.257, "tab-a")]


def test_rolled_back_first_writer_lets_the_waiting_entry_in(db):
    first, second = row(run_id="tab-a"), row(run_id="tab-b", **OVERLAPPING)
    with psycopg.connect(db) as a, psycopg.connect(db, autocommit=True) as b:
        append(a, [first])
        result = {}
        worker = threading.Thread(target=lambda: result.update(append(b, [second])))
        worker.start()
        wait_until_blocked_on_lock(db, b.info.backend_pid)
        a.rollback()
        worker.join(5)
    assert result == {second["id"]: "inserted"}
    assert [r[0] for r in stored(db)] == [second["id"]]


def test_simultaneous_application_refreshes_freeze_exactly_one_of_two_overlapping_entries(db):
    """The original race: both requests read an empty ledger, then write distinct ids."""
    def read_rows(label):
        with psycopg.connect(db) as conn:
            cur = conn.execute("select id, pair, opened_at, closed_at, profit_usdt::float8 "
                               "from public.paper_trade_ledger where paper_label = %s", [label])
            return [{"id": i, "pair": p, "opened_at": o.isoformat(), "closed_at": c.isoformat(), "profit_usdt": v}
                    for i, p, o, c, v in cur.fetchall()]

    def insert_rows(rows):
        with psycopg.connect(db, autocommit=True) as conn:
            outcomes = append(conn, rows)
        by_id = {r["id"]: r for r in rows}
        return {"inserted": [by_id[i] for i, o in outcomes.items() if o == "inserted"],
                "overlapping": [i for i, o in outcomes.items() if o == "overlap"]}

    async def exercise():
        both_read = asyncio.Barrier(2)

        async def read(label):
            snapshot = await asyncio.to_thread(read_rows, label)
            await both_read.wait()  # neither request sees the other's write
            return snapshot

        async def insert(rows):
            return await asyncio.to_thread(insert_rows, rows)

        shifted = trade(-0.4)
        shifted["opened_at"] = OVERLAPPING["opened"]
        return await asyncio.wait_for(asyncio.gather(
            append_closed_trades("official", [trade(0.257)], "tab-a", read, insert),
            append_closed_trades("official", [shifted], "tab-b", read, insert),
        ), timeout=10)

    results = asyncio.run(exercise())
    assert len(stored(db)) == 1, "Both requests froze overlapping trades"
    assert sorted(r["newly_recorded"] for r in results) == [0, 1]
    assert sorted(r["overlaps_frozen_trade"] for r in results) == [0, 1]


def test_many_parallel_racers_leave_exactly_one_trade_per_overlap_group(db):
    racers, groups = 6, 15
    barrier = threading.Barrier(racers)
    outcomes, lock = [], threading.Lock()

    def racer(index):
        with psycopg.connect(db, autocommit=True) as conn:
            for group in range(groups):
                day = f"2026-08-{group + 1:02d}"
                candidate = row(opened=f"{day}T10:{index:02d}:00+00:00", closed=f"{day}T12:00:00+00:00",
                                profit=index / 10, run_id=f"racer-{index}")
                barrier.wait()
                outcome = append(conn, [candidate])[candidate["id"]]
                with lock:
                    outcomes.append(outcome)

    threads = [threading.Thread(target=racer, args=(i,)) for i in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(outcomes) == racers * groups
    assert outcomes.count("inserted") == groups
    assert outcomes.count("overlap") == (racers - 1) * groups
    assert len(stored(db)) == groups


def test_labels_pairs_and_back_to_back_trades_stay_independent(db):
    rows = [
        row(),
        row(label="scale_300_150"),
        row(pair="SUI/USDT"),
        # Next entry starts exactly at the previous exit: allowed, ranges are [open, close).
        row(opened="2026-09-26T22:39:59.999+00:00", closed="2026-09-27T01:00:00+00:00"),
    ]
    with psycopg.connect(db, autocommit=True) as conn:
        assert set(append(conn, rows).values()) == {"inserted"}
    assert len(stored(db)) == 3 and len(stored(db, "scale_300_150")) == 1


def test_one_batch_keeps_earliest_entry_and_reports_its_own_overlap(db):
    later, earlier = row(**OVERLAPPING), row()
    with psycopg.connect(db, autocommit=True) as conn:
        assert append(conn, [later, earlier]) == {earlier["id"]: "inserted", later["id"]: "overlap"}


def test_duplicate_keeps_first_value_and_frozen_rows_stay_immutable(db):
    original = row(profit=0.257, run_id="run-1")
    recalculated = {**row(profit=0.199, run_id="run-2"), "closed_at": "2026-09-26T22:44:59.999+00:00"}
    with psycopg.connect(db, autocommit=True) as conn:
        append(conn, [original])
        assert append(conn, [recalculated]) == {original["id"]: "duplicate"}
        for statement in ("update public.paper_trade_ledger set profit_usdt = 1",
                          "delete from public.paper_trade_ledger"):
            with pytest.raises(psycopg.errors.RaiseException, match="immutable_paper_trade_ledger_row"):
                conn.execute(statement)
    assert stored(db) == [(original["id"], 0.257, "run-1")]

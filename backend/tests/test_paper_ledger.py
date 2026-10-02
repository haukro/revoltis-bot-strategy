from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).parents[1] / "app" / "paper_ledger.py"
SPEC = importlib.util.spec_from_file_location("app.paper_ledger", MODULE_PATH)
ledger = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ledger)


def trade(profit: float, book_ts: str = "1", status: str = "closed") -> dict:
    return {"id": "simulation-HYPE/USDT-1-0", "pair": "HYPE/USDT", "status": status,
            "opened_at": "2026-09-26T20:49:59.999+00:00", "closed_at": "2026-09-26T22:39:59.999+00:00",
            "entry_rate": 91.224, "exit_rate": 91.882, "stake_amount": 50.0, "profit_usdt": profit,
            "exit_reason": "trailing_profit", "raw": {"cost_components": {"book_ts": book_ts, "half_spread": 0.0005}}}


class PaperLedgerTest(unittest.TestCase):
    def test_unfinished_candle_is_dropped(self):
        candles = [{"close_time": 1_000}, {"close_time": 2_000}, {"close_time": 3_000}]
        self.assertEqual(ledger.closed_candles_only(candles, 2_000), candles[:2])

    def test_candles_after_simulation_end_are_dropped(self):
        candles = [{"close_time": 1_000}, {"close_time": 2_000}, {"close_time": 3_000}]
        self.assertEqual(ledger.closed_candles_only(candles, 10_000, 2_000), candles[:2])

    def test_moved_exit_does_not_create_second_row_for_same_entry(self):
        first = trade(0.1)
        moved = trade(0.2)
        moved["closed_at"] = "2026-09-26T23:14:59.999+00:00"
        self.assertEqual(ledger.ledger_rows("official", [first])[0]["id"],
                         ledger.ledger_rows("official", [moved])[0]["id"])

    def test_drift_is_reported_not_applied(self):
        rows = ledger.ledger_rows("official", [trade(0.2)])
        self.assertEqual(ledger.drifted_ids(rows, {rows[0]["id"]: 0.257}), [rows[0]["id"]])
        self.assertEqual(ledger.drifted_ids(rows, {rows[0]["id"]: 0.2}), [])
        self.assertEqual(ledger.new_rows(rows, {rows[0]["id"]}), [])

    def test_open_trades_are_never_recorded(self):
        self.assertEqual(ledger.ledger_rows("official", [trade(0.1, status="open")]), [])

    def test_forced_end_of_test_exit_is_never_recorded(self):
        forced = trade(0.1)
        forced["exit_reason"] = "end_of_test"
        self.assertEqual(ledger.ledger_rows("official", [forced]), [])

    def test_key_is_stable_when_list_position_changes(self):
        first = ledger.ledger_rows("official", [trade(0.1)])[0]["id"]
        other = trade(0.1)
        other["id"] = "simulation-HYPE/USDT-1-7"
        self.assertEqual(first, ledger.ledger_rows("official", [other])[0]["id"])

    def test_labels_do_not_collide(self):
        self.assertNotEqual(ledger.ledger_rows("official", [trade(0.1)])[0]["id"],
                            ledger.ledger_rows("scale_300_150", [trade(0.1)])[0]["id"])

    def test_refresh_with_new_book_does_not_change_recorded_trade(self):
        stored = {row["id"]: row for row in ledger.ledger_rows("official", [trade(0.257, "100")])}
        # Later refresh recalculates the same trade with another order book.
        refreshed = ledger.ledger_rows("official", [trade(0.199, "200")])
        fresh = ledger.new_rows(refreshed, set(stored))
        self.assertEqual(fresh, [])
        only = next(iter(stored.values()))
        self.assertEqual(only["profit_usdt"], 0.257)
        self.assertEqual(only["cost_book_ts"], "100")

    def test_new_trade_is_added(self):
        second = trade(-0.3)
        second["opened_at"] = "2026-09-27T13:09:59.999+00:00"
        second["closed_at"] = "2026-09-27T21:09:59.999+00:00"
        rows = ledger.ledger_rows("official", [trade(0.1), second])
        existing = {rows[0]["id"]}
        self.assertEqual([row["id"] for row in ledger.new_rows(rows, existing)], [rows[1]["id"]])

    def test_summary(self):
        rows = [{"profit_usdt": 0.5}, {"profit_usdt": -0.25}, {"profit_usdt": 0.25}]
        summary = ledger.ledger_summary(rows)
        self.assertEqual(summary["trades"], 3)
        self.assertEqual(summary["pnl_usdt"], 0.5)
        self.assertEqual(summary["profit_factor"], 3.0)
        self.assertEqual(ledger.ledger_summary([])["win_rate_percent"], None)


class FakeStore:
    """In-memory stand-in for the database with the same insert-once behaviour."""
    def __init__(self, fail_insert: bool = False, racing: list[dict] | None = None):
        self.rows: dict[str, dict] = {}
        self.fail_insert = fail_insert
        self.racing = racing or []

    async def read(self, label):
        return [row for row in self.rows.values() if row["paper_label"] == label]

    async def insert(self, rows):
        if self.fail_insert:
            return []
        for row in self.racing:  # another refresh wins the insert first
            self.rows[row["id"]] = row
        self.racing = []
        inserted = []
        for row in rows:
            if row["id"] not in self.rows:
                self.rows[row["id"]] = row
                inserted.append(row)
        return inserted


class RepeatedRefreshTest(unittest.TestCase):
    def run_refresh(self, store, label, trades, run_id):
        return asyncio.run(ledger.append_closed_trades(label, trades, run_id, store.read, store.insert))

    def test_repeated_refresh_records_each_trade_once_and_keeps_first_pnl(self):
        store = FakeStore()
        first = self.run_refresh(store, "official", [trade(0.257, "100")], "run-1")
        self.assertEqual((first["closed_seen"], first["newly_recorded"]), (1, 1))
        # ten more refreshes, order book changed, exit time moved
        moved = trade(0.199, "200")
        moved["closed_at"] = "2026-09-26T22:44:59.999+00:00"
        for number in range(10):
            result = self.run_refresh(store, "official", [moved], f"run-{number + 2}")
            self.assertEqual(result["newly_recorded"], 0)
            self.assertEqual(result["recalculated_differs_from_recorded"], 1)
        self.assertEqual(len(store.rows), 1)
        only = next(iter(store.rows.values()))
        self.assertEqual((only["profit_usdt"], only["run_id"], only["cost_book_ts"]), (0.257, "run-1", "100"))

    def test_official_and_scale_are_separate(self):
        store = FakeStore()
        self.run_refresh(store, "official", [trade(0.257)], "a")
        self.run_refresh(store, "scale_300_150", [trade(0.77)], "b")
        self.assertEqual(len(store.rows), 2)
        self.assertEqual(len(asyncio.run(store.read("official"))), 1)

    def test_write_failure_is_raised_not_hidden(self):
        with self.assertRaises(ValueError):
            self.run_refresh(FakeStore(fail_insert=True), "official", [trade(0.257)], "a")

    def test_parallel_refresh_that_already_froze_the_row_is_not_an_error(self):
        rows = ledger.ledger_rows("official", [trade(0.257)], "other-tab")
        store = FakeStore(racing=rows)
        result = self.run_refresh(store, "official", [trade(0.257)], "a")
        self.assertEqual((result["newly_recorded"], len(store.rows)), (0, 1))
        self.assertEqual(next(iter(store.rows.values()))["run_id"], "other-tab")

    def test_diverged_entry_overlapping_a_frozen_trade_is_not_double_counted(self):
        store = FakeStore()
        self.run_refresh(store, "official", [trade(0.257)], "run-1")
        shifted = trade(-0.4)
        shifted["opened_at"] = "2026-09-26T21:04:59.999+00:00"  # inside the frozen trade
        result = self.run_refresh(store, "official", [shifted], "run-2")
        self.assertEqual((result["newly_recorded"], result["overlaps_frozen_trade"], len(store.rows)), (0, 1, 1))

    def test_open_position_is_not_recorded_on_refresh(self):
        store = FakeStore()
        result = self.run_refresh(store, "official", [trade(0.1, status="open")], "a")
        self.assertEqual((result["closed_seen"], result["newly_recorded"], len(store.rows)), (0, 0, 0))

    def test_overlaps_inside_one_batch_keep_only_the_earliest_entry(self):
        shifted = trade(-0.4)
        shifted["opened_at"] = "2026-09-26T21:04:59.999+00:00"
        for batch in ([trade(0.257), shifted], [shifted, trade(0.257)]):
            store = FakeStore()
            result = self.run_refresh(store, "official", batch, "batch")
            self.assertEqual((result["newly_recorded"], result["overlaps_frozen_trade"]), (1, 1))
            self.assertEqual(next(iter(store.rows.values()))["profit_usdt"], 0.257)

    def test_adjacent_trades_and_other_pairs_do_not_conflict(self):
        store = FakeStore()
        self.run_refresh(store, "official", [trade(0.257)], "first")
        adjacent = trade(0.3)
        adjacent["opened_at"] = trade(0)["closed_at"]
        adjacent["closed_at"] = "2026-09-26T23:04:59.999+00:00"
        other = trade(0.4)
        other["pair"] = "ETH/USDT"
        result = self.run_refresh(store, "official", [adjacent, other], "next")
        self.assertEqual((result["newly_recorded"], result["overlaps_frozen_trade"]), (2, 0))

    def test_parallel_refresh_reports_pnl_drift_against_the_winning_insert(self):
        winner = ledger.ledger_rows("official", [trade(0.257)], "other-tab")
        store = FakeStore(racing=winner)
        result = self.run_refresh(store, "official", [trade(0.1)], "this-tab")
        self.assertEqual(result["newly_recorded"], 0)
        self.assertEqual(result["recalculated_differs_from_recorded"], 1)
        self.assertEqual(next(iter(store.rows.values()))["profit_usdt"], 0.257)


if __name__ == "__main__":
    unittest.main()

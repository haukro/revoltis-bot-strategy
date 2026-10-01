from __future__ import annotations

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

    def test_open_trades_are_never_recorded(self):
        self.assertEqual(ledger.ledger_rows("official", [trade(0.1, status="open")]), [])

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


if __name__ == "__main__":
    unittest.main()

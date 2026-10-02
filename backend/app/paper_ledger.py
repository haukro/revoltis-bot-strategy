"""Append-only paper trade ledger helpers.

The simulator recomputes the whole paper history on every refresh and the old
`simulated_trades` rows are overwritten. This module freezes each closed paper
trade the first time it is seen. A frozen row is never updated, so a later
refresh with a different order book cannot change an already recorded result.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def closed_candles_only(candles: list[dict[str, Any]], now_ms: int, end_ms: int | None = None) -> list[dict[str, Any]]:
    """Drop candles that are not finished yet or end after the simulation end time."""
    cutoff = now_ms if end_ms is None else min(now_ms, end_ms)
    return [candle for candle in candles if int(candle["close_time"]) <= cutoff]


def ledger_key(paper_label: str, trade: dict[str, Any]) -> str:
    """One entry is one trade: the key ignores the exit time.

    A recalculation may move the exit; it must never create a second row for
    the same entry. Only one position per pair is open at a time.
    """
    return f"{paper_label}|{trade['pair']}|{trade['opened_at']}"


def ledger_rows(paper_label: str, trades: list[dict[str, Any]], run_id: str | None = None,
                recorded_at: str | None = None) -> list[dict[str, Any]]:
    """Closed trades only; open positions are never written."""
    stamp = recorded_at or datetime.now(UTC).isoformat()
    rows = []
    for trade in trades:
        # A forced end-of-test exit is not a real close: the position is still open.
        if trade.get("status") != "closed" or trade.get("exit_reason") == "end_of_test":
            continue
        raw = trade.get("raw") or {}
        profile = raw.get("cost_components") or {}
        rows.append({
            "id": ledger_key(paper_label, trade),
            "paper_label": paper_label,
            "run_id": run_id,
            "pair": trade["pair"],
            "opened_at": trade["opened_at"],
            "closed_at": trade["closed_at"],
            "entry_rate": trade["entry_rate"],
            "exit_rate": trade["exit_rate"],
            "stake_amount": trade["stake_amount"],
            "profit_usdt": trade["profit_usdt"],
            "exit_reason": trade["exit_reason"],
            "cost_book_ts": profile.get("book_ts"),
            "cost_snapshot": profile,
            "raw": raw,
            "recorded_at": stamp,
        })
    return rows


def new_rows(rows: list[dict[str, Any]], existing_ids: set[str]) -> list[dict[str, Any]]:
    """Rows not yet in the ledger. Existing ids are left untouched."""
    return [row for row in rows if row["id"] not in existing_ids]


def drifted_ids(rows: list[dict[str, Any]], recorded_profit: dict[str, float], tolerance: float = 1e-6) -> list[str]:
    """Ids already recorded whose recalculated PnL now differs. Reported, never applied."""
    return [row["id"] for row in rows
            if row["id"] in recorded_profit and abs(float(row["profit_usdt"]) - float(recorded_profit[row["id"]])) > tolerance]


def overlapping_ids(rows: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[str]:
    """New rows overlapping a frozen trade or an earlier accepted row of the same pair.

    One position per pair is open at a time, so an overlap means the recalculated
    history diverged from the frozen one; recording it would double-count.
    """
    def ms(value: Any) -> float:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()

    frozen = [(row["pair"], ms(row["opened_at"]), ms(row["closed_at"])) for row in existing
              if row.get("pair") and row.get("opened_at") and row.get("closed_at")]
    conflicts = []
    accepted_ids = set()
    # Also compare against rows accepted earlier in this batch. The earliest
    # entry wins deterministically; an existing frozen row always takes priority.
    for row in sorted(rows, key=lambda item: (ms(item["opened_at"]), ms(item["closed_at"]), item["id"])):
        if row["id"] in accepted_ids:
            continue
        start, end = ms(row["opened_at"]), ms(row["closed_at"])
        if any(pair == row["pair"] and start < closed and end > opened
               for pair, opened, closed in frozen):
            conflicts.append(row["id"])
        else:
            frozen.append((row["pair"], start, end))
            accepted_ids.add(row["id"])
    return conflicts


def ledger_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(row["profit_usdt"]) for row in rows]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "pnl_usdt": round(sum(pnl), 6),
        "win_rate_percent": round(len(wins) / len(rows) * 100, 2) if rows else None,
        "profit_factor": round(sum(wins) / -sum(losses), 4) if losses else None,
    }


async def append_closed_trades(paper_label: str, trades: list[dict[str, Any]], run_id: str,
                               read_existing, insert_new) -> dict[str, Any]:
    """Freeze newly closed trades using injected storage calls.

    read_existing(paper_label) -> rows with id, pair, opened_at, closed_at and profit_usdt
    insert_new(rows) -> rows that were really inserted

    Kept free of web framework imports so repeated refreshes can be tested.
    """
    rows = ledger_rows(paper_label, trades, run_id)
    existing = await read_existing(paper_label)
    recorded = {row["id"]: row.get("profit_usdt") for row in existing}
    fresh = new_rows(rows, set(recorded))
    conflicts = set(overlapping_ids(fresh, existing))
    fresh = [row for row in fresh if row["id"] not in conflicts]
    inserted = await insert_new(fresh) if fresh else []
    if len(inserted) != len(fresh):
        # A parallel refresh may have frozen the same rows first. That is fine;
        # only rows that are still missing afterwards are a failed write.
        stored = await read_existing(paper_label)
        recorded.update({row["id"]: row.get("profit_usdt") for row in stored})
        if any(row["id"] not in recorded for row in fresh):
            raise ValueError("paper_ledger_incomplete_write")
    return {"closed_seen": len(rows), "newly_recorded": len(inserted),
            "recalculated_differs_from_recorded": len(drifted_ids(rows, recorded)),
            "overlaps_frozen_trade": len(conflicts)}
